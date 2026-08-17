# Copyright (c) 2026 xingluosama121, MIT Licensed
"""Web 前端：HTTP + SSE 界面（front.html 多宿主前端，零依赖）。

默认前端（norpagent() 不带参数时自动使用）：
- 启动一个零依赖 HTTP 服务，控制台打印 listening on 地址；
- 浏览器打开 http://127.0.0.1:<port>/ 即为完整聊天界面
  （front.html：多标签会话 / 流式渲染 / 设置 / 插件面板）；
- 任务经 /chat 提交、事件经 SSE 推送（事件翻译由页面内 bridge 完成）。

常用参数（np() 关键字或 config={"web": {...}}）：
    port         端口（默认 8787）
    host         监听地址（默认 127.0.0.1）
    open_browser 是否自动打开浏览器（默认 False）
    language     界面语言（默认 "en"，如 "zh_CN"）

停止方式：页面「退出程序」按钮、np.stop() 轮询生命周期、
或 np.shutdown() / 引擎 request_stop()。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from norpagent.frontends.base import Frontend


class WebFrontend:
    """HTTP + SSE 前端外壳（页面 = front.html）。"""

    frontend_id = "web"

    def __init__(
        self,
        port: int = 8787,
        host: str = "127.0.0.1",
        open_browser: bool = False,
        **kwargs: Any,
    ) -> None:
        # 架构层工厂可能注入 config dict（如 np(config={"web": {...}})）
        cfg = kwargs.get("config") or {}
        if isinstance(cfg, dict):
            port = cfg.get("port", port)
            host = cfg.get("host", host)
            open_browser = bool(cfg.get("open_browser", open_browser))
        self.port = int(port)
        self.host = str(host)
        self.open_browser = bool(open_browser)
        self._engine: Optional[Any] = None
        self._ui: Optional[Any] = None
        self._gate = threading.Lock()

    def attach(self, engine: Any) -> None:
        from norpagent.builtin.ui.web import WebUI

        self._engine = engine
        # 运行时参数透传（np(port=..., language=...) 等非槽位键）
        params: Dict[str, Any] = dict(getattr(engine, "params", None) or {})
        self.port = int(params.get("port", self.port))
        self.host = str(params.get("host", self.host))
        if "open_browser" in params:
            self.open_browser = bool(params["open_browser"])
        language = str(params.get("language") or "en")

        self._ui = WebUI(
            port=self.port, host=self.host, language=language,
        )
        self._ui.set_handler(self._handle_task)
        self._ui.attach_runtime(engine.agent)
        self._ui.set_config_apply(self._apply_config)
        self._ui.set_quit_callback(
            lambda: engine.request_stop()
        )
        self._ui.set_engine_state_fn(lambda: getattr(engine, "state", None).value)

        # 让 Agent 运行时的 ctx.ui 指向本实例：人工审批 / 澄清提问
        # 才能经 SSE 推送到浏览器（否则会落到注册表单例 WebUI 上，
        # 提问永远无人应答）。替换时退订旧的静默监听。
        agent = engine.agent
        if agent is not None:
            old_listener = getattr(agent, "_ui_listener", None)
            if old_listener is not None and old_listener is not self._ui.on_event:
                try:
                    engine._bus.unsubscribe(old_listener)
                except Exception:  # noqa: BLE001
                    pass
            agent.ui = self._ui
            agent._ui_listener = self._ui.on_event
            engine._bus.subscribe(self._ui.on_event)
        engine.subscribe_ui(self._ui)

    def _handle_task(self, prompt_text: str, session_id: Optional[str],
                     task_params: Optional[Dict[str, Any]] = None) -> Any:
        # 同一运行时串行执行任务
        with self._gate:
            return self._engine.submit(
                prompt_text, session_id=session_id, task_params=task_params
            )

    def _apply_config(self, cfg: Dict[str, Any]) -> None:
        """页面保存配置后应用：模型 / 远端地址 / API Key / 插件目录 / 安全。"""
        engine = self._engine
        if engine is None or cfg is None:
            return
        reg = engine.registry
        agent = engine.agent
        model = str(cfg.get("model") or "")
        api_base = str(cfg.get("api_base") or "") or None
        api_key = str(cfg.get("api_key") or "") or None

        # ── 模型：注册表适配器名 / 远端模型名 两种语义 ──
        if model in reg.list_models():
            if model in ("openai_compat", "anthropic"):
                try:
                    if model == "openai_compat":
                        from norpagent.builtin.models.openai_compat import OpenAICompatProvider

                        reg.register_model("openai_compat", OpenAICompatProvider(
                            model_name=None, base_url=api_base, api_key=api_key,
                        ))
                    else:
                        from norpagent.builtin.models.anthropic import AnthropicProvider

                        reg.register_model("anthropic", AnthropicProvider(
                            model_name=None, api_key=api_key,
                        ))
                except Exception:  # noqa: BLE001 — 参数不完整时保留原 provider
                    pass
            # 其它已注册适配器名：无需动作
        elif model:
            # 远端模型名：挂到 openai_compat 适配器上
            try:
                from norpagent.builtin.models.openai_compat import OpenAICompatProvider

                reg.register_model("openai_compat", OpenAICompatProvider(
                    model_name=model, base_url=api_base, api_key=api_key,
                ))
            except Exception:  # noqa: BLE001
                pass
            model = "openai_compat"

        # 让运行时下一次 run() 使用新模型
        if agent is not None and model:
            preset = getattr(agent, "preset", None)
            if preset is not None:
                try:
                    preset.model = model
                except Exception:  # noqa: BLE001
                    pass

        # ── NORP 安全：开启时安装（一句话开启全套安全） ──
        if cfg.get("norp_safe_enabled") and getattr(reg, "security", None) is None:
            try:
                from norpagent import safe

                safe(reg, level="standard")
            except Exception:  # noqa: BLE001
                pass

        # ── 插件目录：重新安装（签名→审计→导入限制管线） ──
        dirs = cfg.get("plugin_dirs") or []
        if dirs:
            try:
                from norpagent.plugins import install_plugin_dirs

                install_plugin_dirs(reg, [str(d) for d in dirs], config={
                    "plugin_security_audit": cfg.get("plugin_security_audit") or "warn",
                    "plugin_signature_verify": True,
                })
            except Exception:  # noqa: BLE001
                pass

    def start(self) -> None:
        if self._ui is not None:
            self._ui.start()
            self.port = int(self._ui.port)
            print(f"[norpagent] listening on http://{self.host}:{self.port}/")
            if self.open_browser:
                try:
                    import webbrowser

                    threading.Thread(
                        target=lambda: webbrowser.open(
                            f"http://{self.host}:{self.port}/"
                        ),
                        daemon=True,
                        name="norpagent-webui-browser",
                    ).start()
                except Exception:  # noqa: BLE001 — 无图形环境静默
                    pass

    def stop(self) -> None:
        if self._ui is not None:
            try:
                self._ui.shutdown()
            except Exception:  # noqa: BLE001
                pass

    def is_alive(self) -> bool:
        return bool(self._ui is not None and getattr(self._ui, "_running", True))


__all__ = ["WebFrontend"]
