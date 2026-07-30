# NORP Vibe Coding Agent

⚡ A ReAct-architecture autonomous coding agent desktop app — drive code with natural language, let AI write it for you.

## Overview

NORP Vibe Coding Agent is a desktop AI coding assistant. Users describe tasks in natural language, and the agent autonomously reasons and invokes **14 tools** — including file I/O, code execution, web search, project scaffolding, and more — to complete them. Built on the **ReAct (Reasoning + Acting)** architecture, it "thinks" then "acts" at each step, streaming its reasoning process for full transparency.

Key Features:
- 🧠 **ReAct Reasoning Loop** — autonomous thinking, tool invocation, iterative execution until task completion
- 🖥️ **Dual-Panel Desktop GUI** — chat on the left, command log on the right, with real-time agent activity
- 🔧 **14 Built-In Tools** — covering file CRUD, shell execution, web search, git commits, and more
- 🐳 **Docker Sandbox** — optional isolated command execution environment for running untrusted code safely
- 🔑 **Encrypted API Key Storage** — protects API keys using Windows DPAPI (win32crypt)
- 💬 **Persistent Conversation Memory** — cross-session context memory (full / summary modes)
- 🌐 **Web Search Integration** — real-time DuckDuckGo search
- 📎 **Drag-and-Drop File Upload** — supports 40+ formats including txt, py, json, pdf, docx, xlsx
- 🎨 **Rich Text Rendering** — Markdown, code highlighting, KaTeX math formulas, tables
- ⚙️ **Granular Configuration** — model selection, reasoning depth, temperature, timeout, confirmation policy, and more

## Technical Architecture

```
┌──────────────────────────────────────────────────┐
│                   main.py                         │
│             (pywebview desktop window)             │
│                                                    │
│   ┌──────────────────────────────────────────┐   │
│   │            front.html                     │   │
│   │   Chat Panel       │  Commands Panel      │   │
│   │   (marked.js)       │  (log stream)        │   │
│   │   (KaTeX math)      │                      │   │
│   └──────────────┬───────────────────────────┘   │
│                  │ pywebview JS Bridge             │
│   ┌──────────────▼───────────────────────────┐   │
│   │            api.py                         │   │
│   │   AgentAPI — frontend/backend bridge      │   │
│   │   (send message / poll events / input /   │   │
│   │    config)                                │   │
│   └──────────────┬───────────────────────────┘   │
│                  │                                 │
│   ┌──────────────▼───────────────────────────┐   │
│   │            loop.py                        │   │
│   │   AgentLoop — ReAct core loop             │   │
│   │                                             │   │
│   │   1. Build System Prompt + context          │   │
│   │   2. Call LLM API (OpenAI SDK)             │   │
│   │   3. Stream reasoning + tool_calls         │   │
│   │   4. Invoke ToolExecutor                   │   │
│   │   5. Feed results back to LLM, loop        │   │
│   └──────────────┬───────────────────────────┘   │
│                  │                                 │
│   ┌──────────────▼───────────────────────────┐   │
│   │         executor.py                       │   │
│   │   ToolExecutor — tool dispatcher          │   │
│   │   ├─ read_file / write_file               │   │
│   │   ├─ replace_in_file / delete_file        │   │
│   │   ├─ list_dir / search_in_files           │   │
│   │   ├─ exec_cmd (local / Docker Sandbox)     │   │
│   │   ├─ web_search (DuckDuckGo API)          │   │
│   │   ├─ init_project / install_dependency    │   │
│   │   ├─ git_commit / task_done               │   │
│   │   └─ open_file / ask_user                 │   │
│   └──────────────────────────────────────────┘   │
│                                                    │
│   ┌──────────────────────────────────────────┐   │
│   │         config.py / tools.py              │   │
│   │   config management (JSON) /              │   │
│   │   tool definitions (JSON Schema)          │   │
│   └──────────────────────────────────────────┘   │
└──────────────────────────────────────────────────┘
```

### Core Modules

