# norpagent

> Copyright (c) 2026 xingluosama121, MIT Licensed

norpagent 是基于注册表的可插拔组件式 Agent 框架。

模型、工具、会话管理、沙箱环境、任务调度、上下文库、用户界面、外部插件均为
独立组件，通过注册表按名称解析。开发者无需修改框架核心代码，即可替换或组合组件。

## 核心概念

```
+--------------------------------------------------------------+
|                        Registry（注册表）                      |
|   模型 / 工具 / 会话 / 沙箱 / 调度器 / UI / 插件 / 组件 注册于此  |
+--------------------------------------------------------------+
|                        Preset（预设模式）                      |
|   声明式配置：组件选择 + 行为参数，定义一种运行模式              |
+--------------------------------------------------------------+
|                     AgentRuntime（通用循环）                   |
|   与具体模型/工具解耦：消息构建 -> 模型调用 -> 工具执行 -> 回填   |
+--------------------------------------------------------------+
```

- 模型：`registry.register_model("my_model", MyModelProvider())`，预设中按名称引用
- 工具：`registry.register_tool("my_tool", MyTool())`；插件整体注册 `registry.register_plugin(MyPlugin())`
- 可替换组件：会话存储、沙箱、任务调度、UI 适配器、上下文库、项目管理器
- 通用组件命名空间：`registry.register_component(kind, name, factory)`；预设声明
  `components={"kind": "name"}`

## 安装

```bash
pip install norpagent                  # 核心包，无第三方依赖（Mock 模型 + 内置工具）
pip install norpagent[openai]          # OpenAI 兼容服务模型适配器（OpenAI/DeepSeek/Qwen/vLLM/Ollama）
pip install norpagent[anthropic]       # Claude 系列模型适配器
pip install norpagent[web]             # 联网检索增强（requests + bs4；不安装时使用标准库引擎）
pip install norpagent[security]        # 插件签名校验（cryptography；不安装时签名按不受信任处理）
pip install norpagent[all]             # 全部可选依赖
```

模型适配器与工具随核心包注册，SDK 按需懒加载：
未安装对应 extras 时不影响注册与列模式，只有实际调用时才给出安装提示。

## 快速开始（np() 入口，v0.5 起）

```python
import norpagent as np

np()                        # standard 预设 + Web 前端（全部内置工具）
running = True
while running:
    if np.stop() == True:   # 生命周期函数：应用结束即退出
        running = False
```

启动后控制台打印 `[norpagent] listening on http://127.0.0.1:8787/`，
浏览器访问该地址打开聊天界面（front.html：多标签会话 / 流式渲染 /
设置 / 插件面板）。界面语言可用 `np(language="zh_CN")` 指定（默认 English）。

- 默认预设为 **standard**：模型可用全部内置工具（文件 / 命令 / 联网 /
  上下文管理 / 项目管理 / 长周期任务协作）。未配置 API Key 时自动使用
  mock 模型，在界面「设置」中填入模型与 Key 后切换为真实模型；
- 「设置 → 推理强度」映射为模型的 `reasoning_effort`（OpenAI 推理系列
  与 DeepSeek V4 透传；DeepSeek V4 的思维链 `reasoning_content` 与正文
  分开传输，发生工具调用的轮次由内核原样回传；UI 选「关」时转译为
  DeepSeek V4 的 `thinking=disabled`），推理开启时温度参数自动禁用；
- 「项目根目录」默认值按操作系统给出（Windows：`文档\NORP-Agent`；
  macOS：`~/Documents/NORP-Agent`；Linux：`~/norpagent-workspace`），
  「浏览」按钮打开目录选择框（后端只读列举实现导航）。

**Web UI 行为与持久化**：

- **设置持久化**：模型 / API Key / 语言 / 插件目录等设置在浏览器保存后
  持久化到 `~/.norpagent/webui_config.json`（环境变量
  `NORPAGENT_WEBUI_CONFIG` 可覆盖），刷新页面、重启 np() 进程后仍保留；
