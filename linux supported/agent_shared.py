# Vibe Coding Agent - 共享代理工具函数
# 从 loop.py 和 async_loop.py 提取公共代码，消除 DRY 重复
# Copyright (c) 2026 xingluosama

from datetime import datetime
from typing import List, Dict, Optional

from tools import BUILTIN_TOOLS


def build_system_prompt(project_root: str, enable_web_search: bool) -> str:
    """构建系统提示词（loop.py / async_loop.py 共用）。"""
    now = datetime.now()
    date_str = now.strftime("%Y年%m月%d日 %H:%M:%S")
    weekday_str = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
    prompt = (
        "[身份]\n"
        "你是 Vibe Coding 自主编程智能体，采用 ReAct 架构。\n"
        "唯一目标：将用户自然语言指令转化为精确的代码操作，主动解决问题，而非被动问答。\n\n"
        f"[环境]\n"
        f"当前系统时间：{date_str}（周{weekday_str}）\n"
        f"工作区根目录：{project_root}\n\n"
        "[工具使用原则]\n"
        "- 先读后写：覆盖或修改文件前，必须先用 read_file 读取现有内容\n"
        "- 主动探索：不确定项目结构时，先用 list_dir 了解目录布局\n"
        "- 批量操作：多个无依赖的工具调用应在一次响应中并行发起\n"
        "- 最小权限：只创建必要的文件，只安装声明的依赖\n"
        "- 精准修改：优先使用 replace_in_file 进行针对性编辑，避免用 write_file 重写整个文件，以节省 token\n\n"
        "[安全约束]\n"
        "- 删除文件或目录前，必须调用 ask_user 获得用户确认\n"
        "- 执行 shell 命令时禁止 sudo、rm -rf /、mkfs 等危险操作\n"
        "- 所有文件路径限定在工作区根目录内，不得包含 .. 或绝对系统路径\n\n"
        "[任务完成]\n"
        "任务完成时调用 task_done，传入总结和涉及的主要代码路径，系统自动写入历史记录。\n\n"
        "[可用工具]\n"
        "read_file(path, start_line?, end_line?): 读取文件内容。可指定行范围只读取需要的代码片段，节省 token。\n"
        "write_file(path, content): 创建或覆盖文件。覆盖前建议先 read_file 备份原内容。\n"
        "replace_in_file(path, old_str, new_str): 替换文件中的指定文本片段。old_str 必须精确匹配文件中唯一一处。若匹配多处则报错，需提供更多上下文以唯一确定。用于针对性修改，避免重写整个文件。\n"
        "list_dir(path?): 列出目录内容，用于了解项目结构。\n"
        "search_in_files(pattern, path?): 在文件中搜索文本模式。\n"
        "delete_file(path): 删除文件或目录。不可逆操作，执行前应请求用户确认。\n"
        "exec_cmd(command, timeout?): 执行 shell 命令。禁止 sudo、rm -rf / 等危险操作。对不确定的命令先加 --dry-run 预览。\n"
        "init_project(type, name): 脚手架初始化新项目，自动创建目录结构。\n"
        "install_dependency(package, manager?): 安装项目依赖。\n"
        "git_commit(message): 提交所有变更到 Git 仓库。\n"
        "ask_user(question): 向用户提问或请求确认。当需要用户做出选择、澄清需求、或确认危险操作时调用。\n"
        "task_done(summary, code_path?): 标记任务完成。完成后会自动将任务摘要和代码路径记录到 .agent_history.json。\n"
        "open_file(path): 用系统默认程序打开文件。用户说「打开某个文件」时调用此工具。支持所有常见文件类型（图片、文档、网页等）。\n"
        "read_clipboard(): 读取系统剪贴板中的文本内容。用户说「读取剪贴板」「粘贴」「看看剪贴板里有什么」时调用。\n"
        "write_clipboard(text): 将文本写入系统剪贴板。用户说「复制到剪贴板」「拷贝这段文字」时调用。\n"
    )
    if enable_web_search:
        prompt += "web_search(query): 联网搜索实时信息，适用于需要最新数据的场景。\n"
    prompt += (
        "\n[输出规范]\n"
        "- 调用工具时系统自动处理格式，你只需正常推理和决策\n"
        "- 任务完成后输出简洁的自然语言总结，无需列出每一步细节\n"
        "- 遇到阻塞性问题时主动调用 ask_user，不要猜测用户意图\n"
    )
    prompt += (
        "\n[历史消息处理]\n"
        "对话中带有 `[历史]` 前缀的消息是之前的用户输入，这些消息已经发生过，请参考它们来理解上下文。\n"
        "不要对 `[历史]` 消息做出新的响应或执行新的任务——它们只是背景信息。\n"
        "只有最后一条不带 `[历史]` 前缀的用户消息才是当前需要处理的任务。\n"
        "当用户询问关于自身信息（如名字、偏好等）时，应优先从 `[历史]` 消息中检索相关事实。\n"
    )
    return prompt


