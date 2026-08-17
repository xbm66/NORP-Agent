# Copyright (c) 2026 xingluosama121, MIT Licensed
"""内置会话管理。

- memory：进程内存储（演示 / 基准测试）；
- sqlite：SQLite 持久化存储（默认 ~/.norpagent/sessions.db），
  零第三方依赖，线程安全，替换方式与 memory 一致。
"""

from norpagent.builtin.sessions.memory import MemorySessionManager
from norpagent.builtin.sessions.sqlite import SQLiteSessionManager

__all__ = ["MemorySessionManager", "SQLiteSessionManager"]
