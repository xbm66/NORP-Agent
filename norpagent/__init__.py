# Copyright (c) 2026 xingluosama121, MIT Licensed
"""norpagent — 可插拔积木式 Agent 框架（模块即入口）。

像搭乐高一样构建 Agent：

    import norpagent as np

    np()                       # 完全按默认逻辑运行一个最简单的 Agent（默认 Web 前端）
    running = True
    while running:
        if np.stop() == True:  # 生命周期函数：应用结束即退出
            running = False

默认前端为 Web（HTTP + SSE，零依赖）：np() 启动后台服务并打印
listening on 地址，主线程用 np.stop() 轮询生命周期即可。
显式使用控制台前端（frontend="norpagent.frontends.console:ConsoleFrontend"）
时，在 Python 交互式解释器（>>> REPL）中自动切换同步模式：
np() 阻塞到用户退出（/exit、exit()、Ctrl+C 或 EOF），无需轮询循环。

换零件只需要「填地址」，核心代码零修改：

    np(preset="standard")                        # 换预设模式
    np(model="openai_compat")                    # 换大脑（模型）
    np(async_loop="myapp.loop:create")           # 换事件循环系统
    np(frontend="norpagent.frontends.web:WebFrontend")  # 换前端（默认即 Web）
    np(session="sqlite", sandbox="pooled")       # 换会话与沙箱

事件循环系统是独立架构函数：

    loop = np.nasyncio()                         # 默认循环（标准 asyncio 适配器）
    loop = np.nasyncio("myapp.nasync:create")    # 地址指向的自定义循环

能力一览：
- 架构层 + 地址函数：除底层最小内核（ArchLayer / 地址解析 /
  注册表 / 事件总线）外，全部组件都是槽位，填地址即可替换；
- 上下文管理：FTS5 上下文库（context_add / context_search / ...）
- 项目管理：project_status（含 git 感知）
- 长周期任务协作：persistent 调度器（task_submit / task_list / ...）
- 沙箱池与 PTC 沙箱执行：pooled 沙箱 / run_python 子进程隔离
- 9 层 29 钩子体系：每个执行结构都是独立 API，可订阅 / 改写 / 否决，
  支持自定义钩子与自定义层（norpagent.hooks）
- 安全系统整体剥离：norpagent.safe() 一句话开启全套安全
- 外部插件：norpagent.plugins（签名 → 审计 → 导入限制 → 注册，
  支持进程级隔离与 PluginSystem 门面）
- 前端体系：console / headless / web / 任意自定义前端
- Web UI：ui="web"（HTTP + SSE，零依赖）
"""

import sys
import types as _types
from typing import Any, Optional

from norpagent.kernel import (
    Registry,
    EventBus,
    EventType,
    AgentEvent,
    Preset,
    load_preset_file,
    RunContext,
    AgentRuntime,
    RunResult,
    ComponentError,
)
from norpagent.builtin import install_defaults
from norpagent.modes import register_all_presets
from norpagent.safe import safe, SafetyKit, SecurityContext
from norpagent import hooks  # noqa: F401  # 9 层钩子体系（子模块 API）
from norpagent.arch import ArchLayer, SlotSpec, SLOT_SPECS  # noqa: F401  # 架构层
from norpagent.loops import nasyncio, LoopRuntime  # noqa: F401  # 事件循环架构函数
from norpagent.runtime import (
    launch,
    current,
    stop,
    submit,
    shutdown,
    is_running,
    NorpEngine,
    EngineState,
    EngineError,
)
from norpagent.frontends import (  # noqa: F401
    Frontend,
    ConsoleFrontend,
    HeadlessFrontend,
    WebFrontend,
)

__version__ = "0.6.9"


# ═══════════════════════════════════════════════════════
#  模块即入口：np() 一键启动 + np.stop() 生命周期轮询
# ═══════════════════════════════════════════════════════

class _NorpAgentModule(_types.ModuleType):
    """让 ``import norpagent as np`` 之后 ``np(...)`` 直接可用。

    只替换模块的 __class__（保留 __path__ 等模块属性，
    子模块导入不受影响），为模块对象挂上 __call__ 与
    便捷方法：np() / np.stop() / np.nasyncio() 等。
    """

    def __call__(self, *args: Any, **kwargs: Any) -> NorpEngine:
        """np(...) = 按架构层装配并启动默认 Agent 应用。

        等价于 norpagent.launch(**kwargs)，返回当前引擎。
        """
        return launch(*args, **kwargs)

    # stop() 由模块级函数遮蔽：模块属性查找优先于类方法，
    # 因此 np.stop() 实际调用的是下方模块级 stop()。
    # 这里再声明同名方法作为文档性说明（不会被使用）。

    def launch(self, **kwargs: Any) -> NorpEngine:
        return launch(**kwargs)

    def nasyncio(self, address: Any = None, **config: Any) -> Any:
        return nasyncio(address, **config)

    def shutdown(self) -> None:
        shutdown()

    def current(self) -> Optional[NorpEngine]:
        return current()

    def submit(self, text: str, session_id: Optional[str] = None) -> Any:
        return submit(text, session_id=session_id)

    @property
    def version(self) -> str:
        return __version__


sys.modules[__name__].__class__ = _NorpAgentModule

__all__ = [
    "__version__",
    # 内核
    "Registry",
    "EventBus",
    "EventType",
    "AgentEvent",
    "Preset",
    "load_preset_file",
    "RunContext",
    "AgentRuntime",
    "RunResult",
    "ComponentError",
    "install_defaults",
    "register_all_presets",
    "safe",
    "SafetyKit",
    "SecurityContext",
    "hooks",
    # 架构层
    "ArchLayer",
    "SlotSpec",
    "SLOT_SPECS",
    "nasyncio",
    "LoopRuntime",
    "Frontend",
    "ConsoleFrontend",
    "HeadlessFrontend",
    "WebFrontend",
    # 运行时（np() 入口）
    "launch",
    "current",
    "stop",
    "submit",
    "shutdown",
    "is_running",
    "NorpEngine",
    "EngineState",
    "EngineError",
]
