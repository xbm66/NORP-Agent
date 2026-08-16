# Copyright (c) 2026 xingluosama121, MIT Licensed
"""槽位装配器：把架构层槽位实现装配为注册表 + 预设覆盖。

ArchLayer 连接完成后，本模块把每个槽位的实现「安装」进
注册表与预设声明，产出三个对象：

    (registry, preset, extras)

- registry：已安装内置组件、预设、插件与安全策略的注册表；
- preset：应用槽位覆盖后的最终预设对象（可直接交给 AgentRuntime）；
- extras：非注册表对象（logger / storage / error_handler 等），
  由引擎（runtime.engine）消费。

默认逻辑全部走「预设声明」，即槽位不填 = 预设说了算；
槽位填了 = 覆盖预设（地址直接接上）。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from norpagent.builtin import install_defaults
from norpagent.kernel.presets import Preset
from norpagent.kernel.registry import ComponentError, Registry
from norpagent.modes import register_all_presets

# 预设字段名 → 注册表注册方法映射（能力组件槽位）
_FIELD_SLOTS = {
    "session": ("register_session", "build_session"),
    "sandbox": ("register_sandbox", "build_sandbox"),
    "scheduler": ("register_scheduler", "build_scheduler"),
}

# 视为「已配置模型凭据」的环境变量
_CREDENTIAL_ENV_KEYS = (
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "DASHSCOPE_API_KEY",
    "NORPAGENT_API_KEY",
)


def _has_model_credentials(params: Dict[str, Any]) -> bool:
    """判断是否已提供模型凭据（参数或环境变量）。"""
    if params.get("api_key"):
        return True
    return any(os.environ.get(key) for key in _CREDENTIAL_ENV_KEYS)


def _apply_model_options(reg: Registry, overrides: Dict[str, Any],
                         params: Dict[str, Any]) -> None:
    """把 model_name / base_url / api_key 快捷参数应用到模型适配器。

    当最终使用的模型是内置适配器名（openai_compat / anthropic）时，
    用这些参数重新构造提供者并覆盖注册，使 np() 与 CLI 行为一致：
    np(model="openai_compat", model_name="deepseek-v4-flash", api_key="...")。
    """
    model_name = overrides.get("model")
    if model_name not in ("openai_compat", "anthropic"):
        return
    kwargs = {
        k: params[k]
        for k in ("model_name", "base_url", "api_key")
        if params.get(k) is not None
    }
    if not kwargs:
        return
    if model_name == "openai_compat":
        from norpagent.builtin.models.openai_compat import OpenAICompatProvider

        reg.register_model("openai_compat", OpenAICompatProvider(**kwargs))
    else:
        from norpagent.builtin.models.anthropic import AnthropicProvider

        reg.register_model("anthropic", AnthropicProvider(**kwargs))


def _register_instance_or_factory(reg: Registry, kind: str, name: str,
                                  value: Any) -> None:
    """把「实例 / 工厂 / 类」注册为具名组件。

    - callable（类 / 工厂函数）→ 直接作为工厂注册；
    - 非 callable 实例 → 包装为返回同一实例的工厂。
    """
    if callable(value):
        factory = value
    else:
        factory = lambda: value  # noqa: E731
    if kind == "session":
        reg.register_session(name, factory)
    elif kind == "sandbox":
        reg.register_sandbox(name, factory)
    elif kind == "scheduler":
        reg.register_scheduler(name, factory)
    else:  # pragma: no cover — 防御
        raise ComponentError(f"未知组件种类 {kind}")


def build_registry(layer: Any,
                   params: Optional[Dict[str, Any]] = None) -> Tuple[Registry, Preset, Dict[str, Any]]:
    """装配注册表与最终预设。"""
    params = params or {}
    reg = Registry()
    install_defaults(reg)
    register_all_presets(reg)

    # ── preset 槽位：决定基线预设 ──
    preset_value = layer.get("preset")
    if isinstance(preset_value, Preset):
        reg.register_preset(preset_value)
        base_name = preset_value.name
    elif isinstance(preset_value, str):
        base_name = preset_value
    elif preset_value is None:
        base_name = "standard"  # 默认逻辑：功能完整的标准预设（全量工具）
    else:
        raise ComponentError(f"preset 槽位需要预设名或 Preset 实例，收到 {type(preset_value)}")
    base: Preset = reg.resolve_preset(base_name)

    overrides: Dict[str, Any] = {}
    components: Dict[str, str] = dict(base.components or {})
    extras: Dict[str, Any] = {}

    # ── 模型 ──
    model = layer.get("model")
    if model is not None:
        if isinstance(model, str):
            if model in reg.list_models():
                overrides["model"] = model  # 已注册名引用
            else:
                # 字符串不是已注册名 → 按模块地址解析
                from norpagent.arch.address import resolve_address

                reg.register_model("_arch_model",
                                   resolve_address(model, slot="model"))
                overrides["model"] = "_arch_model"
        else:
            reg.register_model("_arch_model", model)
            overrides["model"] = "_arch_model"
    elif base.model == "openai_compat" and not _has_model_credentials(params):
        # 未指定模型、且无任何 API 凭据时回落 mock：
        # 工具集完整（模型知道全部可用工具），回复为引导性提示，
        # 用户在前端配置模型 + Key 后即切换到真实模型。
        overrides["model"] = "mock"

    # ── 模型快捷参数（与 CLI --model-name/--base-url/--api-key 对齐） ──
    if any(k in params for k in ("model_name", "base_url", "api_key")):
        _apply_model_options(reg, overrides, params)

    # ── 工具集 ──
    tools = layer.get("tools")
    if tools is not None:
        if isinstance(tools, dict):
            for tname, tool in tools.items():
                reg.register_tool(tname, tool)
            overrides["tools"] = list(tools.keys())
        elif isinstance(tools, (list, tuple)):
            if tools and all(isinstance(x, str) for x in tools):
                overrides["tools"] = list(tools)  # 名字引用
            else:
                names = []
                for tool in tools:
                    tname = getattr(tool, "name", "") or tool.__class__.__name__
                    reg.register_tool(tname, tool)
                    names.append(tname)
                overrides["tools"] = names
        else:  # 单个 Tool 实例
            tname = getattr(tools, "name", "") or tools.__class__.__name__
            reg.register_tool(tname, tools)
            overrides["tools"] = [tname]

    # ── 会话 / 沙箱 / 调度器 ──
    for slot in ("session", "sandbox", "scheduler"):
        value = layer.get(slot)
        if value is None:
            continue
        if isinstance(value, str):
            if value in getattr(reg, f"list_{slot}s")():
                overrides[slot] = value  # 已注册名字引用
            else:
                # 字符串不是已注册名 → 按模块地址解析
                from norpagent.arch.address import resolve_address

                _register_instance_or_factory(
                    reg, slot, f"_arch_{slot}",
                    resolve_address(value, slot=slot),
                )
                overrides[slot] = f"_arch_{slot}"
        else:
            _register_instance_or_factory(reg, slot, f"_arch_{slot}", value)
            overrides[slot] = f"_arch_{slot}"

    # ── UI 渲染适配器 ──
    ui = layer.get("ui")
    if ui is not None:
        reg.register_ui("_arch_ui", ui)
        overrides["ui"] = "_arch_ui"
        extras["ui_adapter"] = ui

    # ── 上下文库 / 项目管理（通用组件命名空间） ──
    for slot, kind in (("context_store", "context_store"),
                       ("project_manager", "project_manager")):
        value = layer.get(slot)
        if value is None:
            continue
        factory = value if callable(value) else (lambda v=value: v)
        reg.register_component(kind, f"_arch_{slot}", factory)
        components[kind] = f"_arch_{slot}"
        extras[slot] = value

    # ── 钩子扩展 ──
    hooks = layer.get("hooks")
    if hooks is not None:
        if callable(hooks) and not isinstance(hooks, dict):
            hooks = hooks(reg)
        if isinstance(hooks, dict):
            for hook_name, fn in hooks.items():
                reg.bus.subscribe(fn, hook_name)

    # ── 安全 ──
    security = layer.get("security")
    if security is not None:
        if isinstance(security, str):
            from norpagent import safe

            safe(reg, level=security)
        elif callable(security):
            security(reg)
        else:  # SecurityContext / 其它对象：直接安装
            reg.security = security

    # ── 外部插件 ──
    plugins = layer.get("plugins")
    if plugins is not None:
        if callable(plugins) and not isinstance(plugins, (list, tuple)):
            plugins = plugins(reg)
        if isinstance(plugins, (list, tuple)):
            from norpagent.plugins import install_plugin_dirs

            install_plugin_dirs(reg, list(plugins), config={
                "plugin_security_audit": "warn",
                "plugin_signature_verify": True,
            })

    # ── 基础服务槽位 ──
    logger = layer.get("logger")
    extras["logger"] = logger if logger is not None else logging.getLogger("norpagent")
    extras["storage"] = layer.get("storage")
    extras["error_handler"] = layer.get("error_handler")

    # ── 组装最终预设 ──
    final = Preset(
        name=f"{base.name}_arch" if overrides or components != dict(base.components or {})
        else base.name,
        description=base.description,
        model=overrides.get("model", base.model),
        tools=list(overrides.get("tools", base.tools)),
        session=overrides.get("session", base.session),
        sandbox=overrides.get("sandbox", base.sandbox),
        scheduler=overrides.get("scheduler", base.scheduler),
        ui=overrides.get("ui", base.ui),
        mode=base.mode,
        params=dict(base.params),
        components=components,
    )
    reg.register_preset(final)
    return reg, final, extras


def default_loop_factory(layer: Any) -> Any:
    """async_loop 槽位的默认实现（标准 asyncio 适配器）。"""
    from norpagent.loops.std_asyncio import StdLoopRuntime

    return StdLoopRuntime()


def default_frontend_factory(layer: Any, prompt: Optional[str] = None,
                             config: Optional[Dict[str, Any]] = None) -> Any:
    """frontend 槽位的默认实现。

    单次任务（prompt 模式）→ headless（输出打印到 stdout）；
    交互模式 → web（HTTP + SSE 服务 front.html，零依赖）。
    """
    if prompt is not None:
        from norpagent.frontends.headless import HeadlessFrontend

        return HeadlessFrontend()
    cfg = dict(config or {})
    web_cfg = cfg.get("web") if isinstance(cfg.get("web"), dict) else {}
    from norpagent.frontends.web import WebFrontend

    return WebFrontend(**web_cfg)


def mount_defaults(layer: Any, prompt: Optional[str] = None) -> None:
    """把库内置默认逻辑登记为各槽位的默认实现。

    仅在用户未填地址时生效（ArchLayer 优先使用用户地址）。
    """
    layer.set_default("async_loop", default_loop_factory)
    layer.set_default("frontend",
                      lambda ctx: default_frontend_factory(
                          ctx.get("layer"), prompt, ctx.get("config")))
    # agent_runtime 默认实现 = kernel.agent.AgentRuntime，
    # 由引擎直接构造（构造上下文带 registry / preset / task_params）。
    from norpagent.kernel.agent import AgentRuntime

    layer.set_default("agent_runtime", lambda ctx: AgentRuntime)


__all__ = ["build_registry", "mount_defaults"]
