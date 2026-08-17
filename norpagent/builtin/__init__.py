# Copyright (c) 2026 xingluosama121, MIT Licensed
"""内置组件包：开箱即用的默认组件，全部通过注册表接入。

``install_defaults(registry)`` 注册全部内置组件；
也可按需挑选：只注册你需要的部分，其余用自定义实现替代。

P2 起模型适配器（openai_compat / anthropic）随包注册，其 SDK
按需懒加载：未安装对应 extras 时，注册与列出均正常，只有
实际调用才会给出明确的安装提示。

P3 新增：
- 上下文管理组件（context_store=fts5）+ context_* 工具；
- 项目管理组件（project_manager=basic）+ project_status 工具；
- 持久化任务调度器（scheduler=persistent）+ task_* 工具；
- 池化沙箱（sandbox=pooled）；
- Web UI 适配器（ui=web，零依赖 HTTP + SSE）。
"""

from __future__ import annotations

from typing import Any

from norpagent.builtin.models.mock import MockModelProvider
from norpagent.builtin.models.openai_compat import OpenAICompatProvider
from norpagent.builtin.models.anthropic import AnthropicProvider
from norpagent.builtin.tools import (
    EchoTool,
    GetTimeTool,
    RunPythonTool,
    FileReadTool,
    FileWriteTool,
    FileListTool,
    FileDeleteTool,
    ExecCmdTool,
    WebSearchTool,
    WebFetchTool,
    WebExtractLinksTool,
    ContextAddTool,
    ContextSearchTool,
    ContextListTool,
    ContextDeleteTool,
    ProjectStatusTool,
    TaskSubmitTool,
    TaskListTool,
    TaskStatusTool,
    TaskCancelTool,
)
from norpagent.builtin.context import FTS5ContextStore
from norpagent.builtin.projects import BasicProjectManager
from norpagent.builtin.sessions.memory import MemorySessionManager
from norpagent.builtin.sessions.sqlite import SQLiteSessionManager
from norpagent.builtin.sandboxes.subprocess import SubprocessSandboxProvider
from norpagent.builtin.sandboxes.pooled import PooledSandboxProvider
from norpagent.builtin.scheduler.simple import SimpleTaskScheduler
from norpagent.builtin.scheduler.persistent import PersistentTaskScheduler
from norpagent.builtin.ui.console import ConsoleUI
from norpagent.builtin.ui.web import WebUI


def install_defaults(registry: Any) -> Any:
    """注册全部内置组件到注册表，返回注册表本身（便于链式调用）。"""
    # 模型
    registry.register_model("mock", MockModelProvider())
    registry.register_model("openai_compat", OpenAICompatProvider())
    registry.register_model("anthropic", AnthropicProvider())
    # 工具（基础）
    registry.register_tool("echo", EchoTool())
    registry.register_tool("get_time", GetTimeTool())
    registry.register_tool("run_python", RunPythonTool())
    # 工具（文件：工作区路径安全约束）
    registry.register_tool("file_read", FileReadTool())
    registry.register_tool("file_write", FileWriteTool())
    registry.register_tool("file_list", FileListTool())
    registry.register_tool("file_delete", FileDeleteTool())
    # 工具（命令：沙箱协议）
    registry.register_tool("exec_cmd", ExecCmdTool())
    # 工具（联网：SSRF 防护，零依赖可用）
    registry.register_tool("web_search", WebSearchTool())
    registry.register_tool("web_fetch", WebFetchTool())
    registry.register_tool("web_extract_links", WebExtractLinksTool())
    # 工具（上下文管理：context_store 组件）
    registry.register_tool("context_add", ContextAddTool())
    registry.register_tool("context_search", ContextSearchTool())
    registry.register_tool("context_list", ContextListTool())
    registry.register_tool("context_delete", ContextDeleteTool())
    # 工具（项目管理：project_manager 组件）
    registry.register_tool("project_status", ProjectStatusTool())
    # 工具（长周期任务协作：scheduler）
    registry.register_tool("task_submit", TaskSubmitTool())
    registry.register_tool("task_list", TaskListTool())
    registry.register_tool("task_status", TaskStatusTool())
    registry.register_tool("task_cancel", TaskCancelTool())
    # 会话
    registry.register_session("memory", MemorySessionManager)
    registry.register_session("sqlite", SQLiteSessionManager)
    # 沙箱
    registry.register_sandbox("subprocess", SubprocessSandboxProvider().create)
    registry.register_sandbox("pooled", PooledSandboxProvider().create)
    # 调度
    registry.register_scheduler("simple", SimpleTaskScheduler)
    registry.register_scheduler("persistent", PersistentTaskScheduler)
    # UI
    registry.register_ui("console", ConsoleUI())
    registry.register_ui("web", WebUI())
    # 通用组件（上下文存储 / 项目管理）
    registry.register_component("context_store", "fts5", FTS5ContextStore)
    registry.register_component("project_manager", "basic", BasicProjectManager)
    return registry


__all__ = [
    "install_defaults",
    "MockModelProvider",
    "OpenAICompatProvider",
    "AnthropicProvider",
    "EchoTool",
    "GetTimeTool",
    "RunPythonTool",
    "FileReadTool",
    "FileWriteTool",
    "FileListTool",
    "FileDeleteTool",
    "ExecCmdTool",
    "WebSearchTool",
    "WebFetchTool",
    "WebExtractLinksTool",
    "ContextAddTool",
    "ContextSearchTool",
    "ContextListTool",
    "ContextDeleteTool",
    "ProjectStatusTool",
    "TaskSubmitTool",
    "TaskListTool",
    "TaskStatusTool",
    "TaskCancelTool",
    "FTS5ContextStore",
    "BasicProjectManager",
    "MemorySessionManager",
    "SQLiteSessionManager",
    "SubprocessSandboxProvider",
    "PooledSandboxProvider",
    "SimpleTaskScheduler",
    "PersistentTaskScheduler",
    "ConsoleUI",
    "WebUI",
]
