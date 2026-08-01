# Vibe Coding Agent - 核心Agent循环
# Copyright (c) 2026 xingluosama

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
from executor import ToolExecutor
from tools import BUILTIN_TOOLS
from plugin_system.manager import PluginManager

CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    """估算一段文本的 token 数量。"""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


class AgentLoop:

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
        plugin_manager: Optional[PluginManager] = None,
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

       
        self._task_start_time = 0.0
        self._pause_start_time = 0.0
        self._total_pause_duration = 0.0

        
        self._last_usage = None      
        self._total_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_call_tokens": 0     
        }
        self._step_count = 0          

        
        self._conversation_history = []

       
        if app_dir:
            self.tool_log_path = os.path.join(app_dir, "tool_calls.jsonl")
        else:
            self.tool_log_path = ""

        
        self._is_deepseek_official = (
            base_url.rstrip('/') == "https://api.deepseek.com"
        )

        # ── Responses API 原生支持（DeepSeek V4 Flash 正式版）──
        # 设置开关 use_responses_api 默认开启：
        # - 仅官方端点 + flash 模型时启用（Responses API 目前仅支持 V4 Flash）
        # - 非 flash 模型 / 自定义端点自动回退 Chat Completions
        self._use_responses_api = (
            use_responses_api
            and self._is_deepseek_official
            and "flash" in model
        )

        # Anthropic 兼容搜索模式仅在未启用 Responses API 时使用
        self._use_anthropic_search = (
            enable_web_search
            and base_url in ("https://api.deepseek.com", "https://api.deepseek.com/")
            and not self._use_responses_api
        )

        self.client = OpenAI(api_key=api_key, base_url=base_url)

        if self._use_anthropic_search:
            self.anthropic_client = AnthropicClient(
                api_key=api_key,
                base_url="https://api.deepseek.com/anthropic"
            )
        else:
            self.anthropic_client = None

        try:
            from executor import DockerSandbox
            self._sandbox = DockerSandbox(project_root)
            self._sandbox.start()
            self.executor = ToolExecutor(project_root, sandbox=self._sandbox, app_dir=app_dir)
        except Exception:
            self._sandbox = None
            self.executor = ToolExecutor(project_root, app_dir=app_dir)

        self.event_queue = event_queue
        self._stop_event = threading.Event()
        self._user_reply_event = threading.Event()
        self._user_reply_value = ""

        # ── Plugin system ──
        self.plugin_manager = plugin_manager or PluginManager(
            [], app_dir, project_root, config={})
        self.plugin_manager.update_config_snapshot({
            "project_root": project_root,
            "app_dir": app_dir,
            "model": model,
            "base_url": base_url,
            "max_steps": max_steps,
            "enable_web_search": enable_web_search,
            "confirm_write_delete": confirm_write_delete,
            "temperature": temperature,
            "think_level": think_level,
            "max_tokens": max_tokens,
            "task_timeout": task_timeout,
        })
        self.plugin_manager.fire_agent_init()


    def _get_elapsed(self) -> float:
        """获取已用时间（扣除暂停时间）。"""
        if self._pause_start_time > 0:
            current_pause = time.time() - self._pause_start_time
        else:
            current_pause = 0.0
        return time.time() - self._task_start_time - self._total_pause_duration - current_pause

    def _check_timeout(self) -> bool:
        """检查是否超时。返回 True 表示已超时。"""
        if self.task_timeout <= 0:
            return False
        return self._get_elapsed() > self.task_timeout

    def _pause_timer(self):
        """暂停超时计时器（进入等待用户输入状态时调用）。"""
        if self.task_timeout > 0:
            self._pause_start_time = time.time()

    def _resume_timer(self):
        """恢复超时计时器（用户输入完成后调用）。"""
        if self.task_timeout > 0 and self._pause_start_time > 0:
            self._total_pause_duration += time.time() - self._pause_start_time
            self._pause_start_time = 0.0


    def _build_tools_openai(self) -> list:
        """构建 OpenAI 格式工具列表（内置 + 插件）。"""
        tools = list(BUILTIN_TOOLS)  # copy

        if self.plugin_manager:
            plugin_tools = self.plugin_manager.get_tools()
            tools.extend(plugin_tools)

        if not self.enable_web_search:
            tools = [t for t in tools if t["function"]["name"] != "web_search"]

        return tools

    def _build_tools_anthropic(self) -> list:
        """构建 Anthropic 格式工具列表（内置 + 插件）。"""
        tools = []

        tools.append({
            "type": "web_search_20250305",
            "name": "web_search"
        })

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

        # Add plugin tools in Anthropic format
        if self.plugin_manager:
            for t in self.plugin_manager.get_tools():
                func = t["function"]
                tools.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {"type": "object", "properties": {}})
                })

        return tools

    def _build_full_messages(self, user_message: str, history: Optional[List[Dict]] = None,
                              memory_content: str = "") -> list:
        """构建完整的消息列表（JSON 格式），上下文回传模式。

        这是「API 无状态，客户端回传上下文」的关键实现：
        每次 API 调用都必须携带完整上下文（当前会话的消息历史）。

        核心策略：
        1. 系统级时间戳消息（让模型感知当前时间）
        2. ★ 历史 user 消息回传，但添加 [历史] 前缀，以区别于当前用户消息
        3. assistant 消息完整回传（含 reasoning_content、tool_calls）
        4. tool 消息完整回传（工具执行结果）
        5. 当前用户消息注入时间戳前缀（不加 [历史] 标记）
        6. ★ 注入持久化记忆（来自 duo2.py 的记忆系统）
        """
        current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
        system_prompt = self._build_system_prompt()

        full_messages = [{"role": "system", "content": system_prompt}]

        if memory_content:
            full_messages.append({
                "role": "system",
                "content": memory_content
            })

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


    _TIMESTAMP_RE = re.compile(
        r'^\[SystemInfo\]当前系统时间：\d{4}年\d{2}月\d{2}日 \d{2}:\d{2}:\d{2}\n'
    )

    def _strip_timestamp_prefix(self, content: str) -> str:
        """剥离 _build_full_messages 注入的 [SystemInfo] 时间戳前缀。
        确保存储的会话历史是干净的原始内容，避免前缀累积。
        """
        return self._TIMESTAMP_RE.sub('', content, count=1)


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


    def stop(self):
        self._stop_event.set()
        self._user_reply_event.set()
        if self.plugin_manager:
            self.plugin_manager.fire_agent_shutdown()
        if self._sandbox:
            self._sandbox.stop()

    def provide_user_input(self, text: str):
        self._user_reply_value = text
        self._user_reply_event.set()

    def _wait_for_user_input(self) -> str:
        """等待用户输入。期间冻结超时计时器。"""
        self._pause_timer()
        self._user_reply_event.clear()
        self._user_reply_event.wait()
        self._resume_timer()
        if self._stop_event.is_set():
            return ""
        return self._user_reply_value


    def _confirm_write_delete(self, tool_name: str, tool_args: dict) -> bool:
        """弹出确认对话框，返回 True 表示用户确认，False 表示取消/停止。"""
        confirm_data = json.dumps({
            "tool": tool_name,
            "path": tool_args.get("path", "")
        }, ensure_ascii=False)
        self.event_queue.put(f"WC:{confirm_data}")
        reply = self._wait_for_user_input()
        if self._stop_event.is_set():
            return False
        return reply.strip() == "__confirm__"


    def get_last_usage(self) -> dict:
        """返回最近一次 API 调用的 token 用量。"""
        if self._last_usage:
            return self._last_usage.copy()
        return {}

    def get_total_usage(self) -> dict:
        """返回累计 token 用量（含工具调用估算）。"""
        return self._total_usage.copy()

    def get_conversation_history(self) -> list:
        """返回当前会话的对话历史（tools 操作链，不含用户提问）。
        用于多轮对话上下文回传。包含 assistant（含 tool_calls、reasoning_content）
        和 tool 消息，不包含 user 消息。
        """
        return self._conversation_history.copy()

    def _update_usage(self, input_tokens: int, output_tokens: int):
        """更新 API token 用量并发送事件到前端。"""
        self._last_usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens
        }
        self._total_usage["input_tokens"] += input_tokens
        self._total_usage["output_tokens"] += output_tokens
        self._send_usage_event()

    def _add_tool_tokens(self, tool_name: str, tool_result: str):
        """估算工具调用返回结果的 token 消耗并累加。
        这些内容会在下一轮 API 调用中作为 input 消耗 token。
        """
        est = estimate_tokens(tool_result)
        self._total_usage["tool_call_tokens"] += est
        self._send_usage_event()

    def _send_usage_event(self):
        """发送 token 用量事件到前端。"""
        usage_event = json.dumps({
            "input_tokens": self._total_usage["input_tokens"],
            "output_tokens": self._total_usage["output_tokens"],
            "tool_call_tokens": self._total_usage["tool_call_tokens"]
        }, ensure_ascii=False)
        self.event_queue.put(f"U:{usage_event}")
        if self.plugin_manager:
            self.plugin_manager.fire_usage_update(self._total_usage.copy())

    def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool, routing to plugin or built-in executor."""
        # Check if it's a plugin tool first
        if self.plugin_manager:
            plugin_tools = self.plugin_manager.get_tools()
            plugin_names = {t["function"]["name"] for t in plugin_tools}
            if tool_name in plugin_names:
                return self.plugin_manager.execute(tool_name, tool_args)
        return self.executor.execute(tool_name, tool_args)


    def _log_tool_call(self, step: int, tool_name: str, args: dict, result: str):
        """将工具调用记录保存为 JSONL 格式（与 config.json 同目录）。"""
        if not self.tool_log_path:
            return
        try:
            result_summary = result[:500] + "..." if len(result) > 500 else result
            record = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "step": step,
                "tool": tool_name,
                "args": args,
                "result_length": len(result),
                "tokens_estimate": estimate_tokens(result),
                "result_summary": result_summary
            }
            with open(self.tool_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass  


    def _get_thinking_extra_body(self) -> dict:
        """返回 thinking extra_body 配置（仅含 thinking 字段）。
        reasoning_effort 作为顶级参数单独传递。
        
        DeepSeek 官方文档要求：
        - thinking 放在 extra_body 中
        - reasoning_effort 作为顶级参数
        """
        if self.think_level == "关":
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}}

    def _get_reasoning_effort(self) -> Optional[str]:
        """返回 reasoning_effort 值（顶级参数），思考关闭时返回 None。"""
        if self.think_level == "关":
            return None
        effort_map = {"低": "low", "中": "medium", "高": "max"}
        return effort_map.get(self.think_level, "max")


    def run(self, user_message: str, history: Optional[List[Dict]] = None,
            memory_content: str = "") -> str:
        self._step_count = 0
        self._last_usage = None
        self._total_usage = {"input_tokens": 0, "output_tokens": 0, "tool_call_tokens": 0}
        self._messages = []
        self._memory_content = memory_content  

        self._task_start_time = time.time()
        self._total_pause_duration = 0.0
        self._pause_start_time = 0.0

        # ── Hook: task started ──
        if self.plugin_manager:
            self.plugin_manager.fire_task_start(user_message)

        try:
            if self._use_responses_api:
                # DeepSeek V4 Flash 原生 Responses API
                result = self._run_responses(user_message, history)
            elif self._use_anthropic_search:
                result = self._run_anthropic(user_message, history)
            else:
                result = self._run_openai(user_message, history)
        except Exception as e:
            if self.plugin_manager:
                self.plugin_manager.fire_task_error(str(e))
            raise

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

        # ── Hook: task done / stopped / timeout ──
        if self.plugin_manager:
            if result == "stopped":
                self.plugin_manager.fire_task_stopped()
            elif result == "timeout":
                self.plugin_manager.fire_task_timeout(self._get_elapsed())
            elif result == "max_steps":
                pass  # neither done nor error
            else:
                self.plugin_manager.fire_task_done(
                    summary=result[:200] if result else "",
                    final_reply=result or "")

        return result


    def _run_openai(self, user_message: str, history: Optional[List[Dict]] = None) -> str:
        """使用 OpenAI SDK 调用 DeepSeek API。

        关键规则（来自 DeepSeek 官方文档）：
        1. 流式处理时 reasoning_content 和 content 互斥，使用 if-else 处理
        2. 工具调用场景下，每轮子请求都必须回传 reasoning_content
        3. reasoning_effort 作为顶级参数传递，thinking 放在 extra_body 中
        4. 无工具调用的最终轮次，reasoning_content 无需回传（下一轮会被忽略）
        5. API 是无状态的，每次请求必须携带完整上下文（messages）
        """
        self._stop_event.clear()
        self.event_queue.reset()

        memory_content = getattr(self, '_memory_content', '')
        messages = self._build_full_messages(user_message, history, memory_content=memory_content)
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

            # ── Hook: before_step ──
            if self.plugin_manager:
                messages = self.plugin_manager.fire_before_step(
                    self._step_count, messages)

            api_params = {
                "model": self.model,
                "messages": messages,
                "tools": active_tools,
                "stream": True,
                "extra_body": thinking_extra_body,
                "max_tokens": self.max_tokens
            }

            if self._is_deepseek_official:
                api_params["stream_options"] = {"include_usage": True}

            if reasoning_effort is not None:
                api_params["reasoning_effort"] = reasoning_effort

            if self.think_level == "关":
                api_params["temperature"] = self.temperature

            stream = self.client.chat.completions.create(**api_params)

            reasoning_parts = []
            content_parts = []
            tool_calls_accum = {}
            stream_usage = None

            # Throttle counters for streaming hooks (every ~100ms)
            _last_ts_fire = 0.0
            _reasoning_buf = ""
            _content_buf = ""
            _output_started = False  # track when output starts to flush thinking

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
                                "id": tc.id or "",
                                "name": "",
                                "arguments": ""
                            }
                        if tc.id:
                            tool_calls_accum[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            tool_calls_accum[idx]["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            tool_calls_accum[idx]["arguments"] += tc.function.arguments

                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    reasoning_parts.append(delta.reasoning_content)
                    self.event_queue.put(f"T:{delta.reasoning_content}")
                    _reasoning_buf += delta.reasoning_content
                if hasattr(delta, 'content') and delta.content:
                    # First output chunk → tell front-end to finalize thinking block
                    if not _output_started:
                        _output_started = True
                        self.event_queue.put("F:")
                    content_parts.append(delta.content)
                    self.event_queue.put(f"R:{delta.content}")
                    _content_buf += delta.content

                # Throttled streaming hooks
                now = time.time()
                if self.plugin_manager and now - _last_ts_fire > 0.1:
                    if _reasoning_buf:
                        self.plugin_manager.fire_reasoning(_reasoning_buf)
                        _reasoning_buf = ""
                    if _content_buf:
                        self.plugin_manager.fire_content(_content_buf)
                        _content_buf = ""
                    _last_ts_fire = now

            # Flush remaining streaming tokens
            if self.plugin_manager:
                if _reasoning_buf:
                    self.plugin_manager.fire_reasoning(_reasoning_buf)
                if _content_buf:
                    self.plugin_manager.fire_content(_content_buf)

            if self._stop_event.is_set():
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            if stream_usage:
                self._update_usage(
                    stream_usage["input_tokens"],
                    stream_usage["output_tokens"]
                )
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in messages) // 4
                estimated_output = len("".join(content_parts)) // 4
                self._update_usage(estimated_input, estimated_output)

            full_reasoning = "".join(reasoning_parts)
            full_content = "".join(content_parts)

            # ── Hook: after_step ──
            if self.plugin_manager:
                self.plugin_manager.fire_after_step(
                    self._step_count, full_reasoning, full_content,
                    tool_calls_accum)

            assistant_msg = {"role": "assistant", "content": full_content}

            if full_reasoning:
                assistant_msg["reasoning_content"] = full_reasoning

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

            status = self._process_tool_calls_openai(messages, tool_calls_list, step)
            if status == "stopped":
                return "stopped"

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"


    def _process_tool_calls_openai(self, messages: list, tool_calls_list: list,
                                   step: int) -> Optional[str]:
        """执行 OpenAI 格式的 tool_calls 并把结果回传到 messages。

        Chat Completions 与 Responses API 两条路径共用：
        - 插件钩子（before/after_tool_call、user_input_required）
        - ask_user 交互（等待用户输入）
        - 写/删文件确认
        - 工具执行、日志记录、token 估算
        返回 "stopped" 表示用户停止任务，None 表示正常完成。
        """
        for tc in tool_calls_list:
            tool_name = tc["function"]["name"]
            tool_args = json.loads(tc["function"]["arguments"])

            # ── Hook: before_tool_call ──
            if self.plugin_manager:
                modified_args = self.plugin_manager.fire_before_tool_call(
                    tool_name, tool_args)
                if modified_args is None:
                    # Blocked by plugin
                    blocked_msg = "Tool call blocked by plugin."
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": blocked_msg
                    })
                    self._log_tool_call(step + 1, tool_name, tool_args, blocked_msg)
                    continue
                tool_args = modified_args

            if tool_name == "ask_user":
                question = tool_args.get("question", "")
                # ── Hook: user_input_required ──
                if self.plugin_manager:
                    self.plugin_manager.fire_user_input_required(question)
                self.event_queue.put(f"Q:{json.dumps(question, ensure_ascii=False)}")
                reply = self._wait_for_user_input()
                if self._stop_event.is_set():
                    self.event_queue.put("E:Task stopped by user")
                    self.event_queue.signal_finish()
                    return "stopped"
                messages.append({"role": "user", "content": reply})
                self._log_tool_call(step + 1, tool_name, tool_args, f"user replied: {reply[:200]}")
                self._add_tool_tokens(tool_name, reply)
            else:
                if (tool_name in ("write_file", "delete_file", "replace_in_file")
                        and self.confirm_write_delete):
                    if not self._confirm_write_delete(tool_name, tool_args):
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
                        self._log_tool_call(step + 1, tool_name, tool_args, cancel_msg)
                        continue

                result = self._execute_tool(tool_name, tool_args)

                # ── Hook: after_tool_call ──
                if self.plugin_manager:
                    result = self.plugin_manager.fire_after_tool_call(
                        tool_name, tool_args, result)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result
                })
                self._log_tool_call(step + 1, tool_name, tool_args, result)
                self._add_tool_tokens(tool_name, result)

        return None


    def _build_responses_tools(self) -> list:
        """构建 Responses API 工具列表。

        Responses API 工具格式要求 name/description/parameters 在顶层，
        而不是嵌套在 function 字段里（那是 Chat Completions 的格式）。
        web_search 使用服务端原生工具（Responses API 原生能力，客户端无需执行）。
        """
        # 先用 Chat Completions 格式拿到工具列表
        cc_tools = self._build_tools_openai()

        # 转换为 Responses API 格式：把 function 字段展平到顶层
        tools = []
        for t in cc_tools:
            func = t.get("function", {})
            name = func.get("name", "")
            if self.enable_web_search and name == "web_search":
                continue  # web_search 用原生格式代替
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
        """将 OpenAI 格式 messages 转换为 Responses API 的 input items。

        DeepSeek V4 Flash Responses API 输入格式：
        - system/user 消息 → {"type": "message", "role": ..., "content": [{"type": "input_text", ...}]}
        - assistant 推理   → {"type": "reasoning", "content": [{"type": "reasoning_text", ...}]}
        - assistant 回复   → {"type": "message", "role": "assistant", "content": [{"type": "output_text", ...}]}
        - 工具调用         → {"type": "function_call", "call_id", "name", "arguments"}
        - 工具结果         → {"type": "function_call_output", "call_id", "output"}
        - 服务端搜索       → {"type": "web_search_call", "id", "status", "query"}
        """
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
                # 服务端原生 web_search 调用上下文
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


    def _run_responses(self, user_message: str, history: Optional[List[Dict]] = None) -> str:
        """使用 OpenAI SDK 的 Responses API 原生调用 DeepSeek V4 Flash。

        DeepSeek V4 Flash 正式版原生支持 Responses API：
        1. 语义化流式事件：response.reasoning_text.delta / response.output_text.delta
           / response.function_call_arguments.delta / response.output_item.done
           / response.completed（携带 usage）
        2. 推理控制使用顶层 reasoning={"effort": ...} 参数（取代 extra_body.thinking）
        3. web_search 为服务端原生工具（开启联网搜索时自动启用，客户端无需执行）
        4. 无状态 API：每次请求必须携带完整上下文（input items）
        5. 不回传历史：每次请求仅携带当前用户消息和系统提示，不附带会话历史
        """
        self._stop_event.clear()
        self.event_queue.reset()

        memory_content = getattr(self, '_memory_content', '')
        # 不回传历史：history 参数被忽略，确保 Responses API 每次调用都是独立的
        messages = self._build_full_messages(user_message, None, memory_content=memory_content)
        self._messages = messages

        # 工具列表：Responses API 模式下 web_search 使用服务端原生工具
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

            # ── Hook: before_step ──
            if self.plugin_manager:
                messages = self.plugin_manager.fire_before_step(
                    self._step_count, messages)

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

            stream = self.client.responses.create(**api_params)

            reasoning_parts = []
            content_parts = []
            tool_calls_accum = {}   # item_id -> {call_id, name, arguments}
            web_search_calls = []   # 服务端原生 web_search 调用记录
            stream_usage = None

            # Throttle counters for streaming hooks (every ~100ms)
            _last_ts_fire = 0.0
            _reasoning_buf = ""
            _content_buf = ""
            _output_started = False  # track when output starts to flush thinking

            for event in stream:
                if self._stop_event.is_set():
                    break

                et = event.type

                if et == "response.reasoning_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    reasoning_parts.append(delta)
                    self.event_queue.put(f"T:{delta}")
                    _reasoning_buf += delta
                elif et == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    # First output chunk → tell front-end to finalize thinking block
                    if not _output_started:
                        _output_started = True
                        self.event_queue.put("F:")
                    content_parts.append(delta)
                    self.event_queue.put(f"R:{delta}")
                    _content_buf += delta
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

                # Throttled streaming hooks
                now = time.time()
                if self.plugin_manager and now - _last_ts_fire > 0.1:
                    if _reasoning_buf:
                        self.plugin_manager.fire_reasoning(_reasoning_buf)
                        _reasoning_buf = ""
                    if _content_buf:
                        self.plugin_manager.fire_content(_content_buf)
                        _content_buf = ""
                    _last_ts_fire = now

            # Flush remaining streaming tokens
            if self.plugin_manager:
                if _reasoning_buf:
                    self.plugin_manager.fire_reasoning(_reasoning_buf)
                if _content_buf:
                    self.plugin_manager.fire_content(_content_buf)

            if self._stop_event.is_set():
                self.event_queue.put("E:Task stopped by user")
                self.event_queue.signal_finish()
                return "stopped"

            if stream_usage:
                self._update_usage(
                    stream_usage["input_tokens"],
                    stream_usage["output_tokens"]
                )
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in messages) // 4
                estimated_output = len("".join(content_parts)) // 4
                self._update_usage(estimated_input, estimated_output)

            full_reasoning = "".join(reasoning_parts)
            full_content = "".join(content_parts)

            # ── Hook: after_step ──
            if self.plugin_manager:
                self.plugin_manager.fire_after_step(
                    self._step_count, full_reasoning, full_content,
                    tool_calls_accum)

            assistant_msg = {"role": "assistant", "content": full_content}

            if full_reasoning:
                assistant_msg["reasoning_content"] = full_reasoning

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

            # 服务端原生 web_search 调用：记录到前端日志，并回传上下文
            for wc in web_search_calls:
                query = getattr(wc, "query", "") or ""
                cmd_info = json.dumps({
                    "tool": "web_search (native Responses API)",
                    "args": {"query": query}
                }, ensure_ascii=False)
                self.event_queue.put(f"C:{cmd_info}")
            if web_search_calls:
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

            status = self._process_tool_calls_openai(messages, tool_calls_list, step)
            if status == "stopped":
                return "stopped"

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"


    def _run_anthropic(self, user_message: str, history: Optional[List[Dict]] = None) -> str:
        """使用 Anthropic SDK 调用 DeepSeek Anthropic 兼容端点。
        web_search 作为 Anthropic 原生工具由 API 端自动处理，
        其他自定义工具在客户端执行。
        """
        self._stop_event.clear()
        self.event_queue.reset()

        system_prompt = self._build_system_prompt()

        memory_content = getattr(self, '_memory_content', '')
        if memory_content:
            system_prompt += "\n\n" + memory_content

        openai_messages = self._build_full_messages(user_message, history, memory_content=memory_content)
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

            # ── Hook: before_step ──
            if self.plugin_manager:
                self.plugin_manager.fire_before_step(self._step_count, anthropic_messages)

            result = self._call_anthropic_stream(
                messages=anthropic_messages,
                system_prompt=system_prompt,
                tools=all_tools,
                thinking=thinking_param,
                output_config=output_config_param
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
                self._update_usage(
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0)
                )
            else:
                estimated_input = sum(len(str(m.get("content", ""))) for m in anthropic_messages) // 4
                estimated_output = len(full_content) // 4
                self._update_usage(estimated_input, estimated_output)

            # ── Hook: after_step ──
            if self.plugin_manager:
                self.plugin_manager.fire_after_step(
                    self._step_count, full_reasoning, full_content,
                    tool_uses)

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

                # ── Hook: before_tool_call ──
                if self.plugin_manager:
                    modified_input = self.plugin_manager.fire_before_tool_call(
                        tool_name, tool_input)
                    if modified_input is None:
                        blocked_msg = "Tool call blocked by plugin."
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": blocked_msg
                        })
                        self._log_tool_call(step + 1, tool_name, tool_input, blocked_msg)
                        continue
                    tool_input = modified_input

                if tool_name == "ask_user":
                    question = tool_input.get("question", "")
                    # ── Hook: user_input_required ──
                    if self.plugin_manager:
                        self.plugin_manager.fire_user_input_required(question)
                    self.event_queue.put(f"Q:{json.dumps(question, ensure_ascii=False)}")
                    reply = self._wait_for_user_input()
                    if self._stop_event.is_set():
                        self.event_queue.put("E:Task stopped by user")
                        self.event_queue.signal_finish()
                        return "stopped"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": reply
                    })
                    self._log_tool_call(step + 1, tool_name, tool_input, f"user replied: {reply[:200]}")
                    self._add_tool_tokens(tool_name, reply)
                else:
                    if (tool_name in ("write_file", "delete_file", "replace_in_file")
                            and self.confirm_write_delete):
                        if not self._confirm_write_delete(tool_name, tool_input):
                            if self._stop_event.is_set():
                                self.event_queue.put("E:Task stopped by user")
                                self.event_queue.signal_finish()
                                return "stopped"
                            cancel_msg = "User cancelled the operation."
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": cancel_msg
                            })
                            self._log_tool_call(step + 1, tool_name, tool_input, cancel_msg)
                            continue

                    result_text = self._execute_tool(tool_name, tool_input)

                    # ── Hook: after_tool_call ──
                    if self.plugin_manager:
                        result_text = self.plugin_manager.fire_after_tool_call(
                            tool_name, tool_input, result_text)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result_text
                    })
                    self._log_tool_call(step + 1, tool_name, tool_input, result_text)
                    self._add_tool_tokens(tool_name, result_text)

            if tool_results:
                anthropic_messages.append({
                    "role": "user",
                    "content": tool_results
                })

        self.event_queue.put("E:Max steps reached, task incomplete")
        self.event_queue.signal_finish()
        return "max_steps"

    def _call_anthropic_stream(
        self,
        messages: list,
        system_prompt: str,
        tools: list,
        thinking=None,
        output_config=None
    ) -> Optional[dict]:
        reasoning_parts = []       
        thinking_blocks = []       
        content_parts = []         
        tool_uses = []              
        usage = None                

        _current_thinking_text = ""  
        _current_thinking_sig = ""

        _output_started = False  # track when output starts to flush thinking

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
                # Throttle for streaming hooks
                _last_ts_fire = 0.0
                _reasoning_buf = ""
                _content_buf = ""

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
                            _current_thinking_text = getattr(cb, 'thinking', '') or ''
                            _current_thinking_sig = getattr(cb, 'signature', '') or ''
                        elif cb.type == "redacted_thinking":
                            thinking_blocks.append({
                                "type": "redacted_thinking",
                                "data": getattr(cb, 'data', '') or ''
                            })

                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, 'thinking') and delta.thinking:
                            reasoning_parts.append(delta.thinking)
                            _current_thinking_text += delta.thinking
                            self.event_queue.put(f"T:{delta.thinking}")
                            _reasoning_buf += delta.thinking
                        if hasattr(delta, 'signature') and delta.signature:
                            _current_thinking_sig = delta.signature
                        if hasattr(delta, 'text') and delta.text:
                            # First output chunk → tell front-end to finalize thinking block
                            if not _output_started:
                                _output_started = True
                                self.event_queue.put("F:")
                            content_parts.append(delta.text)
                            self.event_queue.put(f"R:{delta.text}")
                            _content_buf += delta.text

                        # Throttled streaming hooks
                        now = time.time()
                        if self.plugin_manager and now - _last_ts_fire > 0.1:
                            if _reasoning_buf:
                                self.plugin_manager.fire_reasoning(_reasoning_buf)
                                _reasoning_buf = ""
                            if _content_buf:
                                self.plugin_manager.fire_content(_content_buf)
                                _content_buf = ""
                            _last_ts_fire = now

                    elif event.type == "content_block_stop":
                        if hasattr(event, 'content_block') and event.content_block:
                            cb = event.content_block
                            if cb.type == "thinking":
                                final_text = getattr(cb, 'thinking', '') or _current_thinking_text
                                final_sig = getattr(cb, 'signature', '') or _current_thinking_sig
                                thinking_blocks.append({
                                    "type": "thinking",
                                    "thinking": final_text,
                                    "signature": final_sig
                                })
                                _current_thinking_text = ""
                                _current_thinking_sig = ""
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

                # Flush remaining streaming tokens
                if self.plugin_manager:
                    if _reasoning_buf:
                        self.plugin_manager.fire_reasoning(_reasoning_buf)
                    if _content_buf:
                        self.plugin_manager.fire_content(_content_buf)

        except Exception as e:
            self.event_queue.put(f"E:Anthropic API call failed: {str(e)}")
            return {
                "reasoning": "",
                "content": "",
                "thinking_blocks": [],
                "tool_uses": [],
                "usage": None
            }

        full_reasoning = "".join(reasoning_parts)
        full_content = "".join(content_parts)

        return {
            "reasoning": full_reasoning,
            "content": full_content,
            "thinking_blocks": thinking_blocks,
            "tool_uses": tool_uses,
            "usage": usage
        }

    def _convert_openai_messages_to_anthropic(self, messages: list) -> list:
        """将 _build_full_messages() 生成的 OpenAI 格式消息转换为 Anthropic 格式。

        这是针对「当前对话上下文」的简化转换，与 _convert_history_to_anthropic 不同：
        - 去除 system 消息（由 system_prompt 参数单独传入）
        - 去除 tool 消息（新的对话中不会有历史 tool 消息）
        - 保留 user 和 assistant 消息
        """
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

    def _convert_history_to_anthropic(self, history: List[Dict]) -> list:
        result = []
        i = 0
        while i < len(history):
            msg = history[i]
            role = msg.get("role", "")

            if role == "system":
                i += 1
                continue

            elif role == "user":
                result.append({"role": "user", "content": msg.get("content", "")})
                i += 1

            elif role == "assistant":
                content_blocks = []


                text = msg.get("content", "")
                if text:
                    content_blocks.append({"type": "text", "text": text})

                tool_calls = msg.get("tool_calls", [])
                i += 1

                tool_result_ids = set()
                j = i
                while j < len(history) and history[j].get("role") == "tool":
                    tool_result_ids.add(history[j].get("tool_call_id", ""))
                    j += 1

                valid_tool_calls = []
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    if tc_id in tool_result_ids:
                        valid_tool_calls.append(tc)

                for tc in valid_tool_calls:
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except (json.JSONDecodeError, KeyError):
                        args = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": tc["function"]["name"],
                        "input": args
                    })

                result.append({"role": "assistant", "content": content_blocks})

                tool_results = []
                while i < len(history) and history[i].get("role") == "tool":
                    tm = history[i]
                    tc_id = tm.get("tool_call_id", "")
                    if tc_id in tool_result_ids:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc_id,
                            "content": tm.get("content", "")
                        })
                    i += 1

                if tool_results:
                    result.append({"role": "user", "content": tool_results})

            else:
                i += 1

        return result
