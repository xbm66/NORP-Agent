# NORP Vibe Coding Agent

**Usage Documentation**

Version 1.0 | August 2026

---

## Table of Contents

1. [Product Introduction](#1-product-introduction)
2. [Quick Start](#2-quick-start)
3. [Interface & Layout](#3-interface--layout)
4. [Session Management](#4-session-management)
5. [Built-in Tools Reference](#5-built-in-tools-reference)
6. [Configuration & Options](#6-configuration--options)
7. [Plugin System](#7-plugin-system)
8. [Security Mechanisms](#8-security-mechanisms)
9. [Async Architecture](#9-async-architecture)
10. [Best Practices](#10-best-practices)
11. [Frequently Asked Questions (FAQ)](#11-frequently-asked-questions-faq)
12. [Appendix](#12-appendix)

---

## 1. Product Introduction

### 1.1 What is NORP Vibe Coding Agent?

NORP Vibe Coding Agent is an autonomous coding agent powered by large language models (LLMs), built on the **ReAct** (Reasoning + Acting) architecture. It transforms natural language instructions into precise code operations — actively analyzing problems, writing code, and managing files — rather than passively responding in a Q&A fashion.

Unlike traditional AI chatbots, Vibe Coding Agent has full tool-calling capabilities: it can read and modify files, execute shell commands, search codebases, install dependencies, scaffold projects, and even search the web for the latest technical information. It works like a tireless full-stack engineer, autonomously completing programming tasks in your workspace.

### 1.2 Core Features

- 🤖 **Autonomous Programming**: Based on the ReAct architecture, automatically reasons about and executes multi-step programming tasks including coding, debugging, and refactoring.
- 🔧 **Rich Toolset**: 14 built-in tool functions covering file operations, command execution, project scaffolding, dependency management, and more.
- 🧩 **Extensible Plugin System**: Supports third-party plugins with 15 lifecycle hooks. Custom tools and behaviors can be added. Multi-layer security auditing ensures plugin safety.
- 🛡️ **Security Sandbox**: Supports Docker container isolation and process group isolation, file path boundary checks, dangerous command interception, and write/delete confirmation dialogs.
- 📑 **Multi-Session Management**: Supports up to 16 independent sessions (browser-tab style), each with its own workspace and conversation history.
- ⚡ **Async Architecture**: Built on asyncio with an async execution engine, sandbox pool management, file I/O queue, and lifecycle management.
- 🔒 **API Key Encryption**: Encrypts API keys using Windows DPAPI (win32crypt) or the system keyring.
- 🌐 **Multi-API Compatibility**: Supports OpenAI Chat Completions, DeepSeek Responses API, and Anthropic Messages API.
- 💭 **Chain-of-Thought Visualization**: Real-time streaming display of the AI's reasoning process (reasoning_content), letting you see *how* the AI thinks.
- 📊 **Token Usage Tracking**: Real-time statistics for input/output/tool_call token consumption, with balance inquiry support.

### 1.3 Technical Architecture Overview

Vibe Coding Agent consists of the following core modules:

| Module | Description |
|--------|-------------|
| `main.py` | Application entry point — a desktop window based on pywebview |
| `api.py` | pywebview JS bridge layer, exposing all APIs to the frontend |
| `async_loop.py` | Async Agent main loop (core Reasoning-Acting cycle) |
| `async_executor.py` | Async tool executor (integrates sandbox pool, file I/O queue, etc.) |
| `config.py` | Configuration management with encrypted API key storage |
| `event_queue.py` | Thread-safe event queue (Agent ↔ Frontend communication) |
| `sandbox_pool.py` | Sandbox pool (up to 8 sandboxes, async acquire/release) |
| `file_io_queue.py` | File concurrency conflict detection and queuing |
| `lifecycle_manager.py` | Task lifecycle and zombie process cleanup |
| `permission_cascade.py` | Permission cascade model (hierarchical permission inheritance) |
| `resource_isolator.py` | Resource isolation (terminal 40% + plugin pool 60%) |
| `path_mapper.py` | Bidirectional path mapping (host ↔ sandbox) |
| `plugin_system/` | Plugin framework (manager, security auditing, context) |
| `tools.py` | OpenAI Function Schema definitions for 14 built-in tools |

---

## 2. Quick Start

### 2.1 Installation & Launch

Vibe Coding Agent is a portable desktop application — no installation required. Download and extract the package to any directory, then double-click to run.

On first launch, the application automatically creates a configuration directory at:

```
%LOCALAPPDATA%\vibe_agent\
```

This directory contains `config.json` (configuration file), `base.env` (encrypted API key), plugin logs, tool call records, and more.

### 2.2 Configuring the API Key

Before using the Agent, you need to configure your DeepSeek API key:

1. Open the application and click the **Settings** icon in the top-right corner
2. Paste your DeepSeek API Key into the **API Key** input field
3. Click the **Verify** button to confirm the key is valid
4. Once configured, the system automatically encrypts and stores the key — no need to re-enter it each time

**Supported API Endpoints:**

- **DeepSeek Official Endpoint**: `https://api.deepseek.com` (recommended)
- **Custom OpenAI-Compatible Endpoint**: Supports any API service compatible with the OpenAI SDK
- **Anthropic-Compatible Endpoint**: `https://api.deepseek.com/anthropic`

### 2.3 Your First Conversation

After configuring the API key, type your first task in the input box, for example:

> *"Create a Python Flask Hello World project for me."*

The Agent will autonomously plan the steps: initialize the project → create files → install dependencies → verify the code. You can watch the AI's reasoning process and tool call details in real time through the interface.

---

## 3. Interface & Layout

### 3.1 Main Window

The application window defaults to **1200×800** pixels (minimum **800×500**). It features a modern dark theme and is divided into the following main areas:

- **Top Toolbar**: Session tab switching, new session button, settings entry, token usage display
- **Left Conversation Area**: Streaming AI responses (with collapsible reasoning panels). Messages are rendered token-by-token in real time.
- **Right Info Panel**: Tool call logs (JSON format), token usage charts, plugin status
- **Bottom Input Area**: Text input box + Send/Stop buttons. Supports multi-line input.

### 3.2 Event Stream Protocol

The frontend communicates with the Agent by polling the EventQueue. Event prefixes have the following meanings:

| Prefix | Meaning |
|--------|---------|
| `T:` | **Thinking / Reasoning** — the AI's "inner monologue" |
| `R:` | **Response** — the AI's final output |
| `C:` | **Command** — the AI decides to invoke a tool |
| `Q:` | **Question** — the AI needs user confirmation or input |
| `WC:` | **Write Confirm** — requesting user confirmation for a file operation |
| `D:` | **Done** — task execution complete |
| `E:` | **Error** — exception information |
| `U:` | **Usage** — token consumption statistics |
| `F:` | **Finalize** — reasoning phase complete, entering output phase |

---

## 4. Session Management

### 4.1 Multi-Session Architecture

Vibe Coding Agent supports running up to **16 concurrent sessions** (similar to browser tabs). Each session has:

- Independent conversation history (`conversation_history`)
- Independent event queue (`EventQueue`)
- Independent Agent loop thread (`AsyncAgentLoop`)
- Independent workspace path (`workspace` / `project_root`)
- Independent persistent memory (`memory.json`)

### 4.2 Session Operations

| Action | How To |
|--------|--------|
| **New Session** | Click the "+" button on the right side of the tab bar. You can specify an independent workspace directory for the new session. |
| **Switch Session** | Click a tab to switch. Tasks in different sessions do not affect each other and can run in parallel. |
| **Close Session** | Right-click a tab and select "Close". At least one session must remain (the last one cannot be closed). |
| **Rename Session** | Double-click the tab title to edit the name. |
| **Set Workspace** | Modify `project_root` in session settings. All file operations for that session will be restricted to this directory. |

### 4.3 Memory System

Each session supports persistent memory. When enabled, the system automatically saves conversation records to:

```
%LOCALAPPDATA%\vibe_agent\memory\memory_{session_id}.json
```

There are two memory modes:

- **`full` (Complete Mode)**: Keeps the full content of the most recent N conversation rounds. Older conversations beyond `max_rounds` are automatically removed.
- **`summary` (Summary Mode)**: Keeps only the last 2 rounds of conversation; the rest are compressed into a text summary, saving context tokens.

---

## 5. Built-in Tools Reference

The Agent has **14 built-in tool functions**, all registered in OpenAI Function Calling format. Below is a description of each tool's purpose, parameters, and usage notes.

### 5.1 `read_file` — Read File

Reads the content of any text file within the workspace. Supports line range specification to save tokens.

**Parameters:**

- `path` *(required)*: File path, relative to the workspace root
- `start_line` *(optional)*: Starting line number (1-based)
- `end_line` *(optional)*: Ending line number (inclusive)

> **Tip**: When debugging, use `search_in_files` first to locate the problem line, then use line ranges for precise reading.

### 5.2 `write_file` — Write File

Creates a new file or overwrites an existing one. Parent directories are automatically created if they don't exist.

**Parameters:**

- `path` *(required)*: Target file path
- `content` *(required)*: Complete content to write

> ⚠️ **Safety Tip**: Before overwriting a file, it's recommended to call `read_file` first to back up the original content. If the `confirm_write_delete` option is enabled, a confirmation dialog will appear before writing.

### 5.3 `replace_in_file` — Precise Replacement

Finds and replaces specified text within a file. The `old_str` must match exactly (including indentation and line breaks) and must match a **single, unique** location in the file.

**Parameters:**

- `path` *(required)*: File path
- `old_str` *(required)*: The original text to be replaced (must match exactly)
- `new_str` *(required)*: The replacement text

> **Advantage**: Compared to `write_file` which rewrites the entire file, this tool only modifies the target fragment, significantly saving tokens.

### 5.4 `list_dir` — List Directory

Lists files and subdirectories in a specified directory. Directories are indicated with a trailing `/`.

**Parameters:**

- `path` *(optional)*: Directory path. Defaults to `"."` (workspace root).

### 5.5 `search_in_files` — Search Files

Recursively searches for files containing a specified text pattern within the workspace. Automatically skips `__pycache__`, `node_modules`, `.git`, and similar directories.

**Parameters:**

- `pattern` *(required)*: The text to search for
- `path` *(optional)*: Search scope — can be a file path or directory. Defaults to the entire workspace.

> **Result limit**: Up to 50 matches returned; excess matches are truncated.

### 5.6 `delete_file` — Delete File/Directory

Deletes a file or an entire directory (including all sub-contents). ⚠️ **This operation is irreversible.**

**Parameters:**

- `path` *(required)*: Path of the file or directory to delete

> **Safety constraint**: Before execution, the Agent must call `ask_user` to obtain user confirmation.

### 5.7 `exec_cmd` — Execute Command

Executes a shell command in a sandbox or local environment, with built-in dangerous command interception.

**Parameters:**

- `command` *(required)*: The shell command to execute
- `timeout` *(optional)*: Timeout in seconds. Defaults to 30.

> **Blocked dangerous patterns**: `sudo`, `rm -rf /`, `mkfs`, `dd if=`, `format c:`, and more.

### 5.8 `init_project` — Initialize Project

Automatically creates scaffolded directory structures based on the project type.

**Parameters:**

- `type` *(required)*: Project type — `python` / `web` / `node`
- `name` *(required)*: Project name

> Python type creates `__init__.py`, `main.py`, `requirements.txt`; Web type creates `index.html` + `css/` + `js/` directories.

### 5.9 `install_dependency` — Install Dependency

Installs project dependencies using pip or npm.

**Parameters:**

- `package` *(required)*: Package name, e.g., `flask`, `requests`
- `manager` *(optional)*: Package manager — `pip` (default), `npm`

### 5.10 `git_commit` — Git Commit

Executes `git add -A` and `git commit`, committing current changes to the repository.

**Parameters:**

- `message` *(required)*: Commit message. Conventional commit format is recommended (e.g., `feat: add user auth`).

### 5.11 `ask_user` — Ask User

Used when the Agent needs user confirmation, selection, or additional information. Pauses task execution until the user responds.

**Parameters:**

- `question` *(required)*: The question to present to the user. Supports Markdown format.

### 5.12 `task_done` — Mark Complete

Called when a task is finished. Writes the summary and code paths to `.agent_history.json`.

**Parameters:**

- `summary` *(required)*: Task completion summary
- `code_path` *(optional)*: Main code paths involved

> History records are capped at 20 entries; older records are automatically removed.

### 5.13 `web_search` — Web Search

Performs web searches via the DuckDuckGo Instant Answer API. Requires the `enable_web_search` configuration to be enabled. When using DeepSeek V4 Flash + Responses API mode, server-side native search is used for better results.

**Parameters:**

- `query` *(required)*: Search keyword or question

### 5.14 `open_file` — Open File

Opens a file with the system's default program. Supports all common file types including images, documents, and web pages.

**Parameters:**

- `path` *(required)*: File path

---

## 6. Configuration & Options

All configuration items are stored in `%LOCALAPPDATA%\vibe_agent\config.json`. Below is a complete reference.

### 6.1 General Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `language` | `zh_CN` | Interface language |
| `model` | `deepseek-v4-pro` | Model selection: `deepseek-v4-pro` / `deepseek-v4-flash` |
| `api_base` | `https://api.deepseek.com` | API endpoint URL. Supports custom compatible endpoints |
| `project_root` | `~/vibe_workspace` | Default workspace root directory |
| `max_steps` | `128` | Maximum reasoning steps per task |
| `temperature` | `1.0` | Generation temperature (0.0–2.0) |
| `think_level` | `High` | Reasoning depth: `Off` / `Low` / `Medium` / `High` |
| `max_tokens` | `32767` | Maximum tokens per response |
| `task_timeout` | `0` | Task timeout in seconds. `0` means no limit |
| `enable_web_search` | `false` | Whether to enable web search |
| `confirm_write_delete` | `true` | Whether to require user confirmation before write/delete operations |
| `use_responses_api` | `true` | Whether Flash model uses the Responses API |

### 6.2 Memory System Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `memory` | `false` | Whether to enable the memory system |
| `memory_mode` | `full` | Memory mode: `full` (complete) / `summary` |
| `max_rounds` | `10` | Maximum conversation rounds retained in full mode |

### 6.3 Plugin System Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `plugins_enabled` | `true` | Whether to enable the plugin system |
| `plugin_dirs` | `[]` | List of plugin directories |
| `plugin_security_audit` | `warn` | Security audit level: `off` / `warn` / `block` |
| `plugin_security_import_restrict` | `off` | Import restriction: `off` / `safe` / `strict` |
| `plugin_security_require_permissions` | `false` | Whether to require plugins to declare permissions |
| `plugin_security_resource_limit` | `false` | Whether to enable plugin resource limits |

### 6.4 Async Architecture Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `sandbox_pool_max` | `8` | Maximum number of sandboxes in the pool |
| `sandbox_network_enabled` | `false` | Whether sandboxes allow network access |
| `file_io_queue_enabled` | `true` | Whether to enable file I/O concurrency detection |
| `lifecycle_zombie_scan_seconds` | `5` | Zombie process scan interval (seconds) |
| `resource_terminal_reserved_pct` | `40` | Percentage of resources reserved for the terminal |

---

## 7. Plugin System

Vibe Coding Agent includes a complete plugin framework that allows developers to extend the Agent's capabilities. Plugins can register custom tools, listen to lifecycle hooks, and access session context.

### 7.1 Built-in Official Plugins

The system comes pre-installed with the following official plugins:

- **Code Reviewer**: Performs comprehensive code quality reviews on source files. Checks documentation strings, exception handling, code complexity, naming conventions, TODO/FIXME markers, security vulnerabilities, and more. Generates structured review reports with scores.
- **Dev Utilities**: Provides common development tools including UUID generation, secure password generation, hash computation (MD5/SHA1/SHA256/SHA512), timestamp-to-date conversion, and project line counting.
- **Note Manager**: Create, edit, and search structured notes within conversations. Supports tag-based categorization and full-text search.
- **Time Tracker**: Tracks Agent task execution time and generates time reports with efficiency analysis.

### 7.2 Plugin Security Management

Before loading, plugins undergo multiple layers of security checks:

- **AST Source Audit** — Scans for dangerous patterns (`os.system`, `subprocess`, `eval`, `exec`, etc.)
- **Import Restriction** — Prevents plugins from importing dangerous modules such as `ctypes`, `subprocess`, `socket`
- **Permission Declaration** — Plugins can be required to declare needed permissions in `manifest.json`
- **Resource Limiting** — CPU time (30s) and memory (512MB) limits can be applied to plugins
- **Security Level** — `off` (disable auditing) / `warn` (warn but allow) / `block` (block loading)

> For detailed plugin development instructions, please refer to the **NORP Vibe Coding Agent Plugin Development Guide** document.

---

## 8. Security Mechanisms

### 8.1 Path Boundaries

All file operations are validated through the `_safe_path()` method, ensuring paths are restricted to the workspace root directory. Any path containing `..` or pointing outside the workspace will be rejected.

### 8.2 Command Security

Shell commands are checked for dangerous patterns before execution:

- `sudo`
- `rm -rf /`
- `mkfs`
- `dd if=`
- `> /dev/sda`
- `format c:`

### 8.3 Operation Confirmation

When the `confirm_write_delete` option is enabled, `write_file`, `delete_file`, and `replace_in_file` operations will trigger a confirmation dialog. The user must manually confirm before execution.

### 8.4 API Key Protection

API keys support two encryption storage methods:

- **win32crypt (Windows DPAPI)** — Encrypts using the Windows Data Protection API; only the current user can decrypt
- **keyring** — Uses the system keyring service for storage

### 8.5 Sandbox Isolation

Two sandbox modes are supported:

- **Docker Container** — Fully isolated filesystem, network, and memory limits (recommended)
- **Subprocess Isolation** — Windows Job Object / Unix process groups, no Docker required

### 8.6 Plugin Security

See Section 7.2. Multi-layer security mechanisms ensure third-party plugins do not compromise system security.

---

## 9. Async Architecture

Vibe Coding Agent was refactored from an initial multi-threaded architecture to an **asyncio-based async architecture**, addressing three core challenges: concurrent tasks, resource contention, and zombie processes.

### 9.1 Sandbox Pool (`SandboxPool`)

Manages an async pool of up to 8 sandbox instances. When a task needs a sandbox, it calls `acquire()` to asynchronously obtain one; if none are free, it queues and waits. After use, `release()` returns it to the pool.

Supports:

- Docker container + subprocess isolation (dual mode)
- Path mapping (host path ↔ sandbox path)
- Command execution result path reverse mapping
- Process group management (Windows Job Object / Unix PGID)

### 9.2 File I/O Queue (`FileIOQueue`)

Resolves conflicts when multiple tasks concurrently read/write the same file:

- **Conflict Detection**: Read-read = no conflict, read-write / write-write = conflict
- **FIFO Queuing**: Conflicting operations are automatically queued
- **Write Starvation Prevention**: Subsequent readers also wait when a writer is queued
- **30-Second Timeout**: Prevents deadlocks

### 9.3 Lifecycle Management (`LifecycleManager`)

Solves the problem of residual zombie processes after a user stops a task:

- **Process Group Binding**: All child processes of a task are registered to the same process group
- **Cascading Termination**: When stopping a task, kills the entire process group (not just a single process)
- **Zombie Scanning**: Every 5 seconds, scans stopped tasks to ensure all processes are terminated
- **User Wait Protection**: Tasks in `WAITING_USER` status are not mistakenly killed by the scanner

### 9.4 Permission Cascade (`PermissionCascade`)

A hierarchical permission model: System > Terminal > Plugin Root > Plugin Sub-call. Child operation permissions = Parent permissions ∩ Child declared permissions (intersection). No operation can exceed the permission scope of its parent.

### 9.5 Resource Isolation (`ResourceIsolator`)

Resolves resource conflicts between plugins and the terminal:

- Terminal reserves **40%** of system resources (highest priority)
- Plugin pool occupies **60%**, evenly divided among plugins
- Each plugin has independent quotas: CPU 30s, Memory 256MB, I/O 50MB
- When quotas are exhausted, requests are denied or queued

---

## 10. Best Practices

### 10.1 Writing Effective Task Instructions

- **Be Specific**: Instead of "make a website", say "create a blog with user login using Flask + Bootstrap"
- **Provide Context**: If there's an existing codebase, tell the Agent to explore the project structure first
- **Step-by-Step Requests**: Break complex tasks into multiple smaller steps and progress incrementally
- **Specify Constraints**: Clearly state preferences for tech stack, coding style, file structure, etc.

### 10.2 Token Optimization

- Use `replace_in_file` instead of `write_file` for modifying large files
- Use `read_file` with line range parameters to read only the needed fragments
- Split complex tasks across multiple conversations to avoid overly long single-session contexts
- Enable the memory system (summary mode) to manage long-term conversations

### 10.3 Security Recommendations

- Keep `confirm_write_delete` enabled to avoid accidental file modifications
- Regularly check `plugin_dirs` and remove untrusted plugin directories
- Use Docker sandbox mode when running untrusted code
- Do not share `config.json` or `base.env` files publicly

---

## 11. Frequently Asked Questions (FAQ)

**Q: What should I do if the Agent gets stuck mid-execution?**

A: Click the **Stop** button. The system will kill the entire process group through the LifecycleManager, leaving no zombie processes behind.

**Q: How do I switch models?**

A: Modify the `model` field in Settings. Options include `deepseek-v4-pro` (general purpose) and `deepseek-v4-flash` (fast). The Flash model automatically enables the Responses API for a better search experience.

**Q: Which API providers are supported?**

A: DeepSeek official endpoints are supported by default. Any OpenAI SDK-compatible custom endpoint is also supported. The web search feature is also compatible with DeepSeek's Anthropic endpoint.

**Q: What if a plugin fails to load?**

A: Check the `plugin_security_audit` level in Settings. If set to `block`, any plugin with security risks will be rejected. You can temporarily set it to `warn` or `off`, review the specific audit report, and then decide.

**Q: Why are file operations being rejected?**

A: Check if `confirm_write_delete` is enabled (enabled by default). If so, write/delete operations require manual confirmation. Also verify that the target path is within the workspace root directory.

**Q: How do I track token consumption?**

A: The top-right corner displays real-time cumulative consumption for input/output/tool_call tokens. You can also click **Check Balance** in Settings to get DeepSeek account balance information.

**Q: Do multiple sessions interfere with each other?**

A: No. Each session has its own independent event queue, Agent loop, conversation history, and workspace. However, the file I/O queue will detect cross-session file concurrency conflicts and automatically queue them.

---

## 12. Appendix

### 12.1 Project Directory Structure

```
vibe_agent/
├── main.py                 # Application entry point
├── api.py                  # pywebview API bridge
├── async_loop.py           # Async Agent main loop
├── async_executor.py       # Async tool executor
├── config.py               # Configuration management
├── event_queue.py          # Event queue
├── loop.py                 # Synchronous Agent loop (legacy)
├── executor.py             # Synchronous tool executor (legacy)
├── sandbox_pool.py         # Sandbox pool management
├── file_io_queue.py        # File I/O queue
├── lifecycle_manager.py    # Lifecycle management
├── path_mapper.py          # Path mapping
├── permission_cascade.py   # Permission cascade
├── resource_isolator.py    # Resource isolation
├── tools.py                # Built-in tool definitions
├── front.html              # Frontend interface
├── plugin_system/          # Plugin framework
│   ├── __init__.py
│   ├── manager.py          # Plugin manager
│   ├── security.py         # Security auditing
│   └── context.py          # Plugin context
└── official_plugins/       # Official plugins
    ├── code_reviewer.py
    ├── dev_utilities.py
    ├── note_manager.py
    └── time_tracker.py
```

### 12.2 API Call Modes

The Agent supports three API call modes, automatically switched based on the model and `base_url`:

| Mode | Condition | Characteristics |
|------|-----------|-----------------|
| **Responses API** | Flash model + official endpoint | Server-side native search, semantic event stream, stateless calls |
| **Chat Completions** | Pro model or custom endpoint | Standard OpenAI format, tool calls, streaming reasoning |
| **Anthropic Messages** | Official endpoint + search enabled | Anthropic native search tools, independent system prompts |

### 12.3 Version Information

**NORP Vibe Coding Agent v1.0**

Copyright © 2026 xingluosama

Document generated: August 1, 2026
