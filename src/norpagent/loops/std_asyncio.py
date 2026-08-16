# Copyright (c) 2026 xingluosama121, MIT Licensed
"""标准 asyncio 循环适配器：async_loop 槽位的默认实现。

独立线程跑一个 asyncio 事件循环；submit() 把同步函数交给
循环的线程池执行并阻塞等待结果。对上层完全透明——
想替换成自研事件循环系统（例如 nasync_io 移植版）时，
只需实现 LoopRuntime 协议并填入 async_loop 槽位地址。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Optional


class StdLoopRuntime:
    """基于标准库 asyncio 的 LoopRuntime 实现。"""

    name = "std_asyncio"

    def __init__(self, **kwargs: Any) -> None:
        # 不消费 kwargs 也能接上架构层工厂上下文（config 里可能
        # 带循环专属配置，这里有意忽略——默认循环不需要配置）。
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._closed = threading.Event()

    # ── LoopRuntime 协议 ──────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return  # 已启动
            self._loop = asyncio.new_event_loop()
            self._closed.clear()
            self._thread = threading.Thread(
                target=self._loop.run_forever,
                name="norpagent-std-loop",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        self._closed.set()

    def is_running(self) -> bool:
        loop = self._loop
        return bool(
            loop is not None
            and loop.is_running()
            and self._thread is not None
            and self._thread.is_alive()
            and not self._closed.is_set()
        )

    def join(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if not thread.is_alive():
                # 循环线程退出后关闭循环资源
                loop = self._loop
                if loop is not None and not loop.is_closed():
                    loop.close()

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        loop = self._loop
        if loop is None:
            raise RuntimeError("循环尚未 start()")
        # 已在循环线程内：直接同步执行（避免死等）
        if threading.current_thread() is self._thread:
            return fn(*args, **kwargs)
        if not self.is_running():
            raise RuntimeError("循环已停止")
        # 跨线程执行：结果由 executor 线程直接写入本地盒子并置位事件，
        # 不依赖 asyncio Future 的 done-callback 调度时序
        # （跨线程 add_done_callback 在 future 已完成时存在竞态：
        #  call_soon 不写自管道，loop 阻塞在 selector 上会收不到唤醒）。
        box: dict = {}
        done = threading.Event()

        def _runner() -> None:
            try:
                box["ok"] = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 — 异常传回调用方
                box["exc"] = exc
            finally:
                done.set()

        loop.run_in_executor(None, _runner)
        try:
            done.wait()
            if "exc" in box:
                raise box["exc"]
            return box["ok"]
        except KeyboardInterrupt:  # pragma: no cover
            raise

    # ── 附加能力 ─────────────────────────────────────────

    def run_async(self, coro: Any) -> Any:
        """在循环内执行协程并阻塞返回其结果（可选能力）。

        供「以协程形态编写的自定义入口」使用；引擎默认走 submit()。
        """
        loop = self._loop
        if loop is None:
            raise RuntimeError("循环尚未 start()")
        if threading.current_thread() is self._thread:
            # 已在循环线程内：注册 task 后原地等待其完成
            task = loop.create_task(coro)
            done = threading.Event()

            def _on_done(_: Any) -> None:
                done.set()

            task.add_done_callback(_on_done)
            while not done.is_set() and loop.is_running():
                done.wait(0.05)
            if task.cancelled():
                raise RuntimeError("协程任务被取消")
            exc = task.exception()
            if exc is not None:
                raise exc
            return task.result()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        # 跨线程等待：轮询 done 状态（避免跨线程 add_done_callback 竞态）
        while not fut.done():
            threading.Event().wait(0.005)
        exc = fut.exception()
        if exc is not None:
            raise exc
        return fut.result()


__all__ = ["StdLoopRuntime"]
