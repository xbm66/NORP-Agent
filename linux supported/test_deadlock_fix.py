# -*- coding: utf-8 -*-
"""死锁修复回归测试：验证跨线程 provide_user_input / stop() 能立即唤醒
阻塞在 _wait_for_user_input() 上的 Agent 协程（修复前会挂起 30 分钟）。"""
import asyncio
import threading
import time

from async_loop import AsyncAgentLoop
from event_queue import EventQueue


def make_loop():
    return AsyncAgentLoop(
        api_key="test-key",
        project_root=".",
        event_queue=EventQueue(),
        app_dir="",
        model="test-model",
    )


def test_provide_user_input_wakes_waiter():
    """前端输入应秒级唤醒等待中的 Agent（修复前卡 30 分钟超时）。"""
    agent = make_loop()
    result = {}

    async def waiter():
        result["reply"] = await agent._wait_for_user_input()

    def run_agent():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        agent._agent_loop = loop  # 模拟 run() 中记录事件循环
        loop.run_until_complete(waiter())
        loop.close()

    t = threading.Thread(target=run_agent)
    t.start()
    time.sleep(1.0)  # 确保协程已进入 await

    t0 = time.time()
    agent.provide_user_input("__confirm__")  # 模拟 pywebview 桥接线程
    t.join(timeout=5)
    elapsed = time.time() - t0

    assert result.get("reply") == "__confirm__", f"reply={result.get('reply')}"
    assert not t.is_alive(), "FAIL: 协程未被唤醒（死锁仍在）"
    print(f"  [PASS] provide_user_input 在 {elapsed:.2f}s 内唤醒等待协程")


def test_stop_wakes_waiter():
    """用户点击停止应秒级唤醒等待中的 Agent。"""
    agent = make_loop()
    result = {}

    async def waiter():
        result["reply"] = await agent._wait_for_user_input()

    def run_agent():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        agent._agent_loop = loop
        loop.run_until_complete(waiter())
        loop.close()

    t = threading.Thread(target=run_agent)
    t.start()
    time.sleep(1.0)

    t0 = time.time()
    agent.stop()  # 模拟 JS 桥接线程调用 stop
    t.join(timeout=5)
    elapsed = time.time() - t0

    assert not t.is_alive(), "FAIL: stop() 未唤醒等待协程（死锁仍在）"
    print(f"  [PASS] stop() 在 {elapsed:.2f}s 内唤醒等待协程")


if __name__ == "__main__":
    print("=== Deadlock Fix Regression Tests ===")
    test_provide_user_input_wakes_waiter()
    test_stop_wakes_waiter()
    print("\n=== All Deadlock Tests Passed ===")
