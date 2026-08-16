# Copyright (c) 2026 xingluosama121, MIT Licensed
"""上下文管理组件包：超长上下文的可搜索知识库。

- ``FTS5ContextStore``：SQLite FTS5 实现（零依赖，默认组件名 "fts5"）；
- 替换方式与一切组件相同：实现同一接口后
  ``registry.register_component("context_store", "我的实现", factory)``。

配套工具见 norpagent.builtin.tools.context_tools。
"""

from norpagent.builtin.context.fts5 import FTS5ContextStore

__all__ = ["FTS5ContextStore"]