| Module | File | Responsibility |
|--------|------|----------------|
| **Entry** | `main.py` | Launch pywebview window, load HTML frontend, bind API interfaces |
| **Frontend** | `front.html` | Dual-panel UI: chat rendering, event polling, settings panel, onboarding wizard |
| **API Layer** | `api.py` | Python ↔ JavaScript bridge, exposing all backend methods to the frontend |
| **Loop** | `loop.py` | ReAct core loop: manages LLM calls, tool orchestration, streaming events, timeout control |
| **Executor** | `executor.py` | Tool implementations including Docker Sandbox and all 14 tool handlers |
| **Event Queue** | `event_queue.py` | Thread-safe producer-consumer queue; frontend polls for streaming events |
| **Config** | `config.py` | Configuration persistence (JSON), API key encryption (win32crypt/keyring) |
| **Tool Definitions** | `tools.py` | OpenAI Function Calling format tool JSON Schema definitions |

### Data Flow

```
User Input → AgentAPI.send_message()
  → AgentLoop.run()
    → _build_full_messages() builds full context
    → client.chat.completions.create(stream=True)
    → stream parse T:/R: events → EventQueue → frontend polling
    → parse tool_calls → ToolExecutor.execute()
    → tool results appended to messages, loop continues
    → no tool_calls → return final reply → finish
```

## Quick Start

### Prerequisites

- **OS**: Windows 10+ (pywebview + win32crypt dependencies)
- **Python**: 3.10+
- **Docker** (optional): for sandboxed execution
- **API Key**: DeepSeek API Key (or any OpenAI-compatible API)

### Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd vibe_agent

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. (Optional) Ensure Docker is installed and running
docker --version
```

### Running

```bash
python main.py
```

On first launch, the onboarding wizard will guide you through:
1. Configuring your API Key and Base URL
2. Selecting a model (deepseek-v4-pro / deepseek-v4-flash)
3. Setting the workspace root directory
4. Tuning agent behavior parameters

These can also be adjusted anytime via the **Settings** panel after launch.

### Packaging as an EXE

```bash
pip install pyinstaller
pyinstaller NORP_Vibe_Coding_Agent.spec
```

## Usage Guide

### Basic Workflow

1. **Describe the task** — use natural language in the input box at the bottom
2. **Agent works** — the agent automatically analyzes, reads files, writes code, and executes commands
3. **Real-time visibility** — the left panel shows reasoning and replies, the right panel shows tool invocation logs
4. **Confirm actions** — a confirmation dialog appears before writing/deleting files (can be disabled in settings)
5. **Complete** — the agent provides a summary when the task is finished

### Example Commands

```
Create a Python Flask web application under H:\vctest
```

```
Read api.py and explain what it does
```

```
Change the default model in config.py to deepseek-v4-flash
```

```
Search the project for all uses of requests.get
```

```
Run pip list to see installed packages
```

### File Upload

- **Drag and drop** files directly onto the input area
- Or use **Ctrl+V** to paste files from the clipboard
- Supported formats: txt, py, js, ts, json, csv, html, css, md, pdf, docx, xlsx, yaml, xml, sql, rs, go, java, kt, swift, c, cpp, sh, and 40+ more

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Enter` | Send message |
| `Ctrl+Enter` | Insert newline |
| `Esc` | Dismiss agent question / dismiss confirmation dialog |

### Configuration Reference

| Setting | Default | Description |
|---------|---------|-------------|
| **Model** | deepseek-v4-pro | LLM model to use |
| **API Base URL** | https://api.deepseek.com | API endpoint URL |
| **Reasoning Effort** | High | Reasoning depth: Off / Low / Medium / High |
| **Temperature** | 1.0 | Generation randomness (disabled when reasoning is on) |
| **Max Output Tokens** | 32767 | Max tokens per response |
| **Project Root** | ~/vibe_workspace | Workspace root directory |
| **Enable Web Search** | Off | Allow the agent to search the web |
| **Confirm Write/Delete** | On | Show confirmation dialog before write/delete |
| **Max Steps** | 128 | Max tool invocation steps per task |
| **Task Timeout** | 0 (disabled) | Task timeout in seconds |
| **Memory** | Enabled | Cross-session conversation memory |
| **Memory Mode** | Full | Full retention / Summary compression |
| **Max Rounds** | 10 | Number of recent conversation rounds to keep |