- **页面防缓存**：页面响应带 `Cache-Control: no-store`；
- **客户端断连处理**：浏览器刷新 / 关闭标签页 / curl 中断等客户端断连
  静默处理，不向控制台输出 traceback；
- **端口顺延**：8787 被占用时向后顺延最多 10 个端口，控制台打印实际绑定端口；
- **请求体防护**：负数 Content-Length 按无请求体处理；超过 1MB 的请求体拒收；
- **事件路由**：内核会话 id 与浏览器标签页 id 保持一致，
  思考 / 回复 / 任务结束事件投递到对应会话。

**架构槽位 + 地址函数**：除底层最小内核（ArchLayer / 地址解析 / 注册表 /
事件总线）外，全部组件都是槽位。不填地址 = 使用默认逻辑；填地址 =
按地址接入实现：

```python
np(preset="standard")                                  # 预设模式
np(model="openai_compat")                              # 模型
np(async_loop="myapp.loop:create")                     # 事件循环系统
np(frontend="norpagent.frontends.console:ConsoleFrontend")  # 命令行前端
np(frontend="norpagent.frontends.headless:HeadlessFrontend")# 无头（输出打印 stdout）
np(port=9000, language="zh_CN")                        # Web 端口与界面语言
np(session="sqlite", sandbox="pooled", security="high")# 组件替换
loop = np.nasyncio("myapp.loop:create")                # 循环系统独立架构函数
```

单次任务（headless 模式，输出打印到 stdout）：

```python
np(prompt="总结 README", preset="standard")
while True:
    if np.stop():
        break
print(np.current().last_result.final_content)
```

开发手册见 [docs/DEVELOPER_MANUAL.md](docs/DEVELOPER_MANUAL.md)。

## 快速开始（手工装配）

```python
from norpagent import Registry, AgentRuntime
from norpagent.builtin import install_defaults
from norpagent.modes import register_all_presets

reg = Registry()
install_defaults(reg)          # 注册内置模型/工具/会话/沙箱/调度/UI/组件
register_all_presets(reg)      # 注册 standard / ptc / minimal / creative 预设

agent = AgentRuntime(reg, preset="minimal")
result = agent.run("hello")
print(result.final_content)
```

命令行：

```bash
python -m norpagent --list-modes        # 列出全部预设模式
python -m norpagent --mode minimal      # minimal 模式交互 REPL
python -m norpagent --mode standard --model mock --prompt "..."   # 单次任务（mock 模型）
python -m norpagent --mode standard --model-name deepseek-v4-flash --base-url https://api.deepseek.com/v1
python -m norpagent --mode ptc --prompt "用代码调用 echo 工具" --model mock
python -m norpagent --mode-file my_mode.py   # 加载自定义模式文件
python -m norpagent --mode standard --session sqlite --call-timeout 60   # 持久会话 + 硬超时
python -m norpagent --mode standard --ui web --port 8787      # Web UI（HTTP + SSE 事件流）
python -m norpagent --mode standard --plugin-dir ./my_plugins  # 加载外部插件（签名→审计→导入限制）
python -m norpagent plugin-sign --gen                        # 生成插件签名密钥对
python -m norpagent plugin-sign my_plugin.py --key <私钥hex>  # 对插件签名（生成 .sig）
```

## 预设模式

| 模式 | 组件组合 |
|------|----------|
| `minimal` | mock/openai 模型 + echo/clock 工具（确定性环境，用于模型基准测试） |
| `standard` | openai_compat 模型 + 文件/命令/联网/上下文/项目/任务工具（持久化会话 + 长周期任务调度） |
| `ptc` | openai_compat 模型 + run_python + 业务工具（PTC：模型生成 Python 代码组合多步工具调用） |
| `creative` | mock 模型 + 基础工具，允许任意覆盖（支持 --mode-file 加载自定义模式文件） |

## 内置组件清单

