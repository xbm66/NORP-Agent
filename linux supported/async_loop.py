# Vibe Coding Agent - 异步Agent循环 (Async Agent Loop)
# 从同步架构重构为异步架构
# Copyright (c) 2026 xingluosama

import asyncio
import json
import os
import re
import time
import threading
from typing import List, Dict, Optional

from openai import OpenAI
from anthropic import Anthropic as AnthropicClient

from event_queue import EventQueue
from async_executor import AsyncToolExecutor
from lifecycle_manager import LifecycleManager, TaskLifecycle, get_lifecycle_manager
from sandbox_pool import get_sandbox_pool
from file_io_queue import get_file_io_queue
from agent_shared import (
    build_system_prompt,
    build_full_messages,
    build_tools_openai,
    build_tools_anthropic,
    build_responses_tools,
    build_responses_input,
    get_thinking_extra_body,
    get_reasoning_effort,
    convert_openai_messages_to_anthropic,
)

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

        ★ 死锁修复：asyncio.Event 不是线程安全的。
        从外部线程直接 set() 时，Future.set_result 内部走 loop.call_soon
        （非 call_soon_threadsafe），不会写入自管道唤醒信号——
        若事件循环正阻塞在 selector.select() 上（例如 agent 正在
        _wait_for_user_input() 中 await），回调会滞留在 _ready 队列
        无人处理，导致 wait() 永久挂起（表现为"一直等待回复"）。
        必须通过 call_soon_threadsafe 把 set() 调度到 agent 事件循环线程，
        借助自管道唤醒机制立即生效。
        """
        if self._agent_loop and not self._agent_loop.is_closed():
            # 调度到事件循环线程执行 set()，唤醒阻塞中的协程
            self._agent_loop.call_soon_threadsafe(self._stop_event.set)
            self._agent_loop.call_soon_threadsafe(self._user_reply_event.set)
        else:
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
        # Plugin cleanup (fire shutdown hooks + reap zombie threads)
        if self.plugin_manager:
            try:
                self.plugin_manager.fire_agent_shutdown()
            except Exception:
                pass
            try:
                self.plugin_manager.shutdown()
            except Exception:
                pass

    def provide_user_input(self, text: str):
        """提供用户输入（线程安全，可从任意线程调用）。

        ★ 死锁修复：不能在外部线程直接调用 asyncio.Event.set()。
        事件循环阻塞在 selector 上时收不到唤醒信号，_wait_for_user_input()
        会挂起至 30 分钟硬超时。必须通过 call_soon_threadsafe 调度到
        agent 事件循环线程执行（先写值、再 set，保证读取到的必是新值）。
        """
        if self._agent_loop and not self._agent_loop.is_closed():
            self._agent_loop.call_soon_threadsafe(
                self._set_user_reply, text
            )
        else:
            self._user_reply_value = text
            self._user_reply_event.set()

    def _set_user_reply(self, text: str):
        """在 agent 事件循环线程内设置用户回复（仅由 call_soon_threadsafe 调用）。

        先写 _user_reply_value 再 set 事件，确保 _wait_for_user_input()
        被唤醒后读取到的必然是最新的用户输入。
        """
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
                # ask_user 也是工具调用：必须返回 role=tool 消息（带 tool_call_id），
                # 否则 Responses API 报 "No tool output found for tool call"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": reply
                })
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
        """构建完整的消息列表（委托给共享模块）。"""
        return build_full_messages(
            user_message, self.project_root, self.enable_web_search,
            history=history, memory_content=memory_content
        )

    def _build_system_prompt(self) -> str:
        """构建系统提示词（委托给共享模块）。"""
        return build_system_prompt(self.project_root, self.enable_web_search)

    # ═══════════════════════════════════════════════════════════════
    #  工具构建
    # ═══════════════════════════════════════════════════════════════

    def _build_tools_openai(self) -> list:
        """构建 OpenAI 格式工具列表（委托给共享模块）。"""
        return build_tools_openai(self.plugin_manager, self.enable_web_search)

    def _build_tools_anthropic(self) -> list:
        """构建 Anthropic 格式工具列表（委托给共享模块）。"""
        return build_tools_anthropic(self.plugin_manager, self.enable_web_search)

    def _build_responses_tools(self) -> list:
        """构建 Responses API 工具列表（委托给共享模块）。"""
        return build_responses_tools(self.plugin_manager, self.enable_web_search)

    def _build_responses_input(self, messages: list) -> list:
        """将 OpenAI 格式 messages 转换为 Responses API 的 input items（委托给共享模块）。"""
        return build_responses_input(messages)

    def _get_thinking_extra_body(self) -> dict:
        """返回 thinking extra_body 配置（委托给共享模块）。"""
        return get_thinking_extra_body(self.think_level)

    def _get_reasoning_effort(self) -> Optional[str]:
        """返回 reasoning_effort 值（委托给共享模块）。"""
        return get_reasoning_effort(self.think_level)

    def _convert_openai_messages_to_anthropic(self, messages: list) -> list:
        """将 OpenAI 格式消息转换为 Anthropic 格式（委托给共享模块）。"""
        return convert_openai_messages_to_anthropic(messages)