## Tool List

| Tool | Function |
|------|----------|
| `read_file` | Read file contents, with optional line range |
| `write_file` | Create or overwrite a file |
| `replace_in_file` | Precisely replace a text snippet in a file |
| `delete_file` | Delete a file or directory |
| `list_dir` | List directory contents |
| `search_in_files` | Search for text patterns across project files |
| `exec_cmd` | Execute shell commands |
| `init_project` | Scaffold a new project |
| `install_dependency` | Install pip/npm dependencies |
| `git_commit` | Commit changes to Git |
| `web_search` | DuckDuckGo web search |
| `open_file` | Open a file with the system default application |
| `ask_user` | Ask the user a question or request confirmation |
| `task_done` | Mark task complete and record history |

## Project Structure

```
vibe_agent/
├── main.py              # Entry point, pywebview window main loop
├── front.html           # Frontend UI (dual-panel + settings + onboarding)
├── api.py               # Python ↔ JS API bridge layer
├── loop.py              # ReAct agent core loop
├── executor.py          # Tool executor + Docker Sandbox
├── event_queue.py       # Thread-safe event queue
├── config.py            # Config management & API key encryption
├── tools.py             # Tool JSON Schema definitions
├── requirements.txt     # Python dependencies
├── .gitignore           # Git ignore rules
├── .project_description # Short project description
└── README.md            # Project documentation (this file)
```

## Technical Details

### ReAct Loop

The agent uses the ReAct (Reasoning + Acting) paradigm. Each loop iteration consists of:

1. **Reasoning**: The LLM streams its reasoning process (`reasoning_content`), displayed in real-time in the frontend
2. **Acting**: The LLM's `tool_calls` are parsed and dispatched to `ToolExecutor`
3. **Observation**: Tool execution results are appended to the conversation context, and the next loop iteration begins
4. **Finish**: When the LLM returns no more `tool_calls`, the final text reply is output

### Context Management

- **Stateless API** — the LLM API does not remember history; the client is responsible for sending the full context each time
- **System Prompt** — injects identity definition, tool descriptions, safety constraints, and output specifications
- **History Injection** — previous conversation rounds are injected with a `[History]` prefix so the model can reference them without confusion
- **Memory System** — persists multi-round conversations to `memory/memory.json` for cross-session memory

### Security Design

- **Path Sandbox**: all file operations are restricted within the workspace root directory
- **Command Interception**: blocks dangerous commands like `sudo`, `rm -rf /`, `mkfs`
- **Docker Isolation**: optional sandbox mode runs commands inside isolated containers
- **Write Protection Confirmation**: prompts user confirmation before writing or deleting files
- **API Key Encryption**: stored encrypted using Windows DPAPI or keyring

### Streaming Event Protocol

The frontend polls the `EventQueue` for events. Event prefixes are defined as follows:

| Prefix | Meaning | Display Location |
|--------|---------|------------------|
| `T:` | Reasoning (thinking) content | Chat panel (collapsible area) |
| `R:` | Reply content | Chat panel |
| `C:` | Tool invocation log | Commands panel |
| `U:` | Token usage update | Status bar |
| `E:` | Error message | Chat panel |
| `Q:` | User question | Popup dialog |
| `WC:` | Write/delete confirmation request | Confirmation dialog |
| `D:` | Direct text reply | Chat panel |

## Dependencies

```
pywebview>=5.0        # Desktop WebView window
openai>=1.0.0         # OpenAI SDK (primary API backend)
anthropic>=0.30.0     # Anthropic SDK (search mode)
pywin32>=306          # Windows API (DPAPI encryption)
keyring>=24.0         # Cross-platform secret storage
requests>=2.28.0      # HTTP requests
PyPDF2>=3.0.0         # PDF text extraction
python-docx>=0.8.11   # Word document parsing
openpyxl>=3.1.0       # Excel document parsing
docker>=7.0.0         # Docker SDK (optional)
```

## License

Copyright © 2026 **xingluosama** / **NORP Studio**

---

**NORP Studio** — Building the future of autonomous coding.