**模型适配器**
| 名字 | 说明 |
|------|------|
| `mock` | 确定性脚本应答模型（基准测试 / 调试，无第三方依赖） |
| `openai_compat` | OpenAI 兼容服务：OpenAI / DeepSeek / Qwen / vLLM / Ollama（norpagent[openai]） |
| `anthropic` | Claude 系列（norpagent[anthropic]） |

**工具**
| 名字 | 说明 |
|------|------|
| `echo` / `get_time` | 基础工具（minimal 模式） |
| `run_python` | PTC 执行核心：代码在**沙箱子进程**中隔离执行（AST 预检 + 受限 builtins + 干净命名空间 + 超时强杀），`call_tool()` 经协议通道组合多步工具调用 |
| `file_read` / `file_write` / `file_list` / `file_delete` | 文件操作：限定工作区根目录，拒绝绝对路径与 `..` 穿越 |
| `exec_cmd` | 命令执行：走沙箱协议（替换沙箱实现即替换执行环境） |
| `web_search` / `web_fetch` / `web_extract_links` | 联网检索：SSRF 防护（拒绝内网/回环地址），requests+bs4 优先、标准库兜底 |
| `context_add` / `context_search` / `context_list` / `context_delete` | 上下文管理：跨会话可检索知识库（FTS5 + BM25，中英混合分词） |
| `project_status` | 项目管理：文件统计、最近修改、git 分支与变更感知 |
| `task_submit` / `task_list` / `task_status` / `task_cancel` | 长周期任务协作：子任务按优先级排队、多智能体编排、断点续跑 |

**会话 / 沙箱 / 调度 / UI / 组件**
| 名字 | 说明 |
|------|------|
| `memory` / `sqlite` | 会话存储：进程内 / SQLite 持久化（默认 `~/.norpagent/sessions.db`） |
| `subprocess` / `pooled` | 沙箱：单发子进程 / 池化（复用 + 并发上限 + 超时强杀整个进程树） |
| `simple` / `persistent` | 任务调度：进程内 FIFO / SQLite 持久化优先级队列（崩溃后 `resume()` 续跑） |
| `console` / `web` | UI：控制台 / Web（HTTP + SSE 事件流，无第三方依赖） |
| `context_store=fts5` | 上下文库组件（默认 `~/.norpagent/context.db`） |
| `project_manager=basic` | 项目管理组件（工作区 `.norpagent/project.json` 元数据） |

## 上下文管理

Agent 将长周期任务中的中间结论、外部资料、代码片段写入上下文库，
跨会话、跨任务检索复用：

```python
from norpagent.builtin.context import FTS5ContextStore

store = FTS5ContextStore()                    # 默认 ~/.norpagent/context.db
store.add("网络模块使用 requests 与 urllib 双引擎降级", source="notes", title="网络")
hits = store.search("网络 引擎")              # BM25 相关度排序，中英混合分词
```

预设声明 `components={"context_store": "fts5"}` 后，工具通过
`ctx.context_store` 访问；替换实现 = 注册另一个组件工厂，无需修改核心代码。

## 任务调度与多智能体协作

```python
from norpagent.builtin.scheduler.persistent import PersistentTaskScheduler
from norpagent.protocols.scheduler import AgentTask

sched = PersistentTaskScheduler()             # 任务落盘，进程重启不丢
sched.submit(AgentTask(id="", user_input="整理文档", params={"priority": 0}))
sched.submit(AgentTask(id="", user_input="跑测试", params={"priority": 5}))
sched.resume()                                # 崩溃恢复：遗留 running 任务重新入队

agent = AgentRuntime(reg, preset="standard")
results = sched.drain(agent.task_runner())    # 按优先级执行；子任务可指定不同 preset
```

模型通过 `task_submit` 工具提交子任务；`task.preset_name` 指定不同模式，
即不同子 Agent（共享同一注册表与组件仓库）。

## 外部插件

