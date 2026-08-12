# NORP Agent — Vibe Coding 自主编程智能体

> 桌面端 AI 编程助手，采用 ReAct 架构，将自然语言指令转化为精确的代码操作。

[![Version](https://img.shields.io/badge/version-Release%201.0-blue.svg)]()
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2B-lightgrey.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

---

## 🎯 简介

NORP Agent 是一个基于 **pywebview** 的 Windows 桌面应用。它使用大语言模型（DeepSeek / OpenAI / Anthropic / 本地 Ollama）驱动，能够自主读取、搜索、修改项目文件，执行 shell 命令，并安装依赖——全程在沙箱隔离和权限级联的安全约束下运行。

**核心特性：**

- 🧠 **多模型支持** — DeepSeek V4 Pro/Flash、OpenAI、Anthropic、本地 Ollama / LM Studio / vLLM
- 🔧 **30 个内置工具** — 文件读写、代码搜索、超大文件精确编辑、上下文检索、网页抓取、Shell 执行、Git 提交等
- 🧩 **插件系统** — 4 层 16 个钩子 + 8 个官方插件，可无限扩展
- 🛡️ **多层安全** — NORP 安全系统（危险命令拦截 + UAC 提权检测 + 路径越界防护）、越狱注入检测、运行时完整性校验
- 🪟 **原生体验** — 系统托盘最小化、启动 Splash 画面、Windows 凭据管理器加密存储 API Key
- 🌐 **多语言** — 简体中文 / 繁体中文 / English / Русский / 日本語
- 📦 **单文件打包** — PyInstaller 打包为单 exe（约 38 MB），开箱即用

---

## 🚀 快速开始

### 下载运行（推荐）

从 [Releases](../../releases) 下载 `NORP Agent.exe`，双击运行。

首次启动时需配置 API Key（支持 DeepSeek / OpenAI / Anthropic / 本地模型）。

### 开发者安装

```bash
# 1. 克隆仓库
git clone https://github.com/your-username/norp-agent.git
cd norp-agent

# 2. 安装依赖
pip install -r requirements.txt

# 3. 构建前端
python build_front.py

# 4. 启动
python main.py
```

### 本地模型（Ollama）

1. 安装 [Ollama](https://ollama.com)
2. 拉取模型：`ollama pull qwen3`
3. 在设置中 Base URL 填写 `http://localhost:11434/v1`
4. API Key 任意填写（本地模式无需真实 key）

---

## 🏗️ 项目结构

```
norp-agent/
├── main.py                 # 程序入口（Splash、主窗口、系统托盘、懒代理）
├── api.py                  # API 层（Session 管理、插件、安全、统计）
├── tools.py                # 30 个内置工具定义（OpenAI Function Schema）
├── config.py               # 配置管理（API Key 凭据管理器加密存储）
├── agent_shared.py         # 共享函数（提示词构建、消息拼接、工具转换）
│
├── async_loop.py           # ★ 异步 Agent 主循环（主要路径）
├── async_executor.py       # ★ 异步工具执行器（沙箱池/文件队列/权限/安全）
├── loop.py                 # 同步 Agent 循环（旧版，保留兼容）
├── executor.py             # 同步工具执行器（旧版，保留兼容）
├── event_queue.py          # 线程安全事件队列（SSE 风格流式输出）
│
├── lifecycle_manager.py    # 生命周期管理器（进程组、僵尸扫描）
├── sandbox_pool.py         # 沙箱池（8 个进程级隔离沙箱）
├── file_io_queue.py        # 文件 I/O 队列（并发冲突检测与排队）
├── path_mapper.py          # 路径映射器（宿主 ↔ 沙箱路径）
├── permission_cascade.py   # 权限级联（层级权限模型）
├── resource_isolator.py    # 资源隔离器
│
├── norp_safe.py            # NORP 安全系统（危险命令/UAC/路径越界拦截）
├── jailbreak_guard.py      # 越狱/提示词注入检测
├── runtime_check.py        # 运行时完整性检测（核心文件哈希校验）
├── archive_utils.py        # 安全解压缩（Zip Bomb 防御）
│
├── context_index.py        # 上下文检索引擎（BM25 精确检索 + 对话历史索引）
├── workspace_index.py      # 工作区文件搜索引擎（FTS5 全文索引 + 1GB 流式检索）
├── file_surgery.py         # 分子手术刀（超大文件精确行编辑，<50MB 内存）
├── web_fetcher_native.py   # 网页抓取器（内容提取 + 链接发现 + SSRF 防护）
│
├── plugin_system/          # 插件框架核心
│   ├── manager.py          #   插件管理器（发现/加载/钩子分发）
│   ├── context.py          #   插件上下文 & 日志器
│   └── security.py         #   插件安全审计（AST + 导入限制 + 资源限制）
│
├── official_plugins/       # 8 个官方插件
│   ├── clipboard_manager.py    # 剪贴板历史管理
│   ├── code_reviewer.py        # 代码质量审查
│   ├── dev_utilities.py        # 开发工具（UUID/密码/哈希/时间戳）
│   ├── doc_reader.py           # Office 文档读取（.docx/.xlsx/.pptx）
│   ├── note_manager.py         # 本地笔记管理
│   ├── office_writer.py        # Office 文档写入（.docx/.xlsx/.pptx）
│   ├── stress_tester.py        # 压力测试 / 性能基准
│   └── time_tracker.py         # 时间追踪 / 生产力报告
│
├── front_src/              # 前端源码（HTML/CSS/JS 模块）
│   ├── index.html          #   主页面结构
│   ├── styles.css          #   样式表
│   ├── core.js             #   核心状态与 API 通信
│   ├── ui.js               #   UI 渲染与事件绑定
│   ├── i18n.js             #   多语言国际化
│   ├── tabs.js             #   标签页管理
│   ├── wizard.js           #   设置向导
│   └── main.js             #   入口初始化
├── front.html              # 构建后的单文件前端（约 323 KB）
├── build_front.py          # 前端构建脚本
├── norp_agent.spec         # PyInstaller 打包配置
├── requirements.txt        # 依赖清单
└── README.md            # 项目概述
```

---

## 🔧 内置工具一览（30 个）

### 文件操作
| 工具 | 说明 |
|---|---|
| `read_file` | 读取文件内容，支持行范围 |
| `write_file` | 创建或覆盖文件 |
| `replace_in_file` | 精确文本替换 |
| `delete_file` | 删除文件或目录 |
| `copy_file` | 复制文件或目录 |
| `move_file` | 移动/重命名文件或目录 |
| `list_dir` | 列出目录内容 |

### 代码搜索与分析
| 工具 | 说明 |
|---|---|
| `search_in_files` | 全局文本搜索（小型项目） |
| `index_workspace` | 建立 FTS5 全文索引 |
| `search_files` | 毫秒级全文精确检索 |
| `find_files` | 文件名 glob 模糊匹配 |
| `search_large_file` | 超大文件流式检索（最高 1GB+） |
| `workspace_index_status` | 查看索引统计 |
| `clear_workspace_index` | 清理文件索引 |

### 超大文件精确编辑
| 工具 | 说明 |
|---|---|
| `surgical_replace` | 分子手术刀 — 精确行编辑（替换/插入/删除） |
| `surgical_scan` | 手术前扫描 — 定位目标行 |

### 上下文与知识管理
| 工具 | 说明 |
|---|---|
| `index_context` | 索引文本到 BM25 检索引擎 |
| `search_context` | BM25 精确检索历史上下文 |
| `clear_index` | 清空上下文索引 |
| `index_stats` | 检索引擎统计 |

### 网络
| 工具 | 说明 |
|---|---|
| `web_search` | 网页搜索 |
| `web_fetch` | 抓取网页内容为纯文本 |
| `web_extract_links` | 提取网页中所有超链接 |

### 项目管理
| 工具 | 说明 |
|---|---|
| `exec_cmd` | 执行 Shell 命令 |
| `init_project` | 脚手架初始化新项目 |
| `install_dependency` | 安装项目依赖 |
| `git_commit` | 提交变更到 Git |

### 交互与杂项
| 工具 | 说明 |
|---|---|
| `ask_user` | 向用户提问或请求确认 |
| `task_done` | 标记任务完成 |
| `open_file` | 用系统默认程序打开文件 |
| `read_clipboard` | 读取系统剪贴板 |
| `write_clipboard` | 写入系统剪贴板 |

---

## 🧩 官方插件（8 个）

| 插件 | 提供工具 |
|---|---|
| **clipboard_manager** | `clipboard_history`, `clipboard_clear` |
| **code_reviewer** | `code_review` |
| **dev_utilities** | `generate_uuid`, `generate_password`, `hash_text`, `timestamp_convert` |
| **doc_reader** | `read_docx`, `read_xlsx`, `read_pptx` |
| **note_manager** | `save_note`, `list_notes`, `search_notes` |
| **office_writer** | `write_docx`, `write_xlsx`, `write_pptx` |
| **stress_tester** | `stress_test`, `benchmark_compare` |
| **time_tracker** | `time_report`, `session_stats` |

---

## 🛡️ 安全架构

```
用户输入
  │
  ├── jailbreak_guard.py   ← 越狱/注入检测（正则 + Unicode混淆 + Base64）
  │
  ▼
Agent 循环 (async_loop.py)
  │
  ├── 工具调用
  │     ├── norp_safe.py        ← 危险命令拦截 + UAC 提权检测
  │     ├── permission_cascade  ← 权限级联（层级权限交集）
  │     ├── sandbox_pool        ← 进程级隔离沙箱（8 个池）
  │     ├── file_io_queue       ← 文件并发冲突检测
  │     └── resource_isolator   ← 资源配额
  │
  ├── 插件执行
  │     ├── plugin_system/security.py  ← AST 源码审计 + 导入限制
  │     └── path_mapper               ← 路径映射（宿主 ↔ 沙箱）
  │
  └── 生命周期 (lifecycle_manager.py)
        └── 进程组管理 + 僵尸扫描（5 秒间隔）
```

### 安全特性详解

| 层级 | 模块 | 功能 |
|---|---|---|
| **输入层** | `jailbreak_guard.py` | 越狱注入检测，支持 block/warn 两种模式，默认开启 |
| **命令层** | `norp_safe.py` | 危险命令拦截、UAC 提权检测、路径越界防护 |
| **执行层** | `sandbox_pool.py` | 8 个进程级隔离沙箱，工具调用不污染宿主 |
| **文件层** | `file_io_queue.py` | 并发冲突检测与排队，防止竞态条件 |
| **权限层** | `permission_cascade.py` | 层级权限模型，写入/删除需用户确认 |
| **插件层** | `plugin_system/security.py` | AST 源码审计、导入白名单、资源配额限制 |
| **运行时** | `runtime_check.py` | 启动时核心文件哈希校验，阻止篡改执行 |
| **僵尸防护** | `lifecycle_manager.py` | 两阶段停止、HTTP 传输层关闭、180s API 超时 |

---

## ⚙️ Responses API 说明

> ⚠️ **默认关闭，仅 OpenAI 端点支持。**

DeepSeek 官方文档声明其 Responses API 接口**无状态**——开启后每次请求必须携带完整 `input` 历史，否则多轮对话将丢失上下文。因此该功能仅在 OpenAI 官方端点 (`api.openai.com`) 生效，DeepSeek / 自定义 / 本地端点自动回退 Chat Completions。

如需启用：设置 → Models & API → 勾选"Enable Responses API (Beta)"。

---

## 📋 依赖

| 包名 | 版本 | 用途 |
|---|---|---|
| `pywebview` | ≥6.0 | 桌面窗口（WebView2 渲染） |
| `openai` | ≥2.0 | OpenAI / DeepSeek API |
| `anthropic` | ≥0.120 | Anthropic API |
| `pystray` | ≥0.19 | 系统托盘图标 |
| `Pillow` | ≥12.0 | 托盘图标生成 |
| `pywin32` | ≥312 | Windows API |
| `keyring` | ≥25.0 | API Key 加密存储（Windows 凭据管理器） |
| `requests` | ≥2.34 | HTTP 请求 |

可选依赖：`PyPDF2`、`python-docx`、`openpyxl`、`python-pptx`（Office 文档读写）、`docker`（Docker 沙箱）、`beautifulsoup4`（网页解析增强）。

---

## 🔧 开发

```bash
# 前端开发（修改 front_src/ 后自动重新构建）
python main.py    # 自动检测 front_src/ 变更 → 重新构建 front.html

# 打包
python -m PyInstaller norp_agent.spec --noconfirm
# 输出: dist/NORP Agent.exe

# 代码统计
python -c "from count_lines import *; ..."
```

---

## 📄 许可证

MIT License. Copyright (c) 2026 xingluosama @ NORP Studio.
