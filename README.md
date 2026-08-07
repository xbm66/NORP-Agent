# 🧠 NORP Agent — AI 编程助手桌面客户端

> Vibe Coding Agent · 由 DeepSeek 驱动的本地 AI 编程智能体


NORP Agent 是一款运行在 Windows 上的桌面 AI 编程助手。它连接 DeepSeek 大语言模型，采用 **ReAct（推理+行动）** 架构，能自动理解你的自然语言指令，主动读取项目文件、编写代码、搜索内容、管理笔记，完成复杂的编程任务——你只需说出需求，它会自己动手。

---

## ✨ 功能亮点

| 类别 | 能力 |
|------|------|
| 💬 **自然语言编程** | 用中文/英文/其他支持的语言描述需求，Agent 自动规划、执行、修复 |
| 📁 **文件操作** | 读取、写入、替换、删除文件；支持按行范围精准读取大文件 |
| 🔍 **智能搜索** | 官方插件实现，代码库全文索引检索（SQLite FTS5）、超大文件流式搜索（最高 1GB+） |
| 🔧 **分子手术刀** | 官方插件实现，对超大文件精确替换单行，无需加载整个文件 |
| 📝 **笔记系统** | 官方插件实现，保存/搜索笔记，支持标签分类，自动时间戳 |
| 📄 **Office 文档** | 官方插件实现，读取/生成 Word (.docx)、Excel (.xlsx)、PowerPoint (.pptx) |
| ⏱️ **性能测试** | 官方插件实现，代码压力测试、基准对比，含 P95/P99 统计 |
| 🔌 **插件系统** | 热加载外部 Python 插件，扩展 Agent 能力 |
| 🛡️ **安全沙箱** | 文件操作隔离、权限级联审批、插件安全审计 |
| 🧵 **异步架构** | 多会话并行、沙箱池化复用、僵尸任务自动回收 |
| 🎛️ **高度可配** | 模型选择、温度、思考深度、Token 上限、记忆模式等 |
| 📟 **现代化 UI** | 基于 pywebview 的桌面窗口，联网后支持 Markdown 渲染、LaTeX 数学公式 |

---

## 🚀 快速开始

### 环境要求

- **操作系统**：Windows 10/11 x64
- **Python**：3.10 或更高版本
- **API Key**：[DeepSeek API Key](https://platform.deepseek.com/)（支持 deepseek-v4-pro / deepseek-v4-flash）
- （本地模式下不需要配置API Key）

### 安装与运行

在GitHub的Release页中下载最新的exe文件并双击运行

---

## 🧩 插件系统

NORP Agent 拥有强大的插件架构，11 个官方插件开箱即用：

| 插件 | 功能 |
|------|------|
| `clipboard_manager` | 系统剪贴板读写与历史记录 |
| `code_reviewer` | 代码质量审查（文档、异常、复杂度、命名等） |
| `context_retriever` | 对话上下文 BM25 检索索引 |
| `dev_utilities` | UUID 生成、密码生成、哈希计算、时间戳转换 |
| `doc_reader` | Word / Excel / PowerPoint 文档内容提取 |
| `file_searcher` | 工作区文件索引与毫秒级全文搜索 |
| `file_surgeon` | 「分子手术刀」——超大文件精确行级编辑 |
| `note_manager` | 本地笔记存取与全文搜索 |
| `office_writer` | 生成 Word / Excel / PowerPoint 文档 |
| `stress_tester` | 代码性能基准测试与压力测试 |
| `time_tracker` | 会话耗时追踪与生产力报告 |

你也可以[编写自己的插件](PLUGIN_DEVELOPMENT_GUIDE.md)来扩展 Agent 的能力。

---

## 🏗️ 项目架构

```
norp-agent/
├── main.py                  # 程序入口，pywebview 窗口创建
├── api.py                   # 前端 JS ↔ Python API 桥接层
├── async_loop.py            # 异步 Agent 循环（ReAct 架构）
├── config.py                # 配置管理（加密存储、模型、参数）
├── tools.py                 # 内置工具定义（OpenAI Function Calling Schema）
├── executor.py              # 工具执行分发
├── async_executor.py        # 异步工具执行器
├── sandbox_pool.py          # 沙箱池（文件操作隔离）
├── lifecycle_manager.py     # 会话生命周期管理 & 僵尸扫描
├── resource_isolator.py     # 资源隔离层（文件/网络/进程）
├── permission_cascade.py    # 权限级联审批链
├── event_queue.py           # 事件队列（SSE 风格流式推送）
├── path_mapper.py           # 路径映射（虚拟 ↔ 真实路径）
├── agent_shared.py          # 公共工具函数
├── front.html               # 前端 UI（纯 HTML/CSS/JS，无框架）
├── plugin_system/           # 插件运行时
│   ├── manager.py           #   插件加载/热重载/钩子调度
│   ├── context.py           #   插件上下文（访问 Agent 状态）
│   └── security.py          #   插件安全审计
├── official_plugins/        # 11 个官方插件
└── static/                  # 静态资源
```

---

## ⚙️ 配置说明

所有配置项可通过 UI 设置面板调整，主要参数包括：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `model` | `deepseek-v4-pro` | 模型选择（pro / flash） |
| `temperature` | `1.0` | 生成温度（0~2） |
| `think_level` | `高` | 思考深度（低/中/高） |
| `max_steps` | `128` | 单任务最大 ReAct 步数 |
| `max_tokens` | `32767` | 单次响应最大 Token 数 |
| `task_timeout` | `0`（不限） | 任务超时秒数 |
| `memory` | `false` | 是否启用跨任务记忆 |
| `plugins_enabled` | `true` | 是否启用插件系统 |



---

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

Copyright © 2026 **xingluosama (NORP Studio)**

---

## 🙏 致谢

- [DeepSeek](https://www.deepseek.com/) — 强大的大语言模型 API
- [pywebview](https://pywebview.flowrl.com/) — 轻量级桌面 GUI 框架
- [KaTeX](https://katex.org/) — 快速数学公式渲染
- [marked](https://marked.js.org/) — Markdown 解析器