```python
from norpagent.plugins import install_plugin_dirs

loader = install_plugin_dirs(registry, ["my_plugins"], config={
    "plugin_security_audit": "warn",          # off / warn / block
    "plugin_security_import_restrict": "off", # off / safe / strict
    "plugin_signature_verify": True,          # Ed25519 验签（norpagent[security]）
    "plugin_network_policy": "deny",          # deny / audited_public / public_only / allow_all
})
for info in loader.plugins:
    print(info.name, info.signature_status, info.enabled)
```

插件格式：模块级 `PLUGIN_NAME` / `TOOLS`（OpenAI function schema）/
`execute(tool_name, args, ctx)` / 15 个生命周期钩子 / `APPROVAL_HINTS`。

插件加载安全管线：**发现 → 签名校验（invalid 拒绝）→ AST 审计
（critical 拒绝，含 getattr / __dict__ 反射绕过检测）→ 权限声明校验 →
导入限制（静态预检 + 运行时 meta_path 拦截）→ 适配注册**。
受信任签名放宽审计级别。

**PluginSystem 门面与进程级隔离**：

```python
from norpagent.plugins import PluginSystem

ps = PluginSystem(registry, ["my_plugins"], config={"plugin_isolation": "auto"})
ps.load(); ps.status(); ps.reload("my_tool"); ps.shutdown()
```

插件模块级 `ISOLATION = "process"` 即启用**进程级插件隔离**：插件代码
只在宿主子进程（`python -m norpagent.plugins.host`，JSON 行协议 RPC）中
加载执行，工具与钩子经 RPC 转发，崩溃自动重启，钩子限时执行。
加载管线每个阶段（发现/加载/审计/注册）都是钩子，HookVeto 可终止当前阶段。

## 9 层 29 钩子体系（norpagent.hooks）

执行结构以钩子 API 暴露，可被钩子干预：

```python
from norpagent.hooks import before_model_call, after_tool_call, HookVeto, HookLayer

before_model_call.subscribe(logger, system=reg)        # 模块级订阅
agent.hooks.after_tool_call.subscribe(observer)        # 运行时绑定视图

def forbid_rm(event):                                   # 可变钩子：抛出 HookVeto 终止当前执行结构
    if event.get("tool_name") == "file_delete":
        raise HookVeto("禁止删除文件")
agent.hooks.before_tool_call.subscribe(forbid_rm)

net = HookLayer("L10_network", order=100)              # 自定义层 + 自定义钩子
net.hook("before_network_call", mutating=True)
agent.hooks.install_layer(net)
```

九层：L1 运行时生命周期 → L2 任务 → L3 输入 → L4 会话与历史 →
L5 消息组装 → L6 步骤 → L7 模型调用 → L8 工具调用 → L9 结果定型。
可变钩子可改写数据流（输入/消息/参数/结果），详见 docs/hooks.md。

## 安全系统入口：norpagent.safe()

```python
from norpagent import safe

kit = safe(reg, level="standard")     # 按级别启用安全组件（basic/standard/high）
kit.scan_input(text)                  # 越狱/注入检测
kit.harden(prompt, tools)             # 提示词加固
kit.audit_file(path)                  # AST 审计
kit.verify_plugin(path)               # 签名校验
kit.check_network(url)                # SSRF 裁决
```

安全组件（防护/加固/审批/审计/签名/网络策略/隔离策略）统一由
`norpagent.safe()` 提供：输入防护 = L3 钩子、提示词加固 = L5 钩子、
其余运行态策略 = `registry.security`，内核不直接依赖
`norpagent.security`。详见 docs/security.md。

## 安全防护（norpagent.security）

```python
from norpagent.security import (
    SourceAuditor,       # AST 源码审计：危险调用/导入/动态绕过
    NetworkPolicy,       # 插件网络策略 + SSRF 防护
    ApprovalPolicy,      # 人工审批：原生工具确认 + 插件工具审批
    scan_message,        # 越狱/提示词注入检测（DAN/角色覆写/零宽字符/Base64）
    SignatureVerifier,   # Ed25519 插件签名校验
)
```

