# Copyright (c) 2026 xingluosama121, MIT Licensed
"""Web UI 适配器：零依赖 HTTP + SSE 服务（标准库 http.server）。

Agent 的「界面」是可插拔的：本适配器实现 UIAdapter 协议，
与内核完全解耦：

- 订阅 EventBus，把全部 Agent 事件实时推送到浏览器（Server-Sent Events）；
- ``POST /chat`` 提交任务（后台线程执行，不阻塞 HTTP）；
- ``ask_user``：人工审批 / 澄清问题推送到页面，等待用户作答
  （超时回落 default，保证自动化场景不被卡死）；
- ``notify``：非阻塞通知；
- 页面：默认服务 front.html（多宿主前端，pywebview 桌面与浏览器
  双宿主共用，见 front_src/bridge.js）；资源缺失时回落到内置
  简易页面；
- REST API（供 front.html 的浏览器桥使用）：
  /api/sessions 会话 CRUD、/api/config 配置、/api/models 模型、
  /api/plugins* 插件、/api/security 安全、/api/health 健康、
  /api/usage 用量、/api/upload 文件上传、/api/quit 退出等。

用法（宿主应用 / CLI 集成）::

    ui = WebUI(port=8787)
    ui.set_handler(lambda prompt, session_id, task_params: agent.run(...))
    ui.attach_runtime(agent)
    ui.start()          # 后台线程启动 HTTP 服务
    ...                 # 打开 http://127.0.0.1:8787/
    ui.shutdown()

用 AgentRuntime 挂载时：``AgentRuntime(reg, preset, ui=ui)``，
运行时自动把 ui.on_event 订阅到事件总线。
"""

from __future__ import annotations

import base64
import errno
import json
import logging
import os
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

_ASSET_DIR = os.path.join(os.path.dirname(__file__), "assets")
_FRONT_HTML_PATH = os.path.join(_ASSET_DIR, "front.html")
_logger = logging.getLogger("norpagent.ui.web")

# 客户端断开类异常（Windows: WinError 10053/10054；POSIX: EPIPE/ECONNRESET）。
# 这类异常是浏览器刷新 / 关闭标签页 / curl 中断的常态，必须静默处理，
# 绝不允许 traceback 打满控制台。
_CLIENT_GONE_ERRORS = (
    BrokenPipeError,
    ConnectionAbortedError,
    ConnectionResetError,
    ConnectionError,
    TimeoutError,
)


class _RobustHTTPServer(ThreadingHTTPServer):
    """稳健版 ThreadingHTTPServer（pip 库友好）。

    - ``handle_error`` 覆盖：socketserver 默认对每个连接线程的
      未捕获异常调用 ``traceback.print_exc()``，客户端断连噪声
      （如 WinError 10053）会直接打进用户控制台。这里改为：
      断连静默，其余记 DEBUG 日志；
    - ``daemon_threads``：请求线程为守护线程，进程退出不挂起；
    - ``allow_reuse_address``：重启后立即复用端口（避开 TIME_WAIT）。
    """

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def handle_error(self, request: Any, client_address: Any) -> None:
        import sys as _sys

        exc = _sys.exc_info()[1]
        if isinstance(exc, _CLIENT_GONE_ERRORS):
            return
        _logger.debug(
            "http request error from %s: %s", client_address, exc, exc_info=True
        )


def default_project_root() -> str:
    """按操作系统给出默认项目根目录（Windows / macOS / Linux 各有约定）。

    - Windows：``%USERPROFILE%\\Documents\\NORP-Agent``
    - macOS：``~/Documents/NORP-Agent``
    - Linux/其它：``~/norpagent-workspace``
    """
    import sys as _sys

    home = os.path.expanduser("~") or ""
    if _sys.platform == "win32":
        base = os.environ.get("USERPROFILE") or home
        return os.path.join(base, "Documents", "NORP-Agent")
    if _sys.platform == "darwin":
        return os.path.join(home, "Documents", "NORP-Agent")
    return os.path.join(home, "norpagent-workspace")


def _default_config_path() -> str:
    """Web UI 配置持久化文件路径（环境变量可覆盖）。

    浏览器前端的全部设置（模型 / API Key / 语言 / 插件目录等）
    保存在这里：刷新页面、重启 np() 进程都不会丢。
    默认 ``~/.norpagent/webui_config.json``；测试可传入临时路径。
    """
    env = os.environ.get("NORPAGENT_WEBUI_CONFIG")
    if env:
        return str(env)
    return os.path.join(os.path.expanduser("~"), ".norpagent",
                        "webui_config.json")


# 前端「推理强度」选项 → reasoning_effort 参数。
# 注意：DeepSeek V4 仅接受 low / high / max，值在适配器层统一规范化
# （medium → high，见 openai_compat.normalize_effort）；「关」= none，
# 由适配器转译为 DeepSeek V4 的 thinking=disabled。
_THINK_LEVEL_MAP = {
    "关": "none",
    "低": "low",
    "中": "medium",
    "高": "high",
}

