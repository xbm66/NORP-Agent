# Copyright (c) 2026 xingluosama121, MIT Licensed
"""NorpEngine：np() 一键启动背后的应用引擎与生命周期状态机。

引擎把「架构层装配结果」运转起来：

    layer.connect() → 注册表 + 预设 + 事件循环 + 前端
                    → Agent 运行时
                    → 后台循环线程 + 前端输入线程

生命周期状态机（与 L1 生命周期钩子对齐）：

    STARTING ──start()──▶ RUNNING ──request_stop()──▶ STOPPING ──▶ STOPPED

- start() 广播 on_agent_init（AgentRuntime 构造钩子）；
- request_stop() / 自然完成 广播 on_agent_shutdown；
- np.stop() 轮询 STOPPED（should_stop() 返回 True）。

引擎与循环系统解耦：全部通过 LoopRuntime 协议交互，
不 import 任何具体循环实现。
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional

from norpagent.arch.layer import call_factory


class EngineState(str, Enum):
    """引擎生命周期状态。"""

    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class EngineError(RuntimeError):
    """引擎状态非法操作。"""


class NorpEngine:
    """norpagent 应用引擎。

    用户一般不需要直接构造；用 ``import norpagent as np; np(...)``。
    """

    def __init__(
        self,
        layer: Any,
        registry: Any,
        preset: Any,
        loop: Any,
        frontend: Any,
        extras: Optional[Dict[str, Any]] = None,
        task_params: Optional[Dict[str, Any]] = None,
        prompt: Optional[str] = None,
    ) -> None:
        self.layer = layer
        self.registry = registry
        self.preset = preset
        self.loop = loop
        self.frontend = frontend
        self.extras: Dict[str, Any] = dict(extras or {})
        self.params: Dict[str, Any] = dict(task_params or {})
        self.prompt = prompt

        self._state = EngineState.STARTING
        self._state_lock = threading.Lock()
        self._agent: Optional[Any] = None
        self._bus = registry.bus
        self._logger = self.extras.get("logger")
        self._error_handler: Optional[Callable] = self.extras.get("error_handler")
        self._extra_listeners: list = []
        self._stop_requested = threading.Event()
        self._last_result: Optional[Any] = None

    # ── 状态 ─────────────────────────────────────────────

    @property
    def state(self) -> EngineState:
        with self._state_lock:
            return self._state

    def _set_state(self, state: EngineState) -> None:
        with self._state_lock:
            self._state = state

    def is_running(self) -> bool:
        return self.state in (EngineState.STARTING, EngineState.RUNNING)

    def should_stop(self) -> bool:
        """np.stop() 的语义：返回 True 表示应用已结束、主循环应退出。"""
        return self.state is EngineState.STOPPED

    # ── 启动 ─────────────────────────────────────────────

    def start(self) -> "NorpEngine":
        """装配 Agent 运行时、启动循环与前端（幂等）。"""
        if self.state is not EngineState.STARTING:
            return self
        self._build_agent()
        # 前端绑定引擎（attach 中会注入渲染器并订阅事件）
        self.frontend.attach(self)
        self.loop.start()
        # 先进入 RUNNING 再启动前端：控制台前端在交互式解释器
        # （REPL）下为同步模式，frontend.start() 会阻塞并在循环内
        # 直接 submit 任务，此时引擎必须已是 RUNNING 状态。
        self._set_state(EngineState.RUNNING)
        self.frontend.start()
        # 单次任务模式：后台线程跑 prompt，完成后自动停止
        if self.prompt is not None:
            threading.Thread(
                target=self._run_prompt_task,
                name="norpagent-prompt-task",
                daemon=True,
            ).start()
        return self

    def _build_agent(self) -> None:
        """按 agent_runtime 槽位构造 Agent 运行时。

        默认实现 = kernel.agent.AgentRuntime；填地址 = 替换循环本体。
        工厂上下文按签名注入：registry / preset / ui / task_params。
        """
        runtime_slot = self.layer.get("agent_runtime")
        ui = None
        try:
            ui = self.registry.resolve_ui(self.preset.ui)
        except Exception:  # noqa: BLE001 — 前端可能自带渲染器
            ui = None
        self._agent = call_factory(
            runtime_slot,
            {
                "registry": self.registry,
                "preset": self.preset,
                "ui": ui,
                "task_params": self.params,
                "layer": self.layer,
                "config": {},
            },
        )

    # ── 输入提交 ─────────────────────────────────────────

    def submit(self, text: str, session_id: Optional[str] = None,
               task_params: Optional[Dict[str, Any]] = None) -> Any:
        """提交一条用户输入给 Agent（在循环上下文执行，阻塞返回结果）。

        ``task_params`` 为任务级参数（覆盖构造级默认，如
        workspace_root / max_steps / _stop_check 等）。
        """
        if self.state is not EngineState.RUNNING:
            raise EngineError(f"引擎未在运行状态（当前 {self.state.value}）")
        agent = self._agent
        if agent is None:
            raise EngineError("Agent 尚未装配")
        return self.loop.submit(
            lambda: agent.run(text, session_id=session_id, task_params=task_params)
        )

    def subscribe_ui(self, renderer: Any) -> None:
        """把渲染器订阅到事件总线（自动去重，避免重复输出）。"""
        on_event = getattr(renderer, "on_event", None)
        if on_event is None:
            return
        if self._agent is not None and getattr(self._agent, "ui", None) is renderer:
            return  # AgentRuntime 构造时已订阅同一实例
        self._bus.subscribe(on_event)
        self._extra_listeners.append(on_event)

    # ── 停止 ─────────────────────────────────────────────

    def request_stop(self) -> None:
        """请求停止（幂等，可从任意线程调用）。"""
        if self._stop_requested.is_set():
            return
        self._stop_requested.set()
        if self.state in (EngineState.STOPPING, EngineState.STOPPED):
            return
        self._set_state(EngineState.STOPPING)
        # 1. 前端停止（输入循环退出）
        try:
            self.frontend.stop()
        except Exception:  # noqa: BLE001
            pass
        # 2. Agent 关闭（释放沙箱 / 组件 / 广播 on_agent_shutdown）
        agent = self._agent
        if agent is not None:
            def _close() -> None:
                try:
                    agent.shutdown()
                except Exception:  # noqa: BLE001
                    pass

            if self.loop.is_running():
                try:
                    self.loop.submit(_close)
                except RuntimeError:
                    _close()
            else:
                _close()
        # 3. 退订引擎额外订阅的渲染器
        for fn in self._extra_listeners:
            try:
                self._bus.unsubscribe(fn)
            except Exception:  # noqa: BLE001
                pass
        self._extra_listeners.clear()
        # 4. 停止循环线程
        try:
            self.loop.stop()
            self.loop.join(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
        self._set_state(EngineState.STOPPED)

    def stop(self, timeout: Optional[float] = None) -> None:
        """请求停止并等待引擎完全退出。"""
        self.request_stop()
        deadline = (time.time() + timeout) if timeout else None
        while self.state is not EngineState.STOPPED:
            if deadline and time.time() > deadline:
                return
            time.sleep(0.01)

    def wait(self, timeout: Optional[float] = None) -> bool:
        """等待引擎自然结束；返回是否已停止。"""
        deadline = (time.time() + timeout) if timeout else None
        while not self.should_stop():
            if deadline and time.time() > deadline:
                return False
            time.sleep(0.01)
        return True

    @property
    def agent(self) -> Any:
        """当前 Agent 运行时（已装配）。"""
        return self._agent

    @property
    def last_result(self) -> Any:
        """最近一次任务的结果（RunResult）。"""
        return self._last_result

    # ── 单次任务 ─────────────────────────────────────────

    def _run_prompt_task(self) -> None:
        try:
            result = self.submit(self.prompt)
            self._last_result = result
            # 无头前端下任务完成必须可见地打印最终结果
            # （修复「headless 模式只有输入看不见输出」）
            if getattr(self.frontend, "frontend_id", "") == "headless":
                status = getattr(result, "status", "done")
                content = getattr(result, "final_content", "") or ""
                if status == "done" and content:
                    print(f"[headless] task result: {content}")
                else:
                    err = getattr(result, "error", "") or ""
                    print(f"[headless] task {status}: {err}")
            self._handle_error_if_needed(result)
        except EngineError:
            pass  # 引擎已停止
        except Exception as exc:  # noqa: BLE001 — 最后防线
            self._handle_exception(exc)
        finally:
            self.request_stop()

    def _handle_error_if_needed(self, result: Any) -> None:
        status = getattr(result, "status", "done")
        if status == "error":
            self._handle_exception(
                RuntimeError(getattr(result, "error", "任务失败"))
            )

    def _handle_exception(self, exc: BaseException) -> None:
        # 解释器退出阶段（主线程结束、daemon 引擎线程仍在运行）发生的
        # 异常无需报告：进程即将消亡，日志无意义。
        # （threading._SHUTTING_DOWN 是 CPython 3.9+ 稳定私有标志，
        #  sys.is_finalizing() 是 3.13+ 官方接口，双保险。）
        import sys as _sys

        finalizing = bool(getattr(threading, "_SHUTTING_DOWN", False))
        finalizer = getattr(_sys, "is_finalizing", None)
        if finalizing or (finalizer is not None and finalizer()):
            return
        if self._error_handler is not None:
            try:
                self._error_handler(exc, self)
                return
            except Exception:  # noqa: BLE001 — 错误处理器自身出错再落日志
                pass
        if self._logger is not None:
            try:
                self._logger.error("norpagent 引擎异常: %s", exc)
            except Exception:  # noqa: BLE001
                pass


__all__ = ["NorpEngine", "EngineState", "EngineError"]