def build_full_messages(user_message: str, project_root: str,
                         enable_web_search: bool,
                         history: Optional[List[Dict]] = None,
                         memory_content: str = "") -> list:
    """构建完整的消息列表（loop.py / async_loop.py 共用）。

    核心策略：
    1. 系统级时间戳消息（让模型感知当前时间）
    2. 历史 user 消息回传，但添加 [历史] 前缀
    3. assistant 消息完整回传（含 reasoning_content、tool_calls）
    4. tool 消息完整回传（工具执行结果）
    5. 当前用户消息注入时间戳前缀
    6. 注入持久化记忆
    """
    current_time = datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    system_prompt = build_system_prompt(project_root, enable_web_search)

    full_messages = [{"role": "system", "content": system_prompt}]

    if memory_content:
        full_messages.append({"role": "system", "content": memory_content})

    full_messages.append({
        "role": "system",
        "content": f"[SystemInfo]当前系统时间：{current_time}。"
    })

    if history:
        for m in history:
            role = m.get("role", "")
            if role == "user":
                content = m.get("content", "")
                full_messages.append({
                    "role": "user",
                    "content": f"[历史] {content}"
                })
            elif role == "assistant":
                msg = {"role": "assistant", "content": m.get("content", "")}
                if m.get("reasoning_content"):
                    msg["reasoning_content"] = m["reasoning_content"]
                if m.get("tool_calls"):
                    msg["tool_calls"] = m["tool_calls"]
                if m.get("web_search_calls"):
                    msg["web_search_calls"] = m["web_search_calls"]
                full_messages.append(msg)
            elif role == "tool":
                full_messages.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": m.get("content", "")
                })

    full_messages.append({
        "role": "user",
        "content": f"[SystemInfo]当前系统时间：{current_time}\n{user_message}"
    })

    return full_messages


def build_tools_openai(plugin_manager, enable_web_search: bool) -> list:
    """构建 OpenAI Chat Completions 格式的工具列表。"""
    tools = list(BUILTIN_TOOLS)
    if plugin_manager:
        plugin_tools = plugin_manager.get_tools()
        tools.extend(plugin_tools)
    if not enable_web_search:
        tools = [t for t in tools if t["function"]["name"] != "web_search"]
    return tools


def build_tools_anthropic(plugin_manager, enable_web_search: bool) -> list:
    """构建 Anthropic 格式的工具列表。"""
    tools = [{"type": "web_search_20250305", "name": "web_search"}]
    for t in BUILTIN_TOOLS:
        name = t["function"]["name"]
        if name == "web_search":
            continue
        func = t["function"]
        tools.append({
            "name": name,
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {"type": "object", "properties": {}})
        })
    if plugin_manager:
        for t in plugin_manager.get_tools():
            func = t["function"]
            tools.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}})
            })
    return tools


def build_responses_tools(plugin_manager, enable_web_search: bool) -> list:
    """构建 Responses API 格式的工具列表。

    Responses API 工具格式要求 name/description/parameters 在顶层，
    而不是嵌套在 function 字段里。
    web_search 使用服务端原生工具。
    """
    cc_tools = build_tools_openai(plugin_manager, enable_web_search)
    tools = []
    for t in cc_tools:
        func = t.get("function", {})
        name = func.get("name", "")
        if enable_web_search and name == "web_search":
            continue
        tools.append({
            "type": "function",
            "name": name,
            "description": func.get("description", ""),
            "parameters": func.get("parameters", {"type": "object", "properties": {}}),
        })
    if enable_web_search:
        tools.append({"type": "web_search"})
    return tools


def build_responses_input(messages: list) -> list:
    """将 OpenAI 格式 messages 转换为 Responses API 的 input items。"""
    items = []
    for m in messages:
        role = m.get("role", "")
        if role == "system":
            items.append({
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": m.get("content", "")}]
            })
        elif role == "user":
            items.append({
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": m.get("content", "")}]
            })
        elif role == "assistant":
            reasoning = m.get("reasoning_content", "")
            if reasoning:
                items.append({
                    "type": "reasoning",
                    "content": [{"type": "reasoning_text", "text": reasoning}]
                })
            text = m.get("content", "")
            if text:
                items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}]
                })
            for wc in m.get("web_search_calls", []):
                items.append({
                    "type": "web_search_call",
                    "id": wc.get("id", ""),
                    "status": wc.get("status", "completed"),
                    "query": wc.get("query", "")
                })
            for tc in m.get("tool_calls", []):
                items.append({
                    "type": "function_call",
                    "call_id": tc.get("id", ""),
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"]
                })
        elif role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id", ""),
                "output": m.get("content", "")
            })
    return items


def get_thinking_extra_body(think_level: str) -> dict:
    """返回 thinking extra_body 配置（仅 thinking 字段）。"""
    if think_level == "关":
        return {"thinking": {"type": "disabled"}}
    return {"thinking": {"type": "enabled"}}


def get_reasoning_effort(think_level: str) -> Optional[str]:
    """返回 reasoning_effort 值，思考关闭时返回 None。"""
    if think_level == "关":
        return None
    effort_map = {"低": "low", "中": "medium", "高": "max"}
    return effort_map.get(think_level, "max")


def convert_openai_messages_to_anthropic(messages: list) -> list:
    """将 OpenAI 格式消息转换为 Anthropic 格式。"""
    result = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue
        elif role == "user":
            result.append({"role": "user", "content": msg.get("content", "")})
        elif role == "assistant":
            content = msg.get("content", "")
            result.append({"role": "assistant", "content": content})
    return result
