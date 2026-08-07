# 🧠 NORP Agent — AI 编程助手桌面客户端

> **Vibe Coding Agent** · DeepSeek 驱动 · 本地运行的自主 AI 编程智能体

(https://github.com/xingluosama121/NORP-Agent/releases)

NORP Agent 是一款运行在 **Windows** 上的桌面 AI 编程助手，连接 **DeepSeek** 大语言模型，采用 **ReAct（推理 + 行动）** 架构。它能自主理解自然语言指令，**主动**读取项目文件、编写代码、搜索内容、管理笔记——你只需描述需求，它会自己动手完成整个任务。

---

## 📥 下载与安装

### 🚀 免安装 EXE（推荐）

直接下载最新版可执行文件，**无需安装 Python 或任何依赖**：

> 🔗 **[GitHub Releases → 下载 NORP-Agent.exe](https://github.com/xingluosama121/NORP-Agent/releases)**

下载后双击运行即可。首次启动时输入你的 [DeepSeek API Key](https://platform.deepseek.com/)，选择工作区目录，即刻开始编程。

### 🐍 从源码运行

```bash
# 1. 克隆项目
git clone https://github.com/xingluosama121/NORP-Agent.git
cd norp-agent

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动
python main.py
```

> **环境要求**：Windows 10/11 x64 · Python 3.10+

---

## ✨ 核心特性

### 🗂️ 16 标签页多会话系统

像使用浏览器一样管理你的编程任务——**最多同时开启 16 个独立标签页**，每个标签页都是一个完整的工作会话Agent：

| 能力 | 说明 |
|------|------|
| 🔀 **独立工作区** | 每个标签页可绑定不同的项目目录，互不干扰 |
| 💬 **双面板布局** | 每个标签页包含「聊天面板」+「命令输出面板」，对话与执行结果一目了然 |
| ⚡ **并行任务** | 多个标签页可同时运行不同的 AI 任务，互不阻塞 |
| 🔵 **状态指示灯** | 标签页标题旁显示运行状态：绿色脉冲=执行中、蓝色闪烁=等待用户输入、橙色闪烁=等待确认 |
| 💾 **会话持久化** | 标签页切换时自动保存模态框状态（如 ask_user 弹窗），切回后无缝恢复 |
| ⌨️ **快捷键** | `Ctrl+T` 新建标签页，右键标签页弹出上下文菜单 |


### ⚡ 多线程工具调用引擎

NORP Agent 的工具执行不是简单的串行调用，而是一套**深度异步化的并发执行引擎**：

```
用户指令 → ReAct 推理循环 → 决定调用多个工具
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
              read_file   write_file   exec_cmd
                    │           │           │
                    └───────────┼───────────┘
                                ▼
              ┌─────────────────────────────────┐
              │     AsyncToolExecutor            │
              │  ┌─────────────────────────────┐ │
              │  │ SandboxPool (最多8个沙箱)     │ │
              │  │ 异步获取/释放，池化复用        │ │
              │  └─────────────────────────────┘ │
              │  ┌─────────────────────────────┐ │
              │  │ FileIOQueue                 │ │
              │  │ 文件并发访问检测 & 排队        │ │
              │  └─────────────────────────────┘ │
              │  ┌─────────────────────────────┐ │
              │  │ PathMapper                  │ │
              │  │ 宿主 ↔ 沙箱路径映射           │ │
              │  └─────────────────────────────┘ │
              └─────────────────────────────────┘
```

核心模块：

| 模块 | 功能 |
|------|------|
| `AsyncToolExecutor` | 异步工具执行分发中枢，集成所有安全与隔离模块 |
| `SandboxPool` | 沙箱池管理器，最多 **8 个并行沙箱**，异步获取/释放，池化复用 |
| `FileIOQueue` | 文件并发访问检测器，检测到冲突时自动**排队序列化**，避免竞态条件 |
| `PathMapper` | 宿主路径 ↔ 沙箱虚拟路径的双向映射，透明转换 |
| `LifecycleManager` | 会话生命周期管理，**僵尸任务自动扫描回收** |
| `EventQueue` | SSE 风格事件队列，流式推送到前端，确保 UI 实时响应 |

---

### 🔌 插件系统

NORP Agent 拥有一套**高级插件架构**，支持热加载、钩子注入、安全审计：

#### 架构概览

```
┌──────────────────────────────────────────────┐
│               Plugin Manager                  │
│  ┌────────────┐ ┌──────────┐ ┌─────────────┐ │
│  │ 热加载/卸载  │ │ 钩子调度  │ │ 依赖管理     │ │
│  └────────────┘ └──────────┘ └─────────────┘ │
│  ┌──────────────────────────────────────────┐ │
│  │          15 个 Hook 点（4 层）             │ │
│  │                                          │ │
│  │  L1 生命周期: on_agent_init / shutdown   │ │
│  │  L2 任务层:   on_task_start/done/error   │ │
│  │  L3 步骤层:   before/after_step,         │ │
│  │               before/after_tool_call      │ │
│  │  L4 流事件:   on_reasoning/content/event │ │
│  └──────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────┐ │
│  │          PluginContext                    │ │
│  │  插件访问 Agent 状态的受限接口              │ │
│  └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

#### 11 个官方插件

| 插件 | 分类 | 功能描述 |
|------|------|----------|
| `clipboard_manager` | 🖱️ 系统工具 | 系统剪贴板读写、历史记录管理 |
| `code_reviewer` | 🔍 代码质量 | 代码审查：文档字符串、异常处理、复杂度、命名规范、TODO/FIXME、安全隐患 |
| `context_retriever` | 🧠 记忆增强 | 对话上下文 BM25 全文检索索引，支持超长对话的精准回忆 |
| `dev_utilities` | 🛠️ 开发工具 | UUID 生成、安全密码生成、哈希计算、时间戳转换 |
| `doc_reader` | 📄 文档处理 | Word / Excel / PowerPoint 文档内容提取为 Markdown |
| `file_searcher` | 🔎 文件搜索 | 工作区文件 SQLite FTS5 索引，毫秒级全文搜索 |
| `file_surgeon` | 🔧 文件编辑 | 「分子手术刀」— 超大文件精确行级编辑（流式读写，1GB 文件内存 < 50MB） |
| `note_manager` | 📝 笔记管理 | 本地笔记存取、标签分类、全文搜索 |
| `office_writer` | 📊 文档生成 | 生成 Word / Excel / PowerPoint 文档 |
| `stress_tester` | ⏱️ 性能测试 | 代码压力测试与基准对比，含 P95/P99 统计 |
| `time_tracker` | 📈 效率追踪 | 会话耗时追踪与生产力报告 |

> 📖 想编写自己的插件？请参阅 **[插件开发手册](PLUGIN_DEVELOPMENT_GUIDE.md)** 
---

### 🛡️ 多层安全体系

NORP Agent 将安全视为一等公民，构建了 **4 层纵深防御**：

```
┌──────────────────────────────────────────────────┐
│                  第 1 层：权限级联                   │
│  PermissionCascade — 层级权限模型                   │
│  • 子操作继承父级权限并受其约束                        │
│  • 路径白名单/黑名单 + 深度限制                       │
│  • 4 级权限: SYSTEM > TERMINAL > PLUGIN > CHILD     │
└──────────────────┬───────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────┐
│                  第 2 层：沙箱隔离                   │
│  SandboxPool — 多沙箱池化复用                        │
│  • 最多 8 个子进程/Docker 沙箱并行                   │
│  • 路径映射：虚拟路径 ↔ 真实路径                      │
│  • 进程组管理，支持强制终止                           │
│  • 网络隔离（可选 Docker 容器模式）                    │
└──────────────────┬───────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────┐
│                  第 3 层：资源限制                   │
│  ResourceIsolator — CPU/内存/IO/网络配额            │
│  • CPU 时间限制 · 内存上限 512MB                     │
│  • I/O 吞吐限制 · 最大子进程数 16                    │
│  • 超限自动回收                                     │
└──────────────────┬───────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────┐
│                第 4 层：插件安全审计                  │
│  PluginSecurity — AST 源码静态分析                   │
│  • 加载前 AST 检测：eval/exec/open/os.system 等       │
│  • 导入限制：safe 模式（白名单） / strict 模式         │
│  • 清单权限声明：插件必须声明所需能力                  │
│  • 危险模式分级：off → warn → block                  │
└──────────────────────────────────────────────────┘
```

#### 危险操作确认机制

当 AI 尝试执行**删除文件、覆盖文件**等破坏性操作时，系统会弹出确认对话框，**必须由用户手动批准**才能继续——AI 无法绕过。

---

## 🛠️ 内置工具集

Agent 共拥有 **17 个内置工具** 和 **11 个插件扩展工具**，覆盖编程全流程：

### 内置工具（17 个）

| 工具 | 功能 |
|------|------|
| `read_file` | 按行范围精准读取，支持 >100KB 大文件片段读取 |
| `write_file` | 创建/覆盖文件 |
| `replace_in_file` | 精确文本替换，避免重写整个文件 |
| `list_dir` | 列出目录结构 |
| `search_in_files` | 项目内文本搜索 |
| `delete_file` | 删除文件/目录（需用户确认） |
| `exec_cmd` | 执行 Shell 命令（自动沙箱隔离） |
| `init_project` | 脚手架初始化新项目 |
| `install_dependency` | 安装项目依赖 |
| `git_commit` | Git 提交（约定式提交格式） |
| `ask_user` | 向用户提问、请求确认 |
| `task_done` | 标记任务完成 |
| `web_search` | 网络搜索 |
| `open_file` | 用系统默认程序打开文件 |
| `read_clipboard` | 读取系统剪贴板 |
| `write_clipboard` | 写入系统剪贴板 |

### 插件扩展工具（11 个）

| 工具 | 功能 |
|------|------|
| `clipboard_read/write/history/clear` | 剪贴板管理套件 |
| `code_review` | 代码质量审查 |
| `index_context / search_context / index_stats / clear_index` | 上下文检索套件 |
| `generate_uuid / generate_password / hash_text / timestamp_convert` | 开发工具套件 |
| `read_docx / read_xlsx / read_pptx` | Office 文档读取套件 |
| `write_docx / write_xlsx / write_pptx` | Office 文档生成套件 |
| `save_note / list_notes / search_notes` | 笔记管理套件 |
| `search_files / find_files / surgical_scan / surgical_replace` | 文件搜索与编辑套件 |
| `stress_test / benchmark_compare` | 性能测试套件 |
| `time_report` | 生产力报告 |
| `index_workspace / workspace_index_status / clear_workspace_index` | 工作区索引套件 |

---

## 🏗️ 项目架构

```
norp-agent/
├── main.py                  # 程序入口，pywebview 桌面窗口
├── api.py                   # 前端 JS ↔ Python API 桥接层
├── async_loop.py            # 异步 Agent ReAct 循环（推理→行动→观察）
├── async_executor.py        # 异步工具执行器（集成所有安全模块）
├── executor.py              # 工具执行分发 + Docker 沙箱支持
├── tools.py                 # 内置工具定义（OpenAI Function Calling Schema）
├── config.py                # 配置管理（加密存储 API Key）
│
├── ═══ 安全系统 ═══
├── sandbox_pool.py          # 沙箱池（最多 8 个，异步池化复用）
├── permission_cascade.py    # 权限级联审批链（4 级层级模型）
├── resource_isolator.py     # 资源隔离器（CPU/内存/IO/网络配额）
├── lifecycle_manager.py     # 会话生命周期 & 僵尸扫描回收
├── file_io_queue.py         # 文件并发访问检测 & 排队
├── path_mapper.py           # 宿主 ↔ 沙箱路径映射
│
├── ═══ 插件系统 ═══
├── plugin_system/
│   ├── manager.py           #   插件加载/热重载/15 个 Hook 调度
│   ├── context.py           #   插件上下文（受限 Agent 状态访问）
│   └── security.py          #   AST 安全审计 + 导入限制 + 资源限制
├── official_plugins/        #   11 个官方插件
│
├── ═══ 前端 UI ═══
├── front.html               # 前端 UI（纯 HTML/CSS/JS，无框架）
│                            #   支持 Markdown 渲染、LaTeX 数学公式
│                            #   16 标签页多会话、暗色/亮色主题
├── static/                  # 静态资源
│
├── event_queue.py           # SSE 风格事件队列
├── agent_shared.py          # 公共工具函数
└── norp_agent.spec          # PyInstaller 打包配置
```

---

## ⚙️ 配置参数

所有配置均可通过 UI 设置面板实时调整：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `model` | `deepseek-v4-pro` | 模型选择（pro / flash） |
| `temperature` | `1.0` | 生成温度（0 ~ 2） |
| `think_level` | `高` | 思考深度（低 / 中 / 高） |
| `max_steps` | `128` | 单任务最大 ReAct 循环步数 |
| `max_tokens` | `32767` | 单次 API 响应最大 Token 数 |
| `task_timeout` | `0`（不限） | 任务超时秒数 |
| `memory` | `false` | 跨任务记忆模式 |
| `plugins_enabled` | `true` | 插件系统开关 |
| `plugin_security_audit` | `block` | 插件安全审计级别（off / warn / block） |
| `plugin_security_import_restrict` | `strict` | 插件导入限制（off / safe / strict） |

---

## 📖 文档

| 文档 | 说明 |
|------|------|
| [用户手册](NORP%20Agent%20用户手册.docx) | 安装、配置、使用指南 |
| [插件开发手册](PLUGIN_DEVELOPMENT_GUIDE.md) | 编写自定义插件的完整指南 |
| [插件编写手册](NORP%20Agent%20插件编写手册.docx) | Word 版插件开发详细文档 |
| [版本对比更新手册](版本对比更新手册.docx) | 各版本功能对比与更新记录 |

---

## 🎯 设计理念

- **自主执行**：不只是一个聊天机器人——Agent 会主动读文件、写代码、搜索、测试，循环迭代直到任务完成
- **安全第一**：任何破坏性操作（删文件、执行命令）都需经过沙箱隔离和用户确认
- **可扩展**：插件系统让任何人都能为 Agent 添加新能力，无需修改核心代码
- **离线友好**：除 API 调用外，所有功能均在本地运行，代码不离你的机器（如果配置了本地部署，可以自动进入本地模式，完全断网的条件下依旧可以使用基础Agent功能）

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