# 简易回落页面：assets/front.html 缺失时使用（保持零依赖可运行）
_HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>norpagent Web UI</title>
<style>
  body { font-family: "Segoe UI", system-ui, sans-serif; margin: 0; display: flex;
         height: 100vh; background: #111827; color: #e5e7eb; }
  #main { flex: 1; display: flex; flex-direction: column; max-width: 900px;
          margin: 0 auto; width: 100%; }
  #events { flex: 1; overflow-y: auto; padding: 16px; }
  .ev { margin: 4px 0; padding: 6px 10px; border-radius: 6px; font-size: 13px;
        white-space: pre-wrap; word-break: break-all; }
  .ev-user { background: #1f2937; }
  .ev-content { background: #065f46; }
  .ev-tool { background: #1e3a5f; }
  .ev-meta { background: #374151; color: #9ca3af; }
  .ev-error { background: #7f1d1d; }
  #input-bar { display: flex; padding: 12px; gap: 8px; background: #0b1220; }
  #prompt { flex: 1; padding: 10px; border-radius: 8px; border: 1px solid #374151;
            background: #111827; color: #e5e7eb; font-size: 14px; }
  button { padding: 10px 20px; border-radius: 8px; border: 0; background: #2563eb;
           color: white; cursor: pointer; font-size: 14px; }
  button:disabled { background: #374151; cursor: wait; }
  h3 { margin: 0 16px; color: #9ca3af; font-weight: normal; font-size: 13px; }
</style>
</head>
<body>
<div id="main">
  <h3>norpagent Web UI &middot; 事件流实时推送（SSE）</h3>
  <div id="events"></div>
  <div id="input-bar">
    <input id="prompt" placeholder="输入任务，回车发送" autofocus>
    <button id="send">发送</button>
  </div>
</div>
<script>
const events = document.getElementById('events');
const promptEl = document.getElementById('prompt');
const sendBtn = document.getElementById('send');
let sessionId = null;

function addLine(cls, text) {
  const div = document.createElement('div');
  div.className = 'ev ' + cls;
  div.textContent = text;
  events.appendChild(div);
  events.scrollTop = events.scrollHeight;
  while (events.childNodes.length > 400) events.removeChild(events.firstChild);
}

async function send() {
  const prompt = promptEl.value.trim();
  if (!prompt) return;
  addLine('ev-user', 'User: ' + prompt);
  promptEl.value = '';
  sendBtn.disabled = true;
  try {
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, session_id: sessionId })
    });
    const data = await resp.json();
    if (data.session_id) sessionId = data.session_id;
    if (!data.ok) addLine('ev-error', '[failed] ' + (data.error || data.status));
  } catch (e) {
    addLine('ev-error', '[request failed] ' + e);
  } finally {
    sendBtn.disabled = false;
    promptEl.focus();
  }
}

sendBtn.onclick = send;
promptEl.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });

const es = new EventSource('/events');
es.onmessage = (msg) => {
  try {
    const ev = JSON.parse(msg.data);
    if (ev.type === 'on_content') {
      addLine('ev-content', ev.content);
    } else if (ev.type === 'on_task_start') {
      addLine('ev-meta', '[task start] ' + ev.task_id + ' input: ' + ev.user_input);
    } else if (ev.type === 'before_tool_call') {
      addLine('ev-tool', '[tool] ' + ev.tool_name + ' ' + JSON.stringify(ev.args || {}));
    } else if (ev.type === 'after_tool_call') {
      addLine('ev-tool', '[tool result] ' + ev.tool_name + ' -> ' +
        (String(ev.result || '').slice(0, 300)));
    } else if (ev.type === 'on_task_done') {
      addLine('ev-meta', '[task done] steps=' + (ev.steps || '?'));
    } else if (ev.type === 'on_task_error' || ev.type === 'on_task_timeout') {
      addLine('ev-error', '[task error] ' + (ev.error || ev.timeout));
    } else if (ev.type === 'on_usage_update') {
      addLine('ev-meta', '[usage] in=' + ev.input + ' out=' + ev.output);
    } else if (ev.type === 'question') {
      addLine('ev-error', '[question] ' + ev.question);
      const answer = window.prompt(ev.question, '');
      fetch('/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question_id: ev.question_id, answer: answer || '' })
      });
    } else if (ev.type === 'notify') {
      addLine('ev-meta', '[notify] ' + ev.message);
    }
  } catch (e) { /* 忽略非 JSON 行 */ }
};
es.onerror = () => addLine('ev-error', '[event stream interrupted, reconnecting...]');
</script>
</body>
</html>
"""

# 默认配置：与 front.html 设置面板字段对齐
DEFAULT_CONFIG: Dict[str, Any] = {
    "language": "en",                     # 界面语言（np(language=...) 覆盖）
    "model": "",                          # 模型（缺省 = 引擎预设模型）
    "api_base": "https://api.deepseek.com",
    "api_key": "",
    "project_root": default_project_root(),  # 默认工作区（按操作系统）
    "plugin_dirs": [],
    "norp_safe_enabled": True,
    "plugins_enabled": True,
    "close_button_behavior": "minimize_to_tray",
    "use_responses_api": False,
    "queue_max_size": 200,
    "max_steps": 128,
    "task_timeout": 0,
    "api_request_timeout": 180,
    "enable_web_search": False,
    "native_confirm_enabled": True,
    "native_confirm_write": True,
    "native_confirm_delete": True,
    "native_confirm_exec": True,
    "think_level": "高",
    "temperature": 1.0,
    "max_tokens": 32767,
    "memory": True,
    "memory_mode": "full",
    "max_rounds": 10,
    "custom_system_prompt_enabled": False,
    "custom_system_prompt": "",
    "custom_system_prompt_file": "",
    "jailbreak_guard_enabled": True,
    "jailbreak_guard_action": "block",
    "vision_enabled": False,
    "vision_service_url": "",
    "plugin_security_audit": "block",
    "plugin_security_import_restrict": "strict",
    "plugin_security_require_permissions": True,
    "plugin_security_resource_limit": False,
    "plugin_signature_verify": True,
    "plugin_trusted_keys": [],
    "plugin_isolation": "auto",
    "plugin_network_policy": "deny",
    "plugin_network_url_allowlist": [],
    "plugin_network_domain_allowlist": [],
    "approval_enabled": True,
    "_initialized": False,                # 是否完成过首次配置
}

_MAX_JSON = 1_000_000
_MAX_UPLOAD_JSON = 64_000_000
_MAX_UPLOAD_FILE = 10 * 1024 * 1024


def json_safe(obj: Any, depth: int = 0) -> Any:
    """把任意对象递归转成 JSON 可序列化结构（不可序列化的转字符串）。

    修复：SSE 推送含 ChatMessage / RunContext 等对象时
    json.dumps 抛 TypeError 导致事件流整体断流的问题。
    """
    if depth > 8:
        return str(obj)
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): json_safe(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v, depth + 1) for v in obj]
    try:
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            return json_safe(obj.to_dict(), depth + 1)
    except Exception:  # noqa: BLE001
        pass
    return str(obj)


class WebUI:
    """Web UI 适配器（HTTP + SSE，零第三方依赖，页面 = front.html）。"""

    ui_id = "web"

    def __init__(
        self,
        port: int = 8787,
        host: str = "127.0.0.1",
        ask_timeout: float = 300.0,
        history_limit: int = 2000,
        language: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        config_path: Optional[str] = None,
    ) -> None:
        self.port = int(port)
        self.host = host
        self.ask_timeout = float(ask_timeout)
        self.history_limit = int(history_limit)
        self._language = language or "en"
        # 配置持久化路径：None 表示不落盘（纯内存，测试/嵌入式场景）
        self._config_path = (
            config_path if config_path is not None else _default_config_path()
        )
        self._config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self._config["language"] = self._language
        self._load_config_from_disk()
        # 优先级：显式参数 > 磁盘持久化值 > 默认值
        if language is not None:
            self._config["language"] = language
        if config:
            self._config.update(config)
        self._handler_fn: Optional[Callable] = None
        self._agent: Any = None
        self._config_apply: Optional[Callable[[Dict[str, Any]], None]] = None
        self._quit_callback: Optional[Callable[[], None]] = None
        self._engine_state_fn: Optional[Callable[[], str]] = None
        self._lock = threading.RLock()
        self._subscribers: List[queue.Queue] = []
        self._history: List[dict] = []
        self._questions: Dict[str, Any] = {}
        self._question_sessions: Dict[str, str] = {}
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._task_session: Dict[str, str] = {}
        self._running_sessions: Dict[str, str] = {}
        self._stop_requests: set = set()
        self._session_meta: Dict[str, Dict[str, Any]] = {}
        self._usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0,
                                       "tool_call_tokens": 0}
        self._tlocal = threading.local()
        self._start_ts = time.time()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._closed = False

    # ── 宿主集成 ────────────────────────────────────────

    def set_handler(self, fn: Callable) -> None:
        """设置任务执行回调：fn(prompt, session_id, task_params) -> RunResult 类似对象。"""
        self._handler_fn = fn

    def attach_runtime(self, agent: Any) -> None:
        """绑定 Agent 运行时（会话 REST API / 插件列表 / 调试信息的数据源）。"""
        self._agent = agent
        if agent is not None:
            preset = getattr(agent, "preset", None)
            if preset is not None and not self._config.get("model"):
                self._config["model"] = getattr(preset, "model", "") or ""
            # np(workspace_root=...) 显式指定时覆盖平台默认工作区
            params = getattr(agent, "params", None) or {}
            if params.get("workspace_root"):
                self._config["project_root"] = str(params["workspace_root"])

    def set_config_apply(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        """配置保存后的应用回调（WebFrontend 据此重注册模型/插件/安全）。"""
        self._config_apply = cb

    def set_quit_callback(self, cb: Callable[[], None]) -> None:
        self._quit_callback = cb

    def set_engine_state_fn(self, fn: Callable[[], str]) -> None:
        self._engine_state_fn = fn

    # ── 服务生命周期 ────────────────────────────────────

    def start(self) -> "WebUI":
        """后台线程启动 HTTP 服务（非阻塞）。

        端口被占用时自动向后顺延尝试（最多 10 个端口），
        以实际绑定端口为准（``self.port`` 会被更新），
        彻底绑定失败时抛出带清晰信息的 RuntimeError（不刷 traceback）。
        """
        if self._server is not None:
            return self
        ui = self

        class _Handler(BaseHTTPRequestHandler):
            server_version = "norpagent-webui/0.4"
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):  # 静默访问日志
                pass

            # ── 连接健壮性 ────────────────────────────

            def handle(self):  # noqa: N802
                """覆盖 BaseHTTPRequestHandler.handle：客户端断开不刷 traceback。

                浏览器刷新 / 关闭标签页 / curl 中断都会让读写抛
                ConnectionAbortedError / ConnectionResetError /
                BrokenPipeError，此前一路冒泡到 socketserver，
                控制台被打满 traceback。这里统一吞掉断连噪声；
                真正的内部错误记 DEBUG 日志并尝试返回 500。
                """
                try:
                    super().handle()
                except _CLIENT_GONE_ERRORS:
                    pass
                except Exception as exc:  # noqa: BLE001 — 防御性兜底
                    self.close_connection = True
                    _logger.debug(
                        "request %s failed: %s", getattr(self, "path", "?"),
                        exc, exc_info=True,
                    )
                    try:
                        self._json(500, {"error": "internal server error"})
                    except Exception:  # noqa: BLE001
                        pass

            def finish(self):  # noqa: N802
                """wfile flush 在客户端断开时也会抛异常，同样静默。"""
                try:
                    super().finish()
                except (OSError, _CLIENT_GONE_ERRORS):  # noqa: BLE001
                    pass

            def _json(self, code: int, obj: dict) -> None:
                body = json.dumps(json_safe(obj), ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self, limit: int = _MAX_JSON) -> dict:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    length = 0
                if length > limit:
                    # 超大请求体：直接拒收并关闭连接，避免残留未读字节
                    # 让 keep-alive 连接协议错位（后续请求解析出垃圾）。
                    self.close_connection = True
                    return {}
                if length < 0:
                    length = 0  # 负数 Content-Length：按无请求体处理
                raw = self.rfile.read(length) if length else b""
                try:
                    data = json.loads(raw.decode("utf-8"))
                    return data if isinstance(data, dict) else {}
                except Exception:
                    return {}

            def _html(self, code: int, body: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                # 页面禁止浏览器缓存：每次刷新都取最新 front.html，
                # 否则修复后的前端（如思维链「思考」块翻译）会被旧缓存遮蔽
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                query = parse_qs(parsed.query)
                if path in ("/", "/index.html"):
                    self._html(200, ui.page_bytes())
                elif path == "/favicon.ico":
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif path == "/events":
                    self._handle_sse()
                elif path == "/api/status":
                    self._json(200, ui.stats())
                elif path == "/api/sessions":
                    self._json(200, {"sessions": ui.list_sessions()})
                elif path.startswith("/api/sessions/"):
                    self._handle_session_get(path)
                elif path == "/api/config":
                    self._json(200, ui.get_config())
                elif path == "/api/first_run":
                    self._json(200, {"first_run": ui.first_run()})
                elif path == "/api/models":
                    self._json(200, ui.list_models(query.get("base_url", [""])[0]))
                elif path == "/api/plugins":
                    self._json(200, {"plugins": ui.list_plugins()})
                elif path == "/api/plugins/dirs":
                    self._json(200, {"dirs": ui.get_plugin_dirs()})
                elif path == "/api/security":
                    self._json(200, ui.get_security())
                elif path == "/api/health":
                    self._json(200, ui.health())
                elif path == "/api/usage":
                    self._json(200, ui.usage())
                elif path == "/api/balance":
                    self._json(200, {"balance": None, "error": None})
                elif path == "/api/debug":
                    self._json(200, ui.debug_info())
                elif path == "/api/memory":
                    self._json(200, {"content": None})
                elif path == "/api/fs/list":
                    q = parse_qs(parsed.query)
                    self._json(200, ui.list_fs(
                        q.get("path", [""])[0],
                        include_files=q.get("files", ["0"])[0] == "1",
                    ))
                elif path == "/api/fs/read":
                    q = parse_qs(parsed.query)
                    self._json(200, ui.read_fs_file(q.get("path", [""])[0]))
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/chat":
                    data = self._read_json()
                    prompt = str(data.get("prompt") or "").strip()
                    if not prompt:
                        self._json(400, {"ok": False, "error": "prompt 为空"})
                        return
                    task_id = ui.submit(
                        prompt, str(data.get("session_id") or "") or None
                    )
                    self._json(200, {
                        "ok": True, "task_id": task_id,
                        "session_id": ui._tasks.get(task_id, {}).get("session_id"),
                    })
                elif path == "/answer":
                    data = self._read_json()
                    ui.answer(
                        str(data.get("question_id") or ""),
                        str(data.get("answer") or ""),
                        str(data.get("session_id") or "") or None,
                    )
                    self._json(200, {"ok": True})
                elif path == "/stop":
                    data = self._read_json()
                    ui.stop_task(str(data.get("session_id") or "") or None)
                    self._json(200, {"ok": True})
                elif path == "/api/sessions":
                    data = self._read_json()
                    try:
                        sess = ui.create_session(
                            title=str(data.get("title") or ""),
                            workspace=str(data.get("workspace") or ""),
                        )
                        self._json(200, sess)
                    except Exception as exc:  # noqa: BLE001
                        self._json(500, {"error": str(exc)})
                elif path.startswith("/api/sessions/"):
                    self._handle_session_post(path, data=self._read_json())
                elif path == "/api/config":
                    data = self._read_json()
                    self._json(200, {"ok": True, "config": ui.save_config(
                        data.get("config") or {})})
                elif path == "/api/config/reset":
                    self._json(200, {"ok": True, "config": ui.reset_config()})
                elif path == "/api/key":
                    data = self._read_json()
                    self._json(200, ui.set_api_key(str(data.get("api_key") or "")))
                elif path == "/api/key/validate":
                    data = self._read_json()
                    self._json(200, ui.validate_api_key(
                        str(data.get("api_key") or ""),
                        str(data.get("base_url") or ""),
                    ))
                elif path == "/api/upload":
                    data = self._read_json(limit=_MAX_UPLOAD_JSON)
                    files = data.get("files")
                    if not isinstance(files, list):
                        self._json(400, {"error": "files 必须为列表"})
                        return
                    self._json(200, {"files": ui.upload_files(files)})
                elif path == "/api/plugins/dirs":
                    data = self._read_json()
                    self._json(200, {"ok": True, "dirs": ui.add_plugin_dir(
                        str(data.get("path") or ""))})
                elif path == "/api/plugins/reload":
                    self._json(200, {"ok": True, "plugins": ui.reload_plugins()})
                elif path == "/api/security":
                    data = self._read_json()
                    self._json(200, ui.set_security(data))
                elif path == "/api/memory/clear":
                    data = self._read_json()
                    self._json(200, {"ok": True})
                elif path == "/api/log":
                    data = self._read_json()
                    _logger.info("frontend: %s", str(data.get("message") or "")[:2000])
                    self._json(200, {"ok": True})
                elif path == "/api/quit":
                    self._json(200, {"ok": True})
                    ui.request_quit()
                else:
                    self._json(404, {"error": "not found"})

            def do_DELETE(self):  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path
                if path.startswith("/api/sessions/"):
                    sid = path[len("/api/sessions/"):].strip("/")
                    self._json(200, ui.close_session(sid))
                elif path == "/api/plugins/dirs":
                    data = self._read_json()
                    self._json(200, {"ok": True, "dirs": ui.remove_plugin_dir(
                        str(data.get("path") or ""))})
                else:
                    self._json(404, {"error": "not found"})

            # ── 子路由 ──────────────────────────────────

            def _handle_session_get(self, path: str) -> None:
                rest = path[len("/api/sessions/"):].strip("/")
                parts = rest.split("/")
                sid = parts[0] if parts else ""
                if not sid:
                    self._json(404, {"error": "session id 缺失"})
                    return
                if len(parts) == 1:
                    self._json(200, {"session": ui.session_info(sid)})
                elif parts[1] == "messages":
                    self._json(200, {"messages": ui.session_messages(sid)})
                else:
                    self._json(404, {"error": "not found"})

            def _handle_session_post(self, path: str, data: dict) -> None:
                rest = path[len("/api/sessions/"):].strip("/")
                parts = rest.split("/")
                sid = parts[0] if parts else ""
                if len(parts) >= 2:
                    if parts[1] == "title":
                        ui.set_session_title(sid, str(data.get("title") or ""))
                        self._json(200, {"ok": True})
                        return
                    if parts[1] == "workspace":
                        ui.set_session_workspace(sid, str(data.get("workspace") or ""))
                        self._json(200, {"ok": True})
                        return
                self._json(404, {"error": "not found"})

            def _handle_sse(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                q: "queue.Queue[dict]" = queue.Queue()
                ui._register_subscriber(q)
                try:
                    # 先补发近期历史
                    for item in ui._recent_history():
                        self.wfile.write(
                            f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n".encode("utf-8")
                        )
                    self.wfile.flush()
                    while True:
                        try:
                            item = q.get(timeout=15.0)
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            continue
                        self.wfile.write(
                            f"data: {json.dumps(item, ensure_ascii=False, default=str)}\n\n".encode("utf-8")
                        )
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    ui._unregister_subscriber(q)

        self._server = self._bind(_Handler)
        # port=0 或端口顺延时以实际绑定结果为准（打印 listening on 用）
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._serve, daemon=True, name="norpagent-webui"
        )
        self._thread.start()
        return self

    def _bind(self, handler_cls: type) -> "_RobustHTTPServer":
        """绑定监听端口：占用时顺延重试，失败抛出清晰错误。"""
        in_use_errnos = (
            getattr(errno, "EADDRINUSE", -1),       # POSIX / Windows 10048
            getattr(errno, "WSAEADDRINUSE", -1),    # Windows 10048
            getattr(errno, "EACCES", -1),           # Linux 特权/保留端口
            getattr(errno, "WSAEACCES", -1),        # Windows 10013：端口被监听占用
        )
        last_exc: Optional[OSError] = None
        for offset in range(10):
            candidate = self.port + offset
            try:
                return _RobustHTTPServer((self.host, candidate), handler_cls)
            except OSError as exc:
                last_exc = exc
                if exc.errno not in in_use_errnos or offset >= 9:
                    break
                _logger.warning(
                    "port %s 已被占用，尝试 %s", candidate, candidate + 1
                )
        raise RuntimeError(
            f"无法启动 Web UI（{self.host}:{self.port} 绑定失败）: {last_exc}"
        ) from last_exc

    def _serve(self) -> None:
        """serve_forever 包装：守护线程自身兜底，异常退出不刷控制台。"""
        server = self._server
        if server is None:
            return
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:  # pragma: no cover — 防御
            pass
        except Exception:  # noqa: BLE001
            _logger.exception("web server 异常退出")
        finally:
            with self._lock:
                if self._server is server:
                    self._server = None

    def page_bytes(self) -> bytes:
        """返回 front.html 页面字节（资源缺失时回落内置简易页面）。"""
        try:
            with open(_FRONT_HTML_PATH, "rb") as f:
                return f.read()
        except OSError:
            return _HTML_PAGE.encode("utf-8")

    def request_quit(self) -> None:
        """请求宿主应用退出（非阻塞）。"""
        cb = self._quit_callback
        if cb is not None:
            threading.Thread(target=cb, daemon=True, name="norpagent-webui-quit").start()

    # ── 任务执行 ────────────────────────────────────────

    def _task_defaults(self) -> Dict[str, Any]:
        """把配置面板的采样参数翻译为任务级模型参数。

        - 推理强度（think_level）→ reasoning_effort（关=不传，交给温度）；
        - 温度（推理开启时由适配器省略）；
        - max_tokens。
        通过 task_params 注入后，AgentRuntime 会原样传给模型适配器。
        """
        with self._lock:
            think = str(self._config.get("think_level") or "高")
            temperature = self._config.get("temperature")
            max_tokens = self._config.get("max_tokens")
        effort = _THINK_LEVEL_MAP.get(think, "high")
        defaults: Dict[str, Any] = {}
        if effort != "none":
            defaults["reasoning_effort"] = effort
        else:
            try:
                defaults["temperature"] = float(temperature) if temperature is not None else 1.0
            except (TypeError, ValueError):
                defaults["temperature"] = 1.0
        if max_tokens:
            try:
                defaults["max_tokens"] = int(max_tokens)
            except (TypeError, ValueError):
                pass
        return defaults

    def submit(self, prompt: str, session_id: Optional[str],
               task_params: Optional[Dict[str, Any]] = None) -> str:
        """提交一个任务，后台线程执行（不阻塞 HTTP）。"""
        sid = session_id or ""
        task_id = uuid.uuid4().hex[:12]
        record = {
            "task_id": task_id,
            "status": "running",
            "prompt": prompt,
            "session_id": sid,
            "result": None,
            "error": "",
        }
        with self._lock:
            self._tasks[task_id] = record
            self._task_session[task_id] = sid
            if sid:
                self._running_sessions[sid] = task_id
        self._publish({
            "type": "notify",
            "level": "info",
            "message": f"Task {task_id} submitted",
            "ts": time.time(),
            "sid": sid or None,
        })

        def worker() -> None:
            self._tlocal.session_id = sid
            try:
                if self._handler_fn is None:
                    raise RuntimeError("WebUI 未设置执行回调（ui.set_handler(...)）")
                tp = dict(task_params or {})
                # 配置面板的采样参数注入任务（调用方可显式覆盖）
                for key, value in self._task_defaults().items():
                    tp.setdefault(key, value)
                tp.setdefault("_stop_check", lambda: sid in self._stop_requests)
                meta = self._session_meta.get(sid)
                if meta and meta.get("workspace") and "workspace_root" not in tp:
                    tp["workspace_root"] = meta["workspace"]
                result = self._invoke_handler(self._handler_fn, prompt, sid, tp)
                status = getattr(result, "status", "done")
                content = getattr(result, "final_content", "") or ""
                error = getattr(result, "error", "") or ""
                record["status"] = status
                record["result"] = content
                record["error"] = error
                record["session_id"] = getattr(result, "session_id", "") or sid
                self._publish({
                    "type": "notify",
                    "level": "info" if status == "done" else "error",
                    "message": f"Task {task_id} finished ({status})",
                    "ts": time.time(),
                    "sid": getattr(result, "session_id", "") or sid or None,
                })
            except Exception as exc:  # noqa: BLE001
                record["status"] = "error"
                record["error"] = str(exc)
                self._publish({
                    "type": "notify",
                    "level": "error",
                    "message": f"Task {task_id} failed: {exc}",
                    "ts": time.time(),
                    "sid": sid or None,
                })
            finally:
                self._tlocal.session_id = None
                with self._lock:
                    self._task_session.pop(task_id, None)
                    if sid:
                        self._running_sessions.pop(sid, None)
                    self._stop_requests.discard(sid)

        threading.Thread(
            target=worker, daemon=True, name=f"norpagent-webui-task-{task_id}"
        ).start()
        return task_id

    def stop_task(self, session_id: Optional[str]) -> None:
        """请求停止某会话正在运行的任务（在步骤边界生效）。"""
        sid = session_id or ""
        with self._lock:
            if sid and sid in self._running_sessions:
                self._stop_requests.add(sid)

    @staticmethod
    def _invoke_handler(fn: Callable, prompt: str, session_id: str,
                        task_params: Dict[str, Any]) -> Any:
        """按处理器签名调用：声明了 task_params 则传任务参数，否则两参调用。"""
        try:
            import inspect

            sig = inspect.signature(fn)
            accepts = (
                any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
                or "task_params" in sig.parameters
            )
        except (TypeError, ValueError):
            accepts = False
        if accepts:
            return fn(prompt, session_id, task_params)
        return fn(prompt, session_id)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            tasks = list(self._tasks.values())
        return {
            "ui": self.ui_id,
            "port": self.port,
            "subscribers": len(self._subscribers),
            "history": len(self._history),
            "tasks_total": len(tasks),
            "tasks_running": len(self._running_sessions),
            "language": self._config.get("language", "en"),
            "tasks": tasks[-20:],
        }

    # ── UIAdapter 协议 ─────────────────────────────────

    def on_event(self, event: Any) -> None:
        """接收 AgentEvent：推送给所有 SSE 订阅者并记录历史。

        payload 先经 json_safe 清洗（ChatMessage 等对象安全落网），
        并为每条事件附上会话 id（sid），供前端按标签页路由。

        sid 解析优先级：submit() 登记的 task_id → 原始浏览器会话 id
        （最高优先，防止内核因会话漂移另开新会话导致事件错投）；
        其次才是 payload 自带的 session_id。
        """
        raw_payload = getattr(event, "payload", {}) or {}
        payload = json_safe(raw_payload)
        task_id = payload.get("task_id")
        sid = None
        if task_id:
            with self._lock:
                sid = self._task_session.get(task_id)
        if not sid:
            sid = payload.get("session_id")
        if not sid:
            sid = getattr(self._tlocal, "session_id", None) or ""
        if task_id and sid:
            with self._lock:
                self._task_session.setdefault(task_id, sid)
        item = {
            "type": getattr(event, "type", "?"),
            "payload": payload,
            "ts": getattr(event, "ts", time.time()),
            "sid": sid or None,
        }
        # 展平常用字段，便于前端直接读取
        for key in ("content", "tool_name", "args", "result", "task_id",
                    "user_input", "error", "steps", "timeout", "stream",
                    "input", "output", "total", "session_id", "question",
                    "reason", "reasoning", "tool_call_tokens"):
            if key in payload:
                item[key] = payload[key]
        # 用量累计
        if item["type"] == "on_usage_update":
            try:
                with self._lock:
                    self._usage["input_tokens"] += int(payload.get("input") or 0)
                    self._usage["output_tokens"] += int(payload.get("output") or 0)
                    self._usage["tool_call_tokens"] += int(payload.get("tool_call_tokens") or 0)
            except (TypeError, ValueError):
                pass
        self._publish(item)

    def ask_user(self, question: str, default: str = "") -> str:
        """向用户提问（人工审批 / 澄清）。等待用户在页面作答，
        超时返回 default，保证自动化场景不被卡死。
        """
        question_id = uuid.uuid4().hex[:12]
        box = {"answer": None, "event": threading.Event()}
        sid = getattr(self._tlocal, "session_id", None) or ""
        if not sid:
            # 兜底：非任务线程（或线程上下文丢失）时，唯一运行中的会话即归属
            with self._lock:
                running = list(self._running_sessions.keys())
                if len(running) == 1:
                    sid = running[0]
        with self._lock:
            self._questions[question_id] = box
            if sid:
                self._question_sessions[sid] = question_id
        self._publish({
            "type": "question", "question": question,
            "question_id": question_id, "ts": time.time(),
            "sid": sid or None,
        })
        box["event"].wait(self.ask_timeout)
        with self._lock:
            self._questions.pop(question_id, None)
            if sid:
                self._question_sessions.pop(sid, None)
        answer = box["answer"]
        if answer is None:
            return default
        return str(answer)

    def answer(self, question_id: str, answer: str,
               session_id: Optional[str] = None) -> None:
        with self._lock:
            box = self._questions.get(question_id)
            if box is None and session_id:
                qid = self._question_sessions.get(session_id)
                box = self._questions.get(qid) if qid else None
        if box is not None:
            box["answer"] = answer
            box["event"].set()

    def notify(self, message: str, level: str = "info") -> None:
        self._publish({
            "type": "notify", "message": message,
            "level": level, "ts": time.time(),
            "sid": getattr(self._tlocal, "session_id", None),
        })

    # ── 会话 REST ──────────────────────────────────────

    def _session_manager(self) -> Any:
        if self._agent is None:
            raise RuntimeError("runtime 未绑定（attach_runtime）")
        return self._agent.session_manager

    def create_session(self, title: str = "", workspace: str = "") -> Dict[str, Any]:
        sm = self._session_manager()
        sess = sm.create_session(title=title or "")
        with self._lock:
            self._session_meta[sess.id] = {
                "title": title or sess.title or sess.id[:8],
                "workspace": workspace or "",
                "created_at": getattr(sess, "created_at", time.time()),
            }
        return self.session_info(sess.id)

    def session_info(self, sid: str) -> Dict[str, Any]:
        sm = self._session_manager()
        sess = sm.get_session(sid)
        with self._lock:
            meta = self._session_meta.get(sid) or {}
        if sess is None and not meta:
            return {"id": sid, "exists": False}
        return {
            "id": sid,
            "title": meta.get("title") or (getattr(sess, "title", "") or sid[:8]),
            "workspace": meta.get("workspace") or "",
            "created_at": meta.get("created_at") or getattr(sess, "created_at", 0.0),
            "exists": sess is not None,
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        sm = self._session_manager()
        sessions = sm.list_sessions()
        with self._lock:
            meta_map = dict(self._session_meta)
        out = []
        for sess in sessions:
            meta = meta_map.get(sess.id) or {}
            out.append({
                "id": sess.id,
                "title": meta.get("title") or getattr(sess, "title", "") or sess.id[:8],
                "workspace": meta.get("workspace") or "",
                "created_at": meta.get("created_at")
                or getattr(sess, "created_at", time.time()),
            })
        return out

    def session_messages(self, sid: str) -> List[Dict[str, str]]:
        sm = self._session_manager()
        messages = []
        for m in sm.history(sid):
            role = getattr(m, "role", "") or ""
            if role == "tool":
                continue  # 工具消息属于内部过程，不进入聊天面板
            messages.append({
                "role": role,
                "content": getattr(m, "content", "") or "",
            })
        return messages

    def close_session(self, sid: str) -> Dict[str, Any]:
        self.stop_task(sid)
        try:
            sm = self._session_manager()
            sm.delete_session(sid)
        except Exception:  # noqa: BLE001 — 运行时未绑定等情况
            pass
        with self._lock:
            self._session_meta.pop(sid, None)
        return {"ok": True}

    def set_session_title(self, sid: str, title: str) -> None:
        with self._lock:
            meta = self._session_meta.setdefault(sid, {})
            meta["title"] = title
        try:
            sm = self._session_manager()
            sess = sm.get_session(sid)
            if sess is not None:
                sess.title = title
        except Exception:  # noqa: BLE001
            pass

    def set_session_workspace(self, sid: str, workspace: str) -> None:
        with self._lock:
            meta = self._session_meta.setdefault(sid, {})
            meta["workspace"] = workspace

    # ── 配置 ───────────────────────────────────────────

    def _needs_key(self) -> bool:
        model = str(self._config.get("model") or "")
        return model in ("openai_compat", "anthropic")

    def first_run(self) -> bool:
        with self._lock:
            initialized = bool(self._config.get("_initialized"))
            has_key = bool(self._config.get("api_key"))
            needs = self._needs_key()
        return (not initialized) and needs and (not has_key)

    def get_config(self) -> Dict[str, Any]:
        with self._lock:
            cfg = dict(self._config)
        cfg["has_api_key"] = bool(cfg.get("api_key")) or not self._needs_key()
        cfg["first_run"] = self.first_run()
        return cfg

    def save_config(self, incoming: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            for key, value in (incoming or {}).items():
                if key == "_initialized":
                    continue
                self._config[key] = value
            self._config["_initialized"] = True
            cfg = dict(self._config)
        self._save_config_to_disk(cfg)
        self._apply_config(cfg)
        return self.get_config()

    def reset_config(self) -> Dict[str, Any]:
        with self._lock:
            model = self._config.get("model") or ""
            language = self._language
            self._config = dict(DEFAULT_CONFIG)
            self._config["language"] = language
            if model:
                self._config["model"] = model
            cfg = dict(self._config)
        self._save_config_to_disk(cfg)
        self._apply_config(cfg)
        return self.get_config()

    def set_api_key(self, api_key: str) -> Dict[str, Any]:
        with self._lock:
            self._config["api_key"] = api_key or ""
            self._config["_initialized"] = True
            cfg = dict(self._config)
        self._save_config_to_disk(cfg)
        self._apply_config(cfg)
        return {"ok": True, "config": self.get_config()}

    def validate_api_key(self, api_key: str, base_url: str = "") -> Dict[str, Any]:
        if not api_key:
            return {"ok": False, "error": "API Key 为空"}
        try:
            import openai  # noqa: F401  可选依赖
        except ImportError:
            return {"ok": False, "error": "未安装 openai SDK（pip install norpagent[openai]），无法在线验证"}
        try:
            from openai import OpenAI

            client = OpenAI(
                base_url=base_url or self._config.get("api_base")
                or "https://api.deepseek.com",
                api_key=api_key,
                timeout=15,
            )
            client.models.list()
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"invalid api key: {exc}"}

    def _apply_config(self, cfg: Dict[str, Any]) -> None:
        cb = self._config_apply
        if cb is not None:
            try:
                cb(cfg)
            except Exception:  # noqa: BLE001 — 配置应用失败不能拖垮 HTTP
                _logger.exception("config apply 失败")

    # ── 配置持久化（浏览器前端设置 / API Key 跨进程保留） ──

    def _load_config_from_disk(self) -> None:
        """启动时从磁盘加载上次保存的配置（文件缺失 / 损坏时静默忽略）。

        只接受 DEFAULT_CONFIG 中声明的键 + ``_initialized``，
        未知键一律丢弃（防外部写入注入陌生配置项）。
        """
        path = self._config_path
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        allowed = set(DEFAULT_CONFIG) | {"_initialized"}
        for key, value in data.items():
            if key in allowed:
                self._config[key] = value

    def _save_config_to_disk(self, cfg: Dict[str, Any]) -> None:
        """把配置原子写入磁盘（失败只记日志，不拖垮保存流程）。

        - 只落盘 DEFAULT_CONFIG 中的键 + ``_initialized``；
        - 临时文件 + os.replace 原子替换，避免写一半损坏；
        - POSIX 下收紧权限 0600（含 API Key）。
        """
        path = self._config_path
        if not path:
            return
        try:
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            allowed = set(DEFAULT_CONFIG) | {"_initialized"}
            data = {k: v for k, v in cfg.items() if k in allowed}
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:  # Windows 权限位有限，忽略
                pass
            os.replace(tmp, path)
        except OSError as exc:  # noqa: BLE001
            _logger.warning("webui 配置写入 %s 失败: %s", path, exc)

    # ── 模型 / 插件 / 安全 / 统计 ───────────────────────

    def _registry(self) -> Any:
        if self._agent is None:
            return None
        return getattr(self._agent, "registry", None)

    def list_models(self, base_url: str = "") -> Dict[str, Any]:
        reg = self._registry()
        names = sorted(reg.list_models()) if reg is not None else []
        remote: List[str] = []
        error: Optional[str] = None
        if base_url and self._config.get("api_key"):
            try:
                from openai import OpenAI  # noqa: F401  可选依赖
            except ImportError:
                error = "未安装 openai SDK（pip install norpagent[openai]）"
            else:
                try:
                    client = OpenAI(
                        base_url=base_url,
                        api_key=self._config.get("api_key") or "",
                        timeout=15,
                    )
                    remote = [m.id for m in client.models.list()][:200]
                except Exception as exc:  # noqa: BLE001
                    error = f"{exc}"
        if remote:
            return {"models": remote, "error": None}
        return {"models": names, "error": error}

    def get_plugin_dirs(self) -> List[str]:
        with self._lock:
            dirs = self._config.get("plugin_dirs") or []
        return list(dirs)

    def list_plugins(self) -> List[Dict[str, Any]]:
        reg = self._registry()
        if reg is None:
            return []
        plugins = getattr(reg, "_plugins", {}) or {}
        out = []
        for name in sorted(plugins):
            p = plugins[name]
            tools = []
            try:
                tools = [getattr(t, "name", "") or "" for t in p.get_tools()]
            except Exception:  # noqa: BLE001
                tools = []
            hooks = []
            try:
                hooks = list((p.get_hooks() or {}).keys())
            except Exception:  # noqa: BLE001
                hooks = []
            out.append({
                "name": name,
                "version": str(getattr(p, "version", "") or ""),
                "publisher": str(getattr(p, "publisher", "") or ""),
                "description": str(getattr(p, "description", "") or ""),
                "enabled": True,
                "error": "",
                "tools": tools,
                "hooks": hooks,
                "tool_count": len(tools),
                "hook_count": len(hooks),
                "audit_critical": 0,
                "audit_warning": 0,
                "audit_info": 0,
                "signature_status": "unknown",
                "isolation": "inproc",
            })
        return out

    def add_plugin_dir(self, path: str) -> List[str]:
        path = (path or "").strip()
        with self._lock:
            dirs = list(self._config.get("plugin_dirs") or [])
            if path and path not in dirs:
                dirs.append(path)
                self._config["plugin_dirs"] = dirs
                cfg = dict(self._config)
        self._apply_config(cfg)
        return dirs

    def remove_plugin_dir(self, path: str) -> List[str]:
        path = (path or "").strip()
        with self._lock:
            dirs = [d for d in (self._config.get("plugin_dirs") or []) if d != path]
            self._config["plugin_dirs"] = dirs
            cfg = dict(self._config)
        self._apply_config(cfg)
        return dirs

    def reload_plugins(self) -> List[Dict[str, Any]]:
        with self._lock:
            cfg = dict(self._config)
        self._apply_config(cfg)
        return self.list_plugins()

    def get_security(self) -> Dict[str, Any]:
        with self._lock:
            cfg = dict(self._config)
        # 与桌面前端 openPluginPanel 期望的扁平结构对齐
        return {
            "norp_safe_enabled": cfg.get("norp_safe_enabled", True),
            "plugins_enabled": cfg.get("plugins_enabled", True),
            "audit": cfg.get("plugin_security_audit", "block"),
            "import_restrict": cfg.get("plugin_security_import_restrict", "strict"),
            "require_permissions": cfg.get("plugin_security_require_permissions", True),
            "resource_limit": cfg.get("plugin_security_resource_limit", False),
            "signature_verify": cfg.get("plugin_signature_verify", True),
            "trusted_keys": list(cfg.get("plugin_trusted_keys") or []),
            "isolation": cfg.get("plugin_isolation", "auto"),
            "network_policy": cfg.get("plugin_network_policy", "deny"),
            "network_url_allowlist": list(cfg.get("plugin_network_url_allowlist") or []),
            "network_domain_allowlist": list(cfg.get("plugin_network_domain_allowlist") or []),
            "approval_enabled": cfg.get("approval_enabled", True),
        }

    # 桌面前端 set_plugin_security_config 的 12 个位置参数
    _SECURITY_ARG_KEYS = (
        "plugin_security_audit",            # 0
        "plugin_security_import_restrict",  # 1
        "plugin_security_require_permissions",  # 2
        "plugin_security_resource_limit",   # 3
        "plugin_isolation",                 # 4
        "plugin_signature_verify",          # 5
        "plugin_trusted_keys",              # 6
        "plugin_network_policy",            # 7
        "plugin_network_url_allowlist",     # 8
        "plugin_network_domain_allowlist",  # 9
        "approval_enabled",                 # 10
        "plugins_enabled",                  # 11
    )

    def set_security(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if "norp_safe_enabled" in data:
                self._config["norp_safe_enabled"] = bool(data["norp_safe_enabled"])
            sec = data.get("security")
            if isinstance(sec, dict):
                for key, value in sec.items():
                    if key.startswith("plugin_") or key in ("norp_safe_enabled",):
                        self._config[key] = value
            args = data.get("security_args")
            if isinstance(args, list):
                for idx, value in enumerate(args):
                    if idx < len(self._SECURITY_ARG_KEYS):
                        self._config[self._SECURITY_ARG_KEYS[idx]] = value
            cfg = dict(self._config)
        self._apply_config(cfg)
        return self.get_security()

    def health(self) -> Dict[str, Any]:
        state = "unknown"
        if self._engine_state_fn is not None:
            try:
                state = self._engine_state_fn() or "unknown"
            except Exception:  # noqa: BLE001
                pass
        engine_ok = state in ("running", "starting")
        running = len(self._running_sessions)
        checks = [
            {
                "name": "HTTP Service",
                "passed": True,
                "severity": "info",
                "message": f"listening on http://{self.host}:{self.port}/",
            },
            {
                "name": "Engine",
                "passed": engine_ok,
                "severity": "info" if engine_ok else "error",
                "message": f"engine state: {state}",
            },
            {
                "name": "Running Tasks",
                "passed": True,
                "severity": "info",
                "message": f"{running} task(s) running",
            },
            {
                "name": "SSE Subscribers",
                "passed": True,
                "severity": "info",
                "message": f"{len(self._subscribers)} subscriber(s)",
            },
        ]
        fatal = 0 if engine_ok else 1
        return {
            "ok": engine_ok,
            "status": "healthy" if engine_ok else "degraded",
            "overall_healthy": engine_ok,
            "fatal_count": fatal,
            "error_count": 0 if engine_ok else 1,
            "warning_count": 0,
            "environment_type": "normal",
            "engine_state": state,
            "tasks_running": running,
            "subscribers": len(self._subscribers),
            "uptime": round(time.time() - self._start_ts, 1),
            "checks": checks,
        }

    def usage(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._usage)

    def debug_info(self) -> Dict[str, Any]:
        reg = self._registry()
        return {
            "version": _package_version(),
            "frontend": "web",
            "language": self._config.get("language", "en"),
            "presets": sorted(reg.list_presets()) if reg is not None else [],
            "models": sorted(reg.list_models()) if reg is not None else [],
            "tools": sorted(reg.list_tools()) if reg is not None else [],
            "plugins": sorted(reg.list_plugins()) if reg is not None else [],
            "sessions": len(self.list_sessions()) if self._agent is not None else 0,
            "tasks_total": len(self._tasks),
        }

    # ── 文件系统浏览（浏览器宿主的「目录/文件选择框」） ──

    def list_fs(self, path: str = "", include_files: bool = False) -> Dict[str, Any]:
        """列出一个目录的子目录（可选文件），供浏览器端目录选择框导航。

        path 为空时返回主目录，Windows 下附带盘符列表。
        这是纯本地 UI 能力：服务仅监听 127.0.0.1，且只做只读列举。
        """
        import sys as _sys

        home = os.path.expanduser("~") or ""
        raw = (path or "").strip()
        target = os.path.abspath(os.path.expanduser(raw or home))
        result: Dict[str, Any] = {
            "ok": True,
            "path": target,
            "parent": "",
            "dirs": [],
            "files": [],
            "home": home,
        }
        if _sys.platform == "win32" and not raw:
            drives: List[Dict[str, str]] = []
            try:
                import string as _string

                for letter in _string.ascii_uppercase:
                    root = f"{letter}:\\"
                    if os.path.exists(root):
                        drives.append({"name": root, "path": root})
            except OSError:  # pragma: no cover — 防御
                pass
            result["drives"] = drives
        try:
            entries = sorted(os.scandir(target), key=lambda e: e.name.lower())
        except OSError as exc:
            if not os.path.exists(target):
                # 目录尚不存在（如平台默认工作区）：回退到最近的已存在上级
                ancestor = target
                while ancestor and not os.path.exists(ancestor):
                    parent = os.path.dirname(ancestor)
                    if parent == ancestor:
                        break
                    ancestor = parent
                if ancestor and os.path.isdir(ancestor) and ancestor != target:
                    return self.list_fs(ancestor, include_files=include_files)
            result["ok"] = False
            result["error"] = str(exc)
            return result
        dirs: List[Dict[str, str]] = []
        files: List[Dict[str, str]] = []
        for entry in entries:
            if entry.name.startswith("."):
                continue  # 隐藏条目不进入选择框
            try:
                if entry.is_dir():
                    dirs.append({"name": entry.name, "path": entry.path})
                elif include_files and entry.is_file():
                    files.append({"name": entry.name, "path": entry.path})
            except OSError:
                continue
            if len(dirs) + len(files) >= 500:
                break
        parent = os.path.dirname(target)
        if parent and parent != target:
            result["parent"] = parent
        result["dirs"] = dirs
        result["files"] = files
        return result

    def read_fs_file(self, path: str) -> Dict[str, Any]:
        """读取本地文本文件内容（浏览器宿主的 pick_file 配套能力）。"""
        p = os.path.abspath(os.path.expanduser(path or ""))
        try:
            if os.path.isdir(p):
                return {"ok": False, "error": "目标是一个目录"}
            if os.path.getsize(p) > 2 * 1024 * 1024:
                return {"ok": False, "error": "文件过大（最大 2MB）"}
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                return {"ok": True, "content": f.read()}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    # ── 文件上传 ───────────────────────────────────────

    def upload_files(self, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把前端 dataURL 文件解码为文本内容（二进制不支持）。"""
        out: List[Dict[str, Any]] = []
        for f in files or []:
            name = str(f.get("name") or "file")
            ftype = str(f.get("type") or "")
            data = str(f.get("data") or "")
            try:
                if "," in data:
                    data = data.split(",", 1)[1]
                raw = base64.b64decode(data)
                if len(raw) > _MAX_UPLOAD_FILE:
                    out.append({"name": name, "type": ftype,
                                "error": "file too large (max 10MB)"})
                    continue
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    out.append({"name": name, "type": ftype,
                                "error": "binary file not supported"})
                    continue
                out.append({"name": name, "type": ftype, "content": text})
            except Exception as exc:  # noqa: BLE001
                out.append({"name": name, "type": ftype, "error": str(exc)})
        return out

    # ── 内部 ───────────────────────────────────────────

    def _publish(self, item: dict) -> None:
        with self._lock:
            self._history.append(item)
            if len(self._history) > self.history_limit:
                self._history = self._history[-self.history_limit:]
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(item)
            except queue.Full:
                pass

    def _register_subscriber(self, q: "queue.Queue[dict]") -> None:
        with self._lock:
            self._subscribers.append(q)

    def _unregister_subscriber(self, q: "queue.Queue[dict]") -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _recent_history(self) -> List[dict]:
        with self._lock:
            return list(self._history[-200:])

    def shutdown(self) -> None:
        """停止 HTTP 服务并断开全部订阅者（幂等，可跨线程调用）。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for q in self._subscribers:
                try:
                    q.put_nowait({"type": "notify", "message": "server closed",
                                  "ts": time.time(), "sid": None})
                except queue.Full:
                    pass
            self._subscribers.clear()
            server = self._server
            self._server = None
        if server is None:
            return
        # server.shutdown() 必须在 serve_forever 线程之外调用，
        # 否则死锁（防御：同线程时只关底层 socket）。
        if self._thread is not None and threading.current_thread() is self._thread:
            try:
                server.server_close()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            server.shutdown()
        except Exception:  # noqa: BLE001 — 可能已被服务线程关闭
            pass
        try:
            server.server_close()
        except Exception:  # noqa: BLE001
            pass


def _package_version() -> str:
    try:
        import norpagent

        return getattr(norpagent, "__version__", "?")
    except Exception:  # noqa: BLE001
        return "?"


__all__ = ["WebUI", "json_safe"]
