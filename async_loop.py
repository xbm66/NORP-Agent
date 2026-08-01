# Vibe Coding Agent - 异步Agent循环 (Async Agent Loop)
# 从同步架构重构为异步架构
# Copyright (c) 2026 xingluosama

import asyncio
import json
import os
import re
import time
import threading
from datetime import datetime
from typing import List, Dict, Optional

from openai import OpenAI
from anthropic import Anthropic as AnthropicClient

from event_queue import EventQueue
from async_executor import AsyncToolExecutor
from tools import BUILTIN_TOOLS
from lifecycle_manager import LifecycleManager, TaskLifecycle, get_lifecycle_manager
from sandbox_pool import get_sandbox_pool
from file_io_queue import get_file_io_queue

CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


class AsyncAgentLoop:
    """异步 Agent 循环。

    关键改动（vs 同步版 AgentLoop）：
    1. run() 改为 async，内部所有 I/O 操作异步化
    2. 工具执行通过 AsyncToolExecutor（集成沙箱池/文件IO队列等）
    3. 生命周期绑定：任务启动/停止通过 LifecycleManager 管理进程组
    4. 停止机制：使用 asyncio.Event 替代 threading.Event
    5. API 调用在线程池中执行（OpenAI SDK 是同步的），避免阻塞事件循环
    """

    def __init__(
        self,
        api_key: str,
        project_root: str,
        event_queue: EventQueue,
        app_dir: str = "",
        model: str = "deepseek-v4-pro",
        base_url: str = "https://api.deepseek.com",
        max_steps: int = 128,
        enable_web_search: bool = False,
        confirm_write_delete: bool = True,
        temperature: float = 1.0,
        think_level: str = "高",
        max_tokens: int = 32767,
        task_timeout: int = 0,
        plugin_manager=None,
        use_responses_api: bool = True,
    ):
        self.api_key = api_key
        self.use_responses_api = use_responses_api
        self.base_url = base_url
        self.project_root = project_root
        self.app_dir = app_dir
        self.model = model
        self.max_steps = max_steps
        self.enable_web_search = enable_web_search
        self.confirm_write_delete = confirm_write_delete
        self.temperature = temperature
        self.think_level = think_level
        self.max_tokens = max_tokens
        self.task_timeout = task_timeout

        # 异步事件
        self._stop_event = asyncio.Event()
        self._user_reply_event = asyncio.Event()
        self._user_reply_value = ""

        # Token 统计
        self._last_usage = None
        self._total_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_call_tokens": 0,
        }
        self._step_count = 0

        # 对话历史
        self._conversation_history = []
        self._messages = []
        self._memory_content = ""

        # 计时
        self._task_start_time = 0.0
        self._pause_start_time = 0.0
        self._total_pause_duration = 0.0

        # 日志路径
        if app_dir:
            self.tool_log_path = os.path.join(app_dir, "tool_calls.jsonl")
        else:
            self.tool_log_path = ""

        # 生命周期管理器
        self.lifecycle_manager = get_lifecycle_manager()
        self._task_lifecycle: Optional[TaskLifecycle] = None

        # 事件循环引用（线程安全停止用）
        self._agent_loop: Optional[asyncio.AbstractEventLoop] = None

        # DeepSeek 官方端点检测
        self._is_deepseek_official = (
            base_url.rstrip('/') == "https://api.deepseek.com"
        )

        # Responses API
        self._use_responses_api = (
            use_responses_api
            and self._is_deepseek_official
            and "flash" in model
        )

        # Anthropic 兼容搜索
        self._use_anthropic_search = (
            enable_web_search
            and base_url in ("https://api.deepseek.com", "https://api.deepseek.com/")
            and not self._use_responses_api
        )

        # OpenAI Client（在线程池中调用）
        self.client = OpenAI(api_key=api_key, base_url=base_url)

        if self._use_anthropic_search:
            self.anthropic_client = AnthropicClient(
                api_key=api_key,
                base_url="https://api.deepseek.com/anthropic"
            )
        else:
            self.anthropic_client = None

        # 异步工具执行器
        self.executor = AsyncToolExecutor(
            project_root=project_root,
            app_dir=app_dir,
            task_id=f"task_{id(self)}",
        )

        # 事件队列
        self.event_queue = event_queue

        # 插件
        self.plugin_manager = plugin_manager

    # ═══════════════════════════════════════════════════════════════
    #  停止 / 用户交互
    # ═══════════════════════════════════════════════════════════════

    def stop(self):
        """停止任务（同步接口，供 API 层调用）。

        可从任意线程调用（pywebview JS 桥接线程）。
        使用 call_soon_threadsafe 将清理工作调度到 agent 的事件循环上，
        避免在无事件循环的线程中调用 asyncio.ensure_future 导致 RuntimeError。
        """
        self._stop_event.set()
        self._user_reply_event.set()
        # 生命周期：杀进程组
        if self._task_lifecycle:
            self.lifecycle_manager.stop_task(
                self._task_lifecycle.task_id, reason="user_stop"
            )
        # 线程安全：将清理调度到 agent 专属事件循环
        if self._agent_loop and not self._agent_loop.is_closed():
            self._agent_loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self.executor.cleanup())
            )

    def provide_user_input(self, text: str):
        """提供用户输入。"""
        self._user_reply_value = text
        self._user_reply_event.set()

    async def _wait_for_user_input(self) -> str:
        """异步等待用户输入。

        防御措施：
        1. 将任务状态切换为 WAITING_USER，防止僵尸扫描器误杀
        2. 暂停超时计时器，用户思考时间不计入任务超时
        3. try/except 包裹所有生命周期操作，防止静默失败
        4. 30 分钟硬超时兜底：即使用户不响应也不会永久挂起
        """
        self._pause_timer()
        self._user_reply_event.clear()

        # ── 生命周期：标记为等待用户，暂停超时 ──
        if self._task_lifecycle:
            try:
                self.lifecycle_manager.set_waiting_user(self._task_lifecycle.task_id)
                self.lifecycle_manager.pause_timeout(self._task_lifecycle.task_id)
            except Exception:
                pass  # 防御：即使状态更新失败也不影响主流程

        try:
            # 30分钟硬超时：防止前端崩溃导致永久挂起
            await asyncio.wait_for(
                self._user_reply_event.wait(),
                timeout=1800.0  # 30 minutes
            )
        except asyncio.TimeoutError:
            # 用户交互超时：视为停止任务
            self._stop_event.set()
            if self._task_lifecycle:
                try:
                    self.lifecycle_manager.timeout_task(self._task_lifecycle.task_id)
                except Exception:
                    pass
            self.event_queue.put("E:User interaction timeout (30 min) — task aborted")
            return ""

        # ── 生命周期：恢复运行状态，恢复超时 ──
        if self._task_lifecycle:
            try:
                self.lifecycle_manager.clear_waiting_user(self._task_lifecycle.task_id)
                self.lifecycle_manager.resume_timeout(self._task_lifecycle.task_id)
            except Exception:
                pass

        self._resume_timer()

        if self._stop_event.is_set():
            return ""
        return self._user_reply_value

    async def _confirm_write_delete(self, tool_name: str, tool_args: dict) -> bool:
        """弹出确认对话框。"""
        confirm_data = json.dumps({
            "tool": tool_name,
            "path": tool_args.get("path", "")
        }, ensure_ascii=False)
        self.event_queue.put(f"WC:{confirm_data}")
        reply = await self._wait_for_user_input()
        if self._stop_event.is_set():
            return False
        return reply.strip() == "__confirm__"

    # ═══════════════════════════════════════════════════════════════
    #  计时
    # ═══════════════════════════════════════════════════════════════

    def _get_elapsed(self) -> float:
        if self._pause_start_time > 0:
            current_pause = time.time() - self._pause_start_time
        else:
            current_pause = 0.0
        return time.time() - self._task_start_time - self._total_pause_duration - current_pause

    def _check_timeout(self) -> bool:
        if self.task_timeout <= 0:
            return False
        return self._get_elapsed() > self.task_timeout

    def _pause_timer(self):
        if self.task_timeout > 0:
            self._pause_start_time = time.time()

    def _resume_timer(self):
        if self.task_timeout > 0 and self._pause_start_time > 0:
            self._total_pause_duration += time.time() - self._pause_start_time
            self._pause_start_time = 0.0

    # ═══════════════════════════════════════════════════════════════
    #  Token 统计
    # ═══════════════════════════════════════════════════════════════

    def get_last_usage(self) -> dict:
        if self._last_usage:
            return self._last_usage.copy()
        return {}

    def get_total_usage(self) -> dict:
        return self._total_usage.copy()

    def get_conversation_history(self) -> list:
        return self._conversation_history.copy()

    def _update_usage(self, input_tokens: int, output_tokens: int):
        self._last_usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens
        }
        self._total_usage["input_tokens"] += input_tokens
        self._total_usage["output_tokens"] += output_tokens
        self._send_usage_event()

    def _add_tool_tokens(self, tool_name: str, tool_result: str):
        est = estimate_tokens(tool_result)
        self._total_usage["tool_call_tokens"] += est
        self._send_usage_event()

    def _send_usage_event(self):
        usage_event = json.dumps({
            "input_tokens": self._total_usage["input_tokens"],
            "output_tokens": self._total_usage["output_tokens"],
            "tool_call_tokens": self._total_usage["tool_call_tokens"]
        }, ensure_ascii=False)
        self.event_queue.put(f"U:{usage_event}")

    # ═══════════════════════════════════════════════════════════════
    #  主循环
    # ═══════════════════════════════════════════════════════════════

    async def run(self, user_message: str, history: Optional[List[Dict]] = None,
                  memory_content: str = "") -> str:
        """异步执行 Agent 主循环。"""
        self._step_count = 0
        self._last_usage = None
        self._total_usage = {"input_tokens": 0, "output_tokens": 0, "tool_call_tokens": 0}
        self._messages = []
        self._memory_content = memory_content

        self._task_start_time = time.time()
        self._total_pause_duration = 0.0
        self._pause_start_time = 0.0
        self._stop_event.clear()

        # 记录事件循环引用，供 stop() 线程安全调度
        self._agent_loop = asyncio.get_running_loop()

        # 生命周期：创建任务
        self._task_lifecycle = self.lifecycle_manager.create_task(
            task_id=f"agent_{id(self)}_{int(time.time())}",
            timeout=self.task_timeout,
        )
        self.lifecycle_manager.start_task(self._task_lifecycle.task_id)

        try:
            if self._use_responses_api:
                result = await self._run_responses(user_message, history)
            elif self._use_anthropic_search:
                result = await self._run_anthropic(user_message, history)
            else:
                result = await self._run_openai(user_message, history)
        except asyncio.CancelledError:
            result = "stopped"
        except Exception as e:
            result = f"__ERROR__:{str(e)}"
        finally:
            # 生命周期：标记任务完成
            if self._task_lifecycle:
                self.lifecycle_manager.stop_task(
                    self._task_lifecycle.task_id, reason="completed"
                )

        # 构建对话历史
        conv = []
        for m in self._messages[2:]:
            role = m.get("role", "")
            if role == "assistant":
                msg = {"role": "assistant", "content": m.get("content", "")}
                if m.get("reasoning_content"):
                    msg["reasoning_content"] = m["reasoning_content"]
                if m.get("tool_calls"):
                    msg["tool_calls"] = m["tool_calls"]
                if m.get("web_search_calls"):
                    msg["web_search_calls"] = m["web_search_calls"]
                conv.append(msg)
            elif role == "tool":
                conv.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": m.get("content", "")
                })
        self._conversation_history = conv

        return result

    # ═══════════════════════════════════════════════════════════════
    #  API 调用（在线程池中执行同步 OpenAI SDK）
    # ═══════════════════════════════════════════════════════════════

    async def _run_openai(self, user_message: str,
                          history: Optional[List[Dict]] = None) -> str:
        """异步版 OpenAI Chat Completions 路径。"""
        self.event_queue.reset()

        messages = self._build_full_messages(user_message, history,
                                             memory_content=self._memory_content)
        self._messages = messages

        active_tools = self._build_tools_openai()
        thinking_extra_body = self._get_thinking_extra_body()
        reasoning_effort = self._get_reasoning_effort()

        for step in range(self.max_steps):
            if self._stop_event.is_set():
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            if self._check_timeout():
                elapsed = int(self._get_elapsed())
                self.event_queue.put(f"E:Task timeout after {elapsed}s (limit: {self.task_timeout}s)")
                self.event_queue.signal_finish()
                return "timeout"

            self._step_count = step + 1

            # 在线程池中调用同步 API
            result = await self._call_openai_stream(
                messages=messages,
                tools=active_tools,
                thinking_extra_body=thinking_extra_body,
                reasoning_effort=reasoning_effort,
            )

            if result is None:
                return "stopped"

            full_reasoning = result["reasoning"]
            full_content = result["content"]
            tool_calls_list = result["tool_calls"]
            stream_usage = result.get("usage")

            if stream_usage:
                self._update_usage(stream_usage["input_tokens"], stream_usage["output_tokens"])
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in messages) // 4
                estimated_output = len(full_content) // 4
                self._update_usage(estimated_input, estimated_output)

            assistant_msg = {"role": "assistant", "content": full_content}
            if full_reasoning:
                assistant_msg["reasoning_content"] = full_reasoning

            if tool_calls_list:
                assistant_msg["tool_calls"] = tool_calls_list
                for tc in tool_calls_list:
                    cmd_info = json.dumps({
                        "tool": tc["function"]["name"],
                        "args": json.loads(tc["function"]["arguments"])
                    }, ensure_ascii=False)
                    self.event_queue.put(f"C:{cmd_info}")

            messages.append(assistant_msg)

            if not tool_calls_list:
                if full_content:
                    self.event_queue.put(f"D:{full_content}")
                self.event_queue.signal_finish()
                return full_content

            status = await self._process_tool_calls_async(messages, tool_calls_list, step)
            if status == "stopped":
                return "stopped"

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"

    async def _call_openai_stream(self, messages: list, tools: list,
                                  thinking_extra_body: dict,
                                  reasoning_effort: Optional[str]) -> Optional[dict]:
        """在线程池中调用 OpenAI 流式 API。"""
        api_params = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": True,
            "extra_body": thinking_extra_body,
            "max_tokens": self.max_tokens,
        }
        if self._is_deepseek_official:
            api_params["stream_options"] = {"include_usage": True}
        if reasoning_effort is not None:
            api_params["reasoning_effort"] = reasoning_effort
        if self.think_level == "关":
            api_params["temperature"] = self.temperature

        # 在线程池中运行同步流式调用
        loop = asyncio.get_running_loop()

        def _sync_stream():
            reasoning_parts = []
            content_parts = []
            tool_calls_accum = {}
            stream_usage = None
            _output_started = False

            try:
                stream = self.client.chat.completions.create(**api_params)
                for chunk in stream:
                    if self._stop_event.is_set():
                        break

                    if hasattr(chunk, 'usage') and chunk.usage:
                        stream_usage = {
                            "input_tokens": chunk.usage.prompt_tokens or 0,
                            "output_tokens": chunk.usage.completion_tokens or 0
                        }

                    delta = chunk.choices[0].delta

                    if hasattr(delta, 'tool_calls') and delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_accum:
                                tool_calls_accum[idx] = {
                                    "id": tc.id or "", "name": "", "arguments": ""
                                }
                            if tc.id:
                                tool_calls_accum[idx]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls_accum[idx]["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls_accum[idx]["arguments"] += tc.function.arguments

                    if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                        reasoning_parts.append(delta.reasoning_content)
                        # 在线程安全的方式下发事件
                        loop.call_soon_threadsafe(
                            self.event_queue.put, f"T:{delta.reasoning_content}"
                        )
                    if hasattr(delta, 'content') and delta.content:
                        if not _output_started:
                            _output_started = True
                            loop.call_soon_threadsafe(self.event_queue.put, "F:")
                        content_parts.append(delta.content)
                        loop.call_soon_threadsafe(
                            self.event_queue.put, f"R:{delta.content}"
                        )
            except Exception as e:
                loop.call_soon_threadsafe(
                    self.event_queue.put, f"E:API call failed: {str(e)}"
                )

            tool_calls_list = []
            for idx in sorted(tool_calls_accum.keys()):
                tc = tool_calls_accum[idx]
                if tc["id"] and tc["name"]:
                    tool_calls_list.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"]
                        }
                    })

            return {
                "reasoning": "".join(reasoning_parts),
                "content": "".join(content_parts),
                "tool_calls": tool_calls_list,
                "usage": stream_usage,
            }

        return await loop.run_in_executor(None, _sync_stream)

    # ═══════════════════════════════════════════════════════════════
    #  工具调用处理（异步）
    # ═══════════════════════════════════════════════════════════════

    async def _process_tool_calls_async(self, messages: list,
                                        tool_calls_list: list,
                                        step: int) -> Optional[str]:
        """异步处理工具调用。"""
        for tc in tool_calls_list:
            tool_name = tc["function"]["name"]
            tool_args = json.loads(tc["function"]["arguments"])

            if tool_name == "ask_user":
                question = tool_args.get("question", "")
                self.event_queue.put(f"Q:{json.dumps(question, ensure_ascii=False)}")
                reply = await self._wait_for_user_input()
                if self._stop_event.is_set():
                    self.event_queue.put("E:Task stopped by user")
                    self.event_queue.signal_finish()
                    return "stopped"
                messages.append({"role": "user", "content": reply})
                self.executor.log_tool_call(step + 1, tool_name, tool_args,
                                            f"user replied: {reply[:200]}")
                self._add_tool_tokens(tool_name, reply)
            else:
                if (tool_name in ("write_file", "delete_file", "replace_in_file")
                        and self.confirm_write_delete):
                    if not await self._confirm_write_delete(tool_name, tool_args):
                        if self._stop_event.is_set():
                            self.event_queue.put("E:Task stopped by user")
                            self.event_queue.signal_finish()
                            return "stopped"
                        cancel_msg = "User cancelled the operation."
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": cancel_msg
                        })
                        self.executor.log_tool_call(step + 1, tool_name, tool_args, cancel_msg)
                        continue

                # 异步执行工具
                result = await self.executor.execute(tool_name, tool_args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result
                })
                self.executor.log_tool_call(step + 1, tool_name, tool_args, result)
                self._add_tool_tokens(tool_name, result)

        return None

    # ═══════════════════════════════════════════════════════════════
    #  Responses API 路径（异步）
    # ═══════════════════════════════════════════════════════════════

    async def _run_responses(self, user_message: str,
                             history: Optional[List[Dict]] = None) -> str:
        """异步 Responses API 路径。"""
        self.event_queue.reset()

        messages = self._build_full_messages(user_message, None,
                                             memory_content=self._memory_content)
        self._messages = messages

        tools = self._build_responses_tools()
        reasoning_effort = self._get_reasoning_effort()

        for step in range(self.max_steps):
            if self._stop_event.is_set():
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            if self._check_timeout():
                elapsed = int(self._get_elapsed())
                self.event_queue.put(f"E:Task timeout after {elapsed}s (limit: {self.task_timeout}s)")
                self.event_queue.signal_finish()
                return "timeout"

            self._step_count = step + 1

            input_items = self._build_responses_input(messages)

            api_params = {
                "model": self.model,
                "input": input_items,
                "tools": tools,
                "stream": True,
                "max_output_tokens": self.max_tokens,
            }
            if self.think_level == "关":
                api_params["temperature"] = self.temperature
            elif reasoning_effort is not None:
                api_params["reasoning"] = {"effort": reasoning_effort}

            result = await self._call_responses_stream(api_params)

            if result is None:
                return "stopped"

            full_reasoning = result["reasoning"]
            full_content = result["content"]
            tool_calls_list = result["tool_calls"]
            web_search_calls = result.get("web_search_calls", [])
            stream_usage = result.get("usage")

            if stream_usage:
                self._update_usage(stream_usage["input_tokens"], stream_usage["output_tokens"])
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in messages) // 4
                estimated_output = len(full_content) // 4
                self._update_usage(estimated_input, estimated_output)

            assistant_msg = {"role": "assistant", "content": full_content}
            if full_reasoning:
                assistant_msg["reasoning_content"] = full_reasoning

            if web_search_calls:
                for wc in web_search_calls:
                    query = getattr(wc, "query", "") or ""
                    cmd_info = json.dumps({
                        "tool": "web_search (native Responses API)",
                        "args": {"query": query}
                    }, ensure_ascii=False)
                    self.event_queue.put(f"C:{cmd_info}")
                assistant_msg["web_search_calls"] = [
                    {
                        "id": getattr(wc, "id", "") or "",
                        "status": getattr(wc, "status", "completed") or "completed",
                        "query": getattr(wc, "query", "") or ""
                    }
                    for wc in web_search_calls
                ]

            if tool_calls_list:
                assistant_msg["tool_calls"] = tool_calls_list
                for tc in tool_calls_list:
                    cmd_info = json.dumps({
                        "tool": tc["function"]["name"],
                        "args": json.loads(tc["function"]["arguments"])
                    }, ensure_ascii=False)
                    self.event_queue.put(f"C:{cmd_info}")

            messages.append(assistant_msg)

            if not tool_calls_list:
                if full_content:
                    self.event_queue.put(f"D:{full_content}")
                self.event_queue.signal_finish()
                return full_content

            status = await self._process_tool_calls_async(messages, tool_calls_list, step)
            if status == "stopped":
                return "stopped"

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"

    async def _call_responses_stream(self, api_params: dict) -> Optional[dict]:
        """在线程池中调用 Responses API 流式。"""
        loop = asyncio.get_running_loop()

        def _sync_responses():
            reasoning_parts = []
            content_parts = []
            tool_calls_accum = {}
            web_search_calls = []
            stream_usage = None
            _output_started = False

            try:
                stream = self.client.responses.create(**api_params)
                for event in stream:
                    if self._stop_event.is_set():
                        break

                    et = event.type

                    if et == "response.reasoning_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        reasoning_parts.append(delta)
                        loop.call_soon_threadsafe(self.event_queue.put, f"T:{delta}")
                    elif et == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if not _output_started:
                            _output_started = True
                            loop.call_soon_threadsafe(self.event_queue.put, "F:")
                        content_parts.append(delta)
                        loop.call_soon_threadsafe(self.event_queue.put, f"R:{delta}")
                    elif et == "response.function_call_arguments.delta":
                        item_id = getattr(event, "item_id", "") or ""
                        delta = getattr(event, "delta", "") or ""
                        acc = tool_calls_accum.setdefault(
                            item_id, {"call_id": "", "name": "", "arguments": ""})
                        acc["arguments"] += delta
                    elif et == "response.output_item.done":
                        item = getattr(event, "item", None)
                        if item is not None and getattr(item, "type", "") == "function_call":
                            item_id = getattr(item, "id", "") or ""
                            acc = tool_calls_accum.setdefault(
                                item_id, {"call_id": "", "name": "", "arguments": ""})
                            acc["call_id"] = getattr(item, "call_id", "") or acc["call_id"]
                            acc["name"] = getattr(item, "name", "") or acc["name"]
                            if getattr(item, "arguments", None):
                                acc["arguments"] = item.arguments
                        elif item is not None and getattr(item, "type", "") == "web_search_call":
                            web_search_calls.append(item)
                    elif et == "response.completed":
                        resp = getattr(event, "response", None)
                        if resp is not None and getattr(resp, "usage", None):
                            u = resp.usage
                            stream_usage = {
                                "input_tokens": getattr(u, "input_tokens", 0) or 0,
                                "output_tokens": getattr(u, "output_tokens", 0) or 0
                            }
            except Exception as e:
                loop.call_soon_threadsafe(
                    self.event_queue.put, f"E:Responses API call failed: {str(e)}"
                )

            tool_calls_list = []
            for item_id in sorted(tool_calls_accum.keys()):
                acc = tool_calls_accum[item_id]
                if acc["call_id"] and acc["name"]:
                    tool_calls_list.append({
                        "id": acc["call_id"],
                        "type": "function",
                        "function": {
                            "name": acc["name"],
                            "arguments": acc["arguments"]
                        }
                    })

            return {
                "reasoning": "".join(reasoning_parts),
                "content": "".join(content_parts),
                "tool_calls": tool_calls_list,
                "web_search_calls": web_search_calls,
                "usage": stream_usage,
            }

        return await loop.run_in_executor(None, _sync_responses)

    # ═══════════════════════════════════════════════════════════════
    #  Anthropic 路径（异步）
    # ═══════════════════════════════════════════════════════════════

    async def _run_anthropic(self, user_message: str,
                             history: Optional[List[Dict]] = None) -> str:
        """异步 Anthropic 兼容路径。"""
        self.event_queue.reset()

        system_prompt = self._build_system_prompt()
        if self._memory_content:
            system_prompt += "\n\n" + self._memory_content

        openai_messages = self._build_full_messages(user_message, history,
                                                    memory_content=self._memory_content)
        self._messages = openai_messages

        anthropic_messages = self._convert_openai_messages_to_anthropic(openai_messages)
        all_tools = self._build_tools_anthropic()

        effort_map = {"低": "low", "中": "medium", "高": "max"}
        reasoning_effort = effort_map.get(self.think_level, "max") if self.think_level != "关" else None
        thinking_param = {"type": "enabled"} if reasoning_effort is not None else None
        output_config_param = {"effort": reasoning_effort} if reasoning_effort is not None else None

        for step in range(self.max_steps):
            if self._stop_event.is_set():
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            if self._check_timeout():
                elapsed = int(self._get_elapsed())
                self.event_queue.put(f"E:Task timeout after {elapsed}s (limit: {self.task_timeout}s)")
                self.event_queue.signal_finish()
                return "timeout"

            self._step_count = step + 1

            result = await self._call_anthropic_stream_async(
                messages=anthropic_messages,
                system_prompt=system_prompt,
                tools=all_tools,
                thinking=thinking_param,
                output_config=output_config_param,
            )

            if result is None:
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            full_reasoning = result["reasoning"]
            full_content = result["content"]
            thinking_blocks = result["thinking_blocks"]
            tool_uses = result["tool_uses"]
            usage = result.get("usage")

            if usage:
                self._update_usage(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in anthropic_messages) // 4
                estimated_output = len(full_content) // 4
                self._update_usage(estimated_input, estimated_output)

            if not tool_uses:
                if full_content:
                    self.event_queue.put(f"D:{full_content}")
                self.event_queue.signal_finish()
                return full_content

            assistant_content = []
            for tb in thinking_blocks:
                assistant_content.append(tb)
            if full_content:
                assistant_content.append({"type": "text", "text": full_content})
            for tu in tool_uses:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tu["id"],
                    "name": tu["name"],
                    "input": tu["input"]
                })
            anthropic_messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for tu in tool_uses:
                tool_name = tu["name"]
                tool_input = tu["input"]
                tool_id = tu["id"]

                cmd_info = json.dumps({
                    "tool": tool_name,
                    "args": tool_input
                }, ensure_ascii=False)
                self.event_queue.put(f"C:{cmd_info}")

                if tool_name == "ask_user":
                    question = tool_input.get("question", "")
                    self.event_queue.put(f"Q:{json.dumps(question, ensure_ascii=False)}")
                    reply = await self._wait_for_user_input()
                    if self._stop_event.is_set():
                        return "stopped"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": reply
                    })
                    self._add_tool_tokens(tool_name, reply)
                else:
                    if (tool_name in ("write_file", "delete_file", "replace_in_file")
                            and self.confirm_write_delete):
                        if not await self._confirm_write_delete(tool_name, tool_input):
                            if self._stop_event.is_set():
                                return "stopped"
                            cancel_msg = "User cancelled the operation."
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": cancel_msg
                            })
                            continue

                    result_text = await self.executor.execute(tool_name, tool_input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result_text
                    })
                    self._add_tool_tokens(tool_name, result_text)

            if tool_results:
                anthropic_messages.append({"role": "user", "content": tool_results})

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"

    async def _call_anthropic_stream_async(self, messages: list, system_prompt: str,
                                           tools: list, thinking=None,
                                           output_config=None) -> Optional[dict]:
        """在线程池中调用 Anthropic 流式 API。"""
        loop = asyncio.get_running_loop()

        def _sync_anthropic():
            reasoning_parts = []
            thinking_blocks = []
            content_parts = []
            tool_uses = []
            usage = None
            _output_started = False

            call_params = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system_prompt.strip(),
                "messages": messages,
                "tools": tools,
            }
            if thinking is not None:
                call_params["thinking"] = thinking
            if output_config is not None:
                call_params["output_config"] = output_config
            if self.think_level == "关":
                call_params["temperature"] = self.temperature

            try:
                with self.anthropic_client.messages.stream(**call_params) as stream:
                    for event in stream:
                        if self._stop_event.is_set():
                            try:
                                stream.close()
                            except Exception:
                                pass
                            return None

                        if event.type == "content_block_start":
                            cb = event.content_block
                            if cb.type == "thinking":
                                pass  # tracked via content_block_stop
                        elif event.type == "content_block_delta":
                            delta = event.delta
                            if hasattr(delta, 'thinking') and delta.thinking:
                                reasoning_parts.append(delta.thinking)
                                loop.call_soon_threadsafe(
                                    self.event_queue.put, f"T:{delta.thinking}"
                                )
                            if hasattr(delta, 'text') and delta.text:
                                if not _output_started:
                                    _output_started = True
                                    loop.call_soon_threadsafe(self.event_queue.put, "F:")
                                content_parts.append(delta.text)
                                loop.call_soon_threadsafe(
                                    self.event_queue.put, f"R:{delta.text}"
                                )
                        elif event.type == "content_block_stop":
                            if hasattr(event, 'content_block') and event.content_block:
                                cb = event.content_block
                                if cb.type == "thinking":
                                    thinking_blocks.append({
                                        "type": "thinking",
                                        "thinking": getattr(cb, 'thinking', ''),
                                        "signature": getattr(cb, 'signature', '')
                                    })
                                elif cb.type == "tool_use":
                                    tool_uses.append({
                                        "id": cb.id,
                                        "name": cb.name,
                                        "input": cb.input
                                    })
                        elif event.type == "message_stop":
                            if hasattr(event, 'message') and event.message:
                                msg_usage = getattr(event.message, 'usage', None)
                                if msg_usage:
                                    usage = {
                                        "input_tokens": getattr(msg_usage, 'input_tokens', 0) or 0,
                                        "output_tokens": getattr(msg_usage, 'output_tokens', 0) or 0
                                    }
            except Exception as e:
                loop.call_soon_threadsafe(
                    self.event_queue.put, f"E:Anthropic API call failed: {str(e)}"
                )

            return {
                "reasoning": "".join(reasoning_parts),
                "content": "".join(content_parts),
                "thinking_blocks": thinking_blocks,
                "tool_uses": tool_uses,
                "usage": usage,
            }

        return await loop.run_in_executor(None, _sync_anthropic)

    # ═══════════════════════════════════════════════════════════════
    #  消息构建（复用原版逻辑）
    # ═══════════════════════════════════════════════════════════════

    _TIMESTAMP_RE = re.compile(
        r'^\[SystemInfo\]当前系统时间：\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}:\d{2}\n'
    )

    def _build_full_messages(self, user_message: str, history: Optional[List[Dict]] = None,
                              memory_content: str = "") -> list:
        current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
        system_prompt = self._build_system_prompt()

        full_messages = [{"role": "system", "content": system_prompt}]

        if memory_content:
            full_messages.append({"role": "system", "content": memory_content})

        full_messages.append({
            "role": "system",
            "content": f"[SystemInfo]当前系统时间：{current_time}。"
        })

        if history:
            for m in history:
                role = m.get("role", "")
                if role == "user":
                    content = m.get("content", "")
                    full_messages.append({
                        "role": "user",
                        "content": f"[历史] {content}"
                    })
                elif role == "assistant":
                    msg = {"role": "assistant", "content": m.get("content", "")}
                    if m.get("reasoning_content"):
                        msg["reasoning_content"] = m["reasoning_content"]
                    if m.get("tool_calls"):
                        msg["tool_calls"] = m["tool_calls"]
                    if m.get("web_search_calls"):
                        msg["web_search_calls"] = m["web_search_calls"]
                    full_messages.append(msg)
                elif role == "tool":
                    full_messages.append({
                        "role": "tool",
                        "tool_call_id": m.get("tool_call_id", ""),
                        "content": m.get("content", "")
                    })

        full_messages.append({
            "role": "user",
            "content": f"[SystemInfo]当前系统时间：{current_time}\n{user_message}"
        })

        return full_messages

    def _build_system_prompt(self) -> str:
        now = datetime.now()
        date_str = now.strftime("%Y年%m月%d日 %H:%M:%S")
        weekday_str = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
        prompt = (
            "[身份]\n"
            "你是 Vibe Coding 自主编程智能体，采用 ReAct 架构。\n"
            "唯一目标：将用户自然语言指令转化为精确的代码操作，主动解决问题，而非被动问答。\n\n"
            f"[环境]\n"
            f"当前系统时间：{date_str}（周{weekday_str}）\n"
            f"工作区根目录：{self.project_root}\n\n"
            "[工具使用原则]\n"
            "- 先读后写：覆盖或修改文件前，必须先用 read_file 读取现有内容\n"
            "- 主动探索：不确定项目结构时，先用 list_dir 了解目录布局\n"
            "- 批量操作：多个无依赖的工具调用应在一次响应中并行发起\n"
            "- 最小权限：只创建必要的文件，只安装声明的依赖\n"
            "- 精准修改：优先使用 replace_in_file 进行针对性编辑，避免用 write_file 重写整个文件，以节省 token\n\n"
            "[安全约束]\n"
            "- 删除文件或目录前，必须调用 ask_user 获得用户确认\n"
            "- 执行 shell 命令时禁止 sudo、rm -rf /、mkfs 等危险操作\n"
            "- 所有文件路径限定在工作区根目录内，不得包含 .. 或绝对系统路径\n\n"
            "[任务完成]\n"
            "任务完成时调用 task_done，传入总结和涉及的主要代码路径，系统自动写入历史记录。\n\n"
            "[可用工具]\n"
            "read_file(path, start_line?, end_line?): 读取文件内容。可指定行范围只读取需要的代码片段，节省 token。\n"
            "write_file(path, content): 创建或覆盖文件。覆盖前建议先 read_file 备份原内容。\n"
            "replace_in_file(path, old_str, new_str): 替换文件中的指定文本片段。old_str 必须精确匹配文件中唯一一处。若匹配多处则报错，需提供更多上下文以唯一确定。用于针对性修改，避免重写整个文件。\n"
            "list_dir(path?): 列出目录内容，用于了解项目结构。\n"
            "search_in_files(pattern, path?): 在文件中搜索文本模式。\n"
            "delete_file(path): 删除文件或目录。不可逆操作，执行前应请求用户确认。\n"
            "exec_cmd(command, timeout?): 执行 shell 命令。禁止 sudo、rm -rf / 等危险操作。对不确定的命令先加 --dry-run 预览。\n"
            "init_project(type, name): 脚手架初始化新项目，自动创建目录结构。\n"
            "install_dependency(package, manager?): 安装项目依赖。\n"
            "git_commit(message): 提交所有变更到 Git 仓库。\n"
            "ask_user(question): 向用户提问或请求确认。当需要用户做出选择、澄清需求、或确认危险操作时调用。\n"
            "task_done(summary, code_path?): 标记任务完成。完成后会自动将任务摘要和代码路径记录到 .agent_history.json。\n"
            "open_file(path): 用系统默认程序打开文件。用户说「打开某个文件」时调用此工具。支持所有常见文件类型（图片、文档、网页等）。\n"
        )
        if self.enable_web_search:
            prompt += "web_search(query): 联网搜索实时信息，适用于需要最新数据的场景。\n"
        prompt += (
            "\n[输出规范]\n"
            "- 调用工具时系统自动处理格式，你只需正常推理和决策\n"
            "- 任务完成后输出简洁的自然语言总结，无需列出每一步细节\n"
            "- 遇到阻塞性问题时主动调用 ask_user，不要猜测用户意图\n"
        )
        prompt += (
            "\n[历史消息处理]\n"
            "对话中带有 `[历史]` 前缀的消息是之前的用户输入，这些消息已经发生过，请参考它们来理解上下文。\n"
            "不要对 `[历史]` 消息做出新的响应或执行新的任务——它们只是背景信息。\n"
            "只有最后一条不带 `[历史]` 前缀的用户消息才是当前需要处理的任务。\n"
            "当用户询问关于自身信息（如名字、偏好等）时，应优先从 `[历史]` 消息中检索相关事实。\n"
        )
        return prompt

    # ═══════════════════════════════════════════════════════════════
    #  工具构建
    # ═══════════════════════════════════════════════════════════════

    def _build_tools_openai(self) -> list:
        tools = list(BUILTIN_TOOLS)
        if self.plugin_manager:
            plugin_tools = self.plugin_manager.get_tools()
            tools.extend(plugin_tools)
        if not self.enable_web_search:
            tools = [t for t in tools if t["function"]["name"] != "web_search"]
        return tools

    def _build_tools_anthropic(self) -> list:
        tools = [{"type": "web_search_20250305", "name": "web_search"}]
        for t in BUILTIN_TOOLS:
            name = t["function"]["name"]
            if name == "web_search":
                continue
            func = t["function"]
            tools.append({
                "name": name,
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}})
            })
        if self.plugin_manager:
            for t in self.plugin_manager.get_tools():
                func = t["function"]
                tools.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}})
                })
        return tools

    def _build_responses_tools(self) -> list:
        cc_tools = self._build_tools_openai()
        tools = []
        for t in cc_tools:
            func = t.get("function", {})
            name = func.get("name", "")
            if self.enable_web_search and name == "web_search":
                continue
            tools.append({
                "type": "function",
                "name": name,
                "description": func.get("description", ""),
                "parameters": func.get("parameters", {"type": "object", "properties": {}}),
            })
        if self.enable_web_search:
            tools.append({"type": "web_search"})
        return tools

    def _build_responses_input(self, messages: list) -> list:
        items = []
        for m in messages:
            role = m.get("role", "")
            if role == "system":
                items.append({
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": m.get("content", "")}]
                })
            elif role == "user":
                items.append({
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": m.get("content", "")}]
                })
            elif role == "assistant":
                reasoning = m.get("reasoning_content", "")
                if reasoning:
                    items.append({
                        "type": "reasoning",
                        "content": [{"type": "reasoning_text", "text": reasoning}]
                    })
                text = m.get("content", "")
                if text:
                    items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}]
                    })
                for wc in m.get("web_search_calls", []):
                    items.append({
                        "type": "web_search_call",
                        "id": wc.get("id", ""),
                        "status": wc.get("status", "completed"),
                        "query": wc.get("query", "")
                    })
                for tc in m.get("tool_calls", []):
                    items.append({
                        "type": "function_call",
                        "call_id": tc.get("id", ""),
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"]
                    })
            elif role == "tool":
                items.append({
                    "type": "function_call_output",
                    "call_id": m.get("tool_call_id", ""),
                    "output": m.get("content", "")
                })
        return items

    def _get_thinking_extra_body(self) -> dict:
        if self.think_level == "关":
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}}

    def _get_reasoning_effort(self) -> Optional[str]:
        if self.think_level == "关":
            return None
        effort_map = {"低": "low", "中": "medium", "高": "max"}
        return effort_map.get(self.think_level, "max")

    def _convert_openai_messages_to_anthropic(self, messages: list) -> list:
        result = []
        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                continue
            elif role == "user":
                result.append({"role": "user", "content": msg.get("content", "")})
            elif role == "assistant":
                content = msg.get("content", "")
                result.append({"role": "assistant", "content": content})
        return result
