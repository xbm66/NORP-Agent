# NORP Agent

> Vibe Coding Agent —— 桌面端 AI 编程 / 自动化助手（异步架构）
> Copyright (c) 2026 xingluosama

NORP Agent 是一个运行在 Windows 桌面上的 AI Agent 应用：它通过大语言模型（LLM）驱动，可以在你的工作区里阅读代码、编写文件、执行命令、搜索网页、管理笔记，并通过**插件系统**与**视觉 API** 无限扩展能力。

- 内置 **ReAct 循环**：思考（Reasoning）→ 调用工具（Act）→ 观察结果（Observation），直到完成任务。
- 内置 **插件系统**：无需改主程序即可注册新工具、订阅 Agent 生命周期钩子，且插件在**独立子进程**中隔离运行。
- 内置 **视觉 API**：接入任意多模态视觉模型（OpenAI 兼容 / Claude / 本地 llama.cpp），让 Agent「看得懂」图片与视频。
- 多层安全体系：越狱防护、危险命令拦截、路径越界防护、插件静态审计、进程隔离、签名校验、SSRF 防护、人工审批。

---

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [界面与使用](#界面与使用)
- [插件开发](#插件开发)
- [视觉 API](#视觉-api)
- [安全体系](#安全体系)
- [项目结构](#项目结构)
- [配置说明](#配置说明)
- [常见问题](#常见问题)
- [相关文档](#相关文档)

---

## 功能特性

### 核心能力

- **多模型接入**：支持任意 OpenAI 兼容服务（DeepSeek / OpenAI / Qwen / GLM / Ollama / vLLM / llama.cpp 等），模型列表动态拉取，无需硬编码白名单。
- **Responses API 与 Chat Completions** 双协议支持（`use_responses_api` 可切换）。
- **会话管理**：多会话并行、会话标题、每会话独立工作区与记忆。
- **文件操作**：读文件（含大文件分段读取、图片视觉读取）、写文件、删除、移动、搜索、全文索引（SQLite FTS5）、超大文件流式检索。
- **命令执行**：exec_cmd 执行 shell 命令（受 NORP 安全系统约束）、依赖安装、Git 提交。
- **网页抓取**：web_fetch / web_extract_links / web_search，正文提取。
- **文档解析**：PDF（PyPDF2）、Word（python-docx）、Excel（openpyxl）。
- **代码分析**：AST 语法检查、源码扫描、surgical 级精确修改。

### 插件系统（plugin_system）

- **15 个生命周期钩子**：从 Agent 启动、任务开始/结束/出错，到每一步 ReAct、每一次工具调用、流式 token 输出，全流程可订阅。
- **工具注册**：插件用 OpenAI function schema 声明工具，Agent 自动发现并调用。
- **进程级隔离**（默认）：插件在独立子进程（plugin_host）中加载执行，崩溃不影响主进程。
- **多层安全**：AST 静态审计（block/warn/off）、导入白名单限制（strict/safe/off）、权限声明（manifest permissions）、Ed25519 签名校验、网络策略四粒度 + SSRF 防护、插件工具调用人工审批。
- **热重载**：开发时可即时卸载/重载插件。

### 视觉 API（vision_*）

- 三种接入方式：**内置 provider**（openai_compatible / anthropic / llama_cpp，开箱即用）> **本地回调**（`register_vision_handler`）> **外部服务 URL**（HTTP JSON 协议）。
- Agent 链路打通：上传图片/视频自动生成视觉描述；`read_file` 读图自动转视觉描述。
- 窗口捕获与键鼠操作外挂：`capture_worker`（C++ Graphics Capture 单窗口捕获，被遮挡也可见）+ 坐标闭环 + SendInput 注入 + 安全裁决器（L0~L3 分级 / 三态熔断 / Ctrl+End 物理熔断 / 用户接管）。

### 安全体系

| 层 | 机制 |
|---|---|
| 内容级 | 越狱/提示词注入检测（jailbreak_guard）、危险命令与 UAC 拦截、路径越界防护（norp_safe）、权限级联（permission_cascade） |
| 代码级 | 插件 AST 静态审计、危险模块导入拦截（ctypes/subprocess/socket 等）、资源限制 |
| 进程级 | 插件子进程隔离、沙箱池（sandbox_pool） |
| 来源级 | 插件 Ed25519 签名校验（官方公钥内置）、信任公钥列表 |
| 网络级 | 插件网络策略四粒度 + SSRF 防护（内网/环回/元数据地址强制拦截） |
| 人工级 | 原生工具确认（写入/删除/命令）、插件工具调用审批、运行时完整性自检 |

---

## 快速开始

### 环境要求

- Windows 10/11（64 位）
- Python 3.10+（开发环境推荐 3.12）
- 一个 OpenAI 兼容的 LLM API（云端或本地均可）

### 安装

```bash
# 1. 克隆 / 解压项目到本地目录

# 2. （推荐）创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动
python main.py
```

首次启动会弹出配置向导：选择语言、填写 API Key、选择工作区目录。配置保存在 `%LOCALAPPDATA%\vibe_agent\config.json`（API Key 默认存入 Windows 凭据管理器 keyring）。

> 可选：安装 `cryptography` 已包含在 requirements 中（插件签名校验依赖）。

### 快速验证

1. 在主界面输入框输入「你好」，确认 Agent 能正常对话。
2. 输入「看看当前工作区里有什么文件」，确认工具调用正常。
3. 在「设置」中开启「视觉 API」并配置视觉模型，上传一张图片测试视觉描述。

---

## 界面与使用

- **主窗口**：内嵌前端（front.html，pywebview 渲染），支持深色模式、多语言（中文简体 / 中文繁体 / English / Русский / 日本語）。
- **设置面板**：模型与 API、工作区、安全开关（原生工具确认、越狱防护、NORP 安全）、视觉 API、关闭按钮行为等。
- **插件控制面板**：启用/禁用插件、添加插件目录、查看插件状态与安全审计结果、配置签名与网络策略、插件工具调用审批。
- **调试面板**：沙箱池状态、文件 IO 队列、生命周期统计、运行时健康、NORP 安全日志、插件钩子触发记录。

常用操作：

| 操作 | 位置 |
|---|---|
| 新建会话 / 切换工作区 | 主窗口顶部 |
| 上传图片 / 文档 | 输入框附件按钮 |
| 停止任务 | 任务运行中的「停止」按钮（或 Esc） |
| 最小化到托盘 | 关闭按钮（默认行为，可在设置改为直接退出） |

---

## 插件开发

完整的插件开发文档见 [docs/插件开发指南.md](docs/插件开发指南.md)，覆盖：

- 5 分钟写出第一个插件（无需了解任何内部实现）
- 工具注册（TOOLS + execute）与 15 个钩子的完整参考
- 安全模型：静态审计、导入限制、进程隔离、签名、网络策略、人工审批
- manifest.json、签名分发、调试排错、进阶主题（重度开发者）

**最小插件示例**（放入插件目录即可）：

```python
PLUGIN_NAME = "Hello Plugin"
PLUGIN_PUBLISHER = "Your Name"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "我的第一个插件"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "hello",
        "description": "返回问候语",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "名字"}
            },
            "required": [],
            "additionalProperties": False
        }
    }
}]

def execute(tool_name: str, args: dict, context) -> str:
    if tool_name == "hello":
        return f"你好，{args.get('name', '世界')}！—— 来自 Hello Plugin"
    return f"未知工具: {tool_name}"
```

插件目录：在「插件控制面板」中添加（config.json → `plugin_dirs`）。

---

## 视觉 API

完整的视觉 API 开发手册见 [docs/视觉API开发手册.md](docs/视觉API开发手册.md)，覆盖：

- 三种接入方式（内置 provider / 本地回调 / 外部服务 URL）与优先级
- 核心 API 参考（`process_visual` / `describe_visual_file` / `register_vision_handler` 等）
- 外部服务 HTTP 协议（请求 / 响应格式）
- 内置 Provider 适配层（openai_compatible / anthropic / llama_cpp）与自定义 provider
- 窗口捕获（capture_worker）+ 键鼠操作（SendInput）+ 安全裁决器（L0~L3 / 熔断 / override / delegate）
- 动作-验证-收敛协调器（VisionCoordinator）

**30 秒接入**（设置面板）：

1. 开启「视觉 API」
2. 选择 provider：`openai_compatible`（OpenAI / Qwen-VL / GLM-4V / Ollama / vLLM 等）
3. 填写 `vision_model` / `vision_api_key`（可选 `vision_base_url`）
4. 上传一张图片，Agent 即可看到视觉描述

---

## 安全体系

### 内容安全（LLM 层）

- **jailbreak_guard**：检测越狱 / 提示词注入，默认 `block` 拦截（可改为 `warn` 仅告警）。
- **norp_safe**：拦截危险 shell 命令、UAC 提权请求、路径越界（`..` 穿越 / 绝对系统路径）。
- **permission_cascade**：权限级联校验，工具调用按风险分级。

### 插件安全（详见插件开发指南「安全模型」）

默认配置即「最安全」：静态审计 `block`、导入限制 `strict`、进程隔离 `process`、签名校验开启、网络策略 `deny`、插件工具审批开启。

### 人工审批

- 原生工具：`write_file` / `replace_in_file` / `delete_file` / `exec_cmd` / `git_commit` 等默认弹窗确认（设置面板可关）。
- 插件工具：`approval_enabled` 开启后，所有插件工具调用均需人工确认（插件控制面板）。

---

## 项目结构

```
.
├── main.py                 # 程序入口：webview 窗口、JS bridge、启动流程
├── api.py                  # AgentAPI：前端桥接层（会话/配置/插件/视觉/统计）
├── config.py               # 配置管理（ConfigManager + 全部默认值）
├── loop.py                 # 同步 ReAct 循环（旧架构）
├── async_loop.py           # 异步 ReAct 循环（新架构，默认）
├── executor.py             # 同步工具执行器（工具实现）
├── async_executor.py       # 异步工具执行器（线程池 + 文件 IO 队列 + 沙箱）
├── tools.py                # 工具定义（OpenAI function schema）
├── vision.py               # 视觉 API 开放接口层（开放接口 + 回调注册 + 外部服务）
├── vision_adapters.py      # 视觉 Provider 适配层（openai/anthropic/llama_cpp）
├── vision_capture.py       # 窗口捕获封装（capture_worker 调用 + FrameSource）
├── vision_actions.py       # 操作执行层（坐标闭环 + SendInput 键鼠注入）
├── vision_safety.py        # 安全裁决器（L0~L3 分级 / 三态熔断 / override / delegate）
├── vision_coordinator.py   # 动作-验证-收敛协调器
├── vision_ipc.py           # IPC 协议层（XML 信封 + JSON 负载）
├── plugin_system/          # 插件系统
│   ├── manager.py          #   PluginManager：发现/加载/分发
│   ├── context.py          #   PluginContext + SimpleLogger
│   ├── security.py         #   静态审计 + 导入限制 + 资源限制
│   ├── signature.py        #   Ed25519 签名校验
│   ├── network_policy.py   #   网络策略 + SSRF 防护
│   ├── approval.py         #   人工审批策略
│   └── plugin_host.py      #   插件宿主子进程（进程隔离 + RPC）
├── plugins/                # 插件目录（用户插件）
├── official_plugins/       # 官方插件（带签名）
├── capture_worker/         # C++ 窗口捕获子进程（Graphics Capture）
├── sandbox_pool.py         # 命令执行沙箱池
├── file_io_queue.py        # 文件 IO 队列（读写互斥）
├── norp_safe.py            # NORP 安全系统（命令/UAC/路径）
├── jailbreak_guard.py      # 越狱/注入防护
├── permission_cascade.py   # 权限级联
├── workspace_index.py      # 工作区全文索引（SQLite FTS5）
├── context_index.py        # 对话上下文检索引擎
├── front.html              # 前端（构建产物）
├── front_src/              # 前端源码（index.html / ui.js / i18n.js）
├── build_front.py          # 前端构建脚本
├── docs/                   # 文档（设计文档、插件指南、视觉手册）
└── requirements.txt        # Python 依赖
```

---

## 配置说明

配置文件：`%LOCALAPPDATA%\vibe_agent\config.json`（由 ConfigManager 管理，前端设置面板读写）。

常用配置键（完整列表见 `config.py` 的 `defaults`）：

| 键 | 默认值 | 说明 |
|---|---|---|
| `language` | `zh_CN` | 界面语言 |
| `model` | `deepseek-v4-pro` | 模型名 |
| `api_base` | `https://api.deepseek.com` | API 地址（任意 OpenAI 兼容服务） |
| `project_root` | `~/vibe_workspace` | 默认工作区 |
| `max_steps` | `128` | ReAct 最大步数 |
| `plugins_enabled` | `true` | 启用插件系统 |
| `plugin_dirs` | `[]` | 插件目录列表 |
| `plugin_security_audit` | `block` | 插件静态审计级别 |
| `plugin_security_import_restrict` | `strict` | 插件导入限制 |
| `plugin_isolation` | `process` | 插件隔离模式（process / inprocess） |
| `plugin_signature_verify` | `true` | 插件签名校验 |
| `plugin_network_policy` | `deny` | 插件网络策略 |
| `approval_enabled` | `true` | 插件工具调用审批 |
| `vision_enabled` | `false` | 视觉 API 总开关 |
| `vision_provider` | `""` | 视觉 provider（openai_compatible / anthropic / llama_cpp） |
| `norp_safe_enabled` | `true` | NORP 安全系统 |
| `jailbreak_guard_enabled` | `true` | 越狱防护 |

---

## 常见问题

**Q：启动报错缺少依赖？**
运行 `pip install -r requirements.txt`。

**Q：插件加载失败 / 被拦截？**
打开「插件控制面板」查看该插件的安全审计结果（audit_issues）与错误信息。默认 `block` + `strict` 是最高安全配置，若插件确实可信：可为其添加签名并加入信任公钥（推荐），或临时放宽审计级别。

**Q：视觉 API 提示「未配置视觉处理」？**
在设置中开启「视觉 API」并配置 provider（或 `vision_service_url` / 注册本地回调）。详见视觉 API 开发手册。

**Q：如何让 Agent 读取图片？**
开启视觉 API 后，`read_file` 遇到图片会自动返回视觉描述；也可在输入框上传图片。

**Q：重复启动提示？**
NORP Agent 已通过 `instance.lock` 做重复启动检测，确认后允许多实例运行。

---

## 相关文档

| 文档 | 位置 |
|---|---|
| 插件开发指南（小白 → 重度开发者） | [docs/插件开发指南.md](docs/插件开发指南.md) |
| 视觉 API 开发手册 | [docs/视觉API开发手册.md](docs/视觉API开发手册.md) |
| 视觉 + 可操作 Agent 设计文档 | [docs/vision_agent_design.md](docs/vision_agent_design.md) |
| 更新日志 | [更新日志.txt](更新日志.txt) |

---

## 致谢与许可

- 项目作者：xingluosama
- 本项目仅供学习与研究使用，请遵守所在地区的法律法规与相关服务条款。