内核集成（按需开启）：

- `params["jailbreak_guard"] = True`：用户输入进入模型前拦截越狱/注入；
- `params["harden_prompt"] = True`：系统提示词附加安全加固规则；
- `params["approval_policy"]` / `params["approval_config"]`：
  工具调用前人工审批（经 UI `ask_user`，用户否定即阻止）。

## 预设模式定义示例

```python
from norpagent.kernel.presets import Preset

MY_PRESET = Preset(
    name="my_mode",
    description="自定义模式",
    model="openai_compat",           # 任意已注册模型名
    tools=["file_read", "exec_cmd"], # 任意已注册工具名
    session="sqlite",                # 会话存储（memory / sqlite）
    sandbox="pooled",                # 沙箱（subprocess / pooled）
    scheduler="persistent",          # 任务调度（simple / persistent）
    ui="web",                        # 用户界面（console / web）
    components={                     # 通用组件装配
        "context_store": "fts5",
        "project_manager": "basic",
    },
    params={
        "max_steps": 32,
        "temperature": 0.7,
        "task_timeout": 0,           # 任务超时秒数（轮次边界检查，0=不限）
        "call_timeout": 60,          # 单次模型调用硬超时秒数（阻塞中途强杀，0=不限）
        "workspace_root": ".",       # 文件类工具的工作区根目录
        "jailbreak_guard": True,     # 越狱/注入拦截
        "harden_prompt": True,       # 系统提示词加固
    },
)
```

### 超时机制（两级）

- `task_timeout`：任务级超时，在轮次边界检查（模型调用是同步阻塞的）；
- `call_timeout`：模型调用级**硬超时**，对应模型 API 请求超时机制。
  超时后主循环立即放弃等待返回 `timeout` 结果，后台请求线程被标记取消
  （`params["_cancel_event"]`，适配器流式循环据此尽早退出），并在
  `shutdown()` 时统一回收。

## 插件协议（进程内）

```python
class MyPlugin:
    name = "my_plugin"
    version = "1.0.0"
    publisher = "me"

    def get_tools(self):            # 向 Agent 注册工具
        return [EchoTool()]

    def get_hooks(self):            # 订阅 Agent 生命周期事件
        return {"on_task_done": self.on_done}

    def on_done(self, event):
        print("task done:", event.payload)
```

外部（文件）插件经 `norpagent.plugins` 加载器加载，经过签名校验 /
审计 / 导入限制 / 网络策略 / 人工审批安全管线。

## 安全行为

- **工作区路径锁定**：文件类工具要求路径相对于工作区根目录；绝对路径、`..` 穿越、
  符号链接跳出被拒绝；
- **SSRF 防护**：联网工具仅允许公网 http/https；DNS 解析后拒绝回环/内网/链路本地地址；
- **沙箱池超时强杀**：命令超时终止整个进程树（Windows taskkill /T，POSIX 进程组 SIGKILL）；
  被强杀的实例不再复用；
- **插件安全管线**：签名 → AST 审计（含反射绕过检测）→ 权限声明 → 导入限制；
- **越狱防护**：DAN/角色覆写/解除限制模板匹配 + 零宽字符 + Unicode 同形字 + Base64 隐藏指令检测；
- **PTC 受限执行**：`run_python` 内置函数白名单 + 禁用 import。

## 文档

| 文档 | 内容 |
|------|------|
| [docs/hooks.md](docs/hooks.md) | 9 层 29 钩子：语义、可变钩子返回契约、自定义层/钩子 |
| [docs/security.md](docs/security.md) | norpagent.safe()：三级预设、SafetyKit API、与内核的边界 |
| [docs/presets.md](docs/presets.md) | standard/ptc/minimal/creative 四种预设 |
| [docs/plugins.md](docs/plugins.md) | 插件系统：安全管线、PluginSystem 门面、进程级隔离 |
