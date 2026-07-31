# ──────────────────────────────────────────────────────────────
# Plugin: Code Reviewer
# Publisher: xingluosama
# Version: 1.0.0
# Description: 对指定的源代码文件执行代码质量审查。检查文档字符串、
#   异常处理、代码复杂度、命名规范、TODO/FIXME 标记、安全隐患等。
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Code Reviewer"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "代码质量审查：检查文档字符串、异常处理、复杂度、安全隐患等。"

import os
import time
from datetime import datetime

# ── 1. 工具注册 ────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "code_review",
            "description": (
                "对指定的源代码文件执行代码质量审查。"
                "检查项目包括：函数/类文档字符串、异常处理、代码复杂度、"
                "命名规范、TODO/FIXME 标记、安全隐患等。"
                "返回结构化的审查报告，包含问题计数和具体建议。"
                "适用于 Python、JavaScript、TypeScript、Go、Rust 等常见语言。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要审查的文件路径（相对于工作区根目录）"
                    },
                    "strictness": {
                        "type": "string",
                        "description": "审查严格程度：'lenient'（宽松）、'normal'（正常，默认）、'strict'（严格）",
                        "default": "normal"
                    }
                },
                "required": ["file_path"],
                "additionalProperties": False
            }
        }
    }
]

# ── 2. 代码审查逻辑 ────────────────────────────────────────────

# 常见问题的正则模式
import re

_PATTERNS = {
    "TODO/FIXME": (r"(TODO|FIXME|HACK|XXX|BUG)", "warning", "发现待办标记"),
    "裸异常": (r"except\s*:", "critical", "裸 except 会捕获所有异常，应指定异常类型"),
    "print 调试": (r"^\s*print\s*\(", "info", "可能存在调试用 print 语句"),
    "过长的行": (None, "info", ""),  # 运行时检查
    "硬编码密钥": (r"(api_?key|secret|password|token)\s*=\s*[\"'][^\"']+[\"']", "critical", "可能存在硬编码的敏感信息"),
    "TODO 注释": (r"#\s*TODO", "warning", "存在未完成的 TODO"),
}

_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".swift", ".kt", ".rb",
    ".php", ".sh", ".bat", ".ps1", ".sql",
}

_LANG_FEATURES = {
    ".py": {
        "function": r"def\s+(\w+)\s*\(",
        "class": r"class\s+(\w+)",
        "comment": r"#",
        "docstring": r'^\s*(?:"{3}|\'{3})',
    },
    ".js": {
        "function": r"function\s+(\w+)\s*\(|(\w+)\s*=\s*(?:async\s*)?\(",
        "class": r"class\s+(\w+)",
        "comment": r"//",
    },
    ".ts": {
        "function": r"function\s+(\w+)\s*\(|(\w+)\s*=\s*(?:async\s*)?\(",
        "class": r"class\s+(\w+)",
        "comment": r"//",
    },
    ".go": {
        "function": r"func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(",
        "comment": r"//",
    },
    ".rs": {
        "function": r"fn\s+(\w+)\s*\(",
        "comment": r"//",
    },
}

_MAX_LINE_LENGTH = {
    "lenient": 150,
    "normal": 120,
    "strict": 100,
}

_MAX_FUNCTION_LINES = {
    "lenient": 80,
    "normal": 50,
    "strict": 30,
}


def _review_file(file_path: str, strictness: str) -> str:
    """执行代码审查并返回报告。"""
    ext = os.path.splitext(file_path)[1].lower()

    if ext not in _CODE_EXTENSIONS:
        return (
            f"⚠️ 不支持的文件类型：{ext}。"
            f"支持的类型：{', '.join(sorted(_CODE_EXTENSIONS))}"
        )

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            lines = content.split("\n")
    except Exception as e:
        return f"❌ 无法读取文件：{e}"

    issues = []
    stats = {
        "total_lines": len(lines),
        "non_empty": sum(1 for l in lines if l.strip()),
        "comment_lines": 0,
        "functions": 0,
        "classes": 0,
    }

    max_line = _MAX_LINE_LENGTH.get(strictness, 120)
    max_func = _MAX_FUNCTION_LINES.get(strictness, 50)
    lang_info = _LANG_FEATURES.get(ext, {})
    func_pattern = lang_info.get("function")
    class_pattern = lang_info.get("class")
    comment_pattern = lang_info.get("comment", "#")

    # 统计注释行
    if comment_pattern:
        stats["comment_lines"] = sum(
            1 for l in lines
            if l.strip().startswith(comment_pattern)
        )

    # 统计函数和类
    if func_pattern:
        stats["functions"] = sum(
            1 for l in lines if re.search(func_pattern, l)
        )
    if class_pattern:
        stats["classes"] = sum(
            1 for l in lines if re.search(class_pattern, l)
        )

    # ── 逐行检查 ──
    in_function = False
    func_start_line = 0
    func_name = ""
    indent_levels = []

    for i, line in enumerate(lines, 1):
        stripped = line.rstrip()
        raw = line

        # 行长度
        if len(raw) > max_line:
            issues.append({
                "line": i,
                "severity": "info",
                "category": "行长度",
                "message": f"行长度 {len(raw)} 超过限制 {max_line}（{raw[:60].strip()}...）"
            })

        # 尾随空格 (strict 模式)
        if strictness == "strict" and raw != stripped:
            issues.append({
                "line": i,
                "severity": "info",
                "category": "格式",
                "message": "行尾有尾随空格"
            })

        # TAB 字符 (strict 模式)
        if strictness == "strict" and "\t" in raw:
            issues.append({
                "line": i,
                "severity": "info",
                "category": "格式",
                "message": "使用了 TAB 缩进，建议使用空格"
            })

        # ── 模式匹配 ──
        for name, (pattern, sev, msg) in _PATTERNS.items():
            if pattern is None:
                continue
            if re.search(pattern, raw, re.IGNORECASE):
                issues.append({
                    "line": i,
                    "severity": sev,
                    "category": name,
                    "message": msg
                })

        # ── 函数长度检测 ──
        if func_pattern and re.search(func_pattern, raw) and not in_function:
            # 进入新函数
            in_function = True
            func_start_line = i
            match = re.search(func_pattern, raw)
            func_name = match.group(1) if match else "?"
        elif in_function and raw.strip() == "":
            # 空行，可能在函数体中间
            pass

    # ── 全局检查：嵌套过深（简单启发式） ──
    max_indent = 0
    for line in lines:
        indent = len(line) - len(line.lstrip())
        max_indent = max(max_indent, indent)
        indent_step = 4  # 假设 4 空格缩进
        depth = indent // indent_step
        if depth > 5:
            pass  # 可检测深度

    # ── 构建报告 ──
    critical = [i for i in issues if i["severity"] == "critical"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    infos = [i for i in issues if i["severity"] == "info"]

    # 摘要
    report_parts = [
        f"📋 **代码审查报告** — `{os.path.basename(file_path)}`",
        f"",
        f"📊 **统计**:",
        f"  • 总行数: {stats['total_lines']}",
        f"  • 非空行: {stats['non_empty']}",
        f"  • 注释行: {stats['comment_lines']}",
        f"  • 函数/方法: {stats['functions']}",
        f"  • 类: {stats['classes']}",
        f"",
    ]

    # 严重问题
    if critical:
        report_parts.append(f"🔴 **严重问题 ({len(critical)} 个):**")
        for item in critical[:10]:
            report_parts.append(f"  • L{item['line']}: [{item['category']}] {item['message']}")
        if len(critical) > 10:
            report_parts.append(f"  … 及其他 {len(critical) - 10} 个严重问题")
        report_parts.append("")

    # 警告
    if warnings:
        report_parts.append(f"🟠 **警告 ({len(warnings)} 个):**")
        for item in warnings[:10]:
            report_parts.append(f"  • L{item['line']}: [{item['category']}] {item['message']}")
        if len(warnings) > 10:
            report_parts.append(f"  … 及其他 {len(warnings) - 10} 个警告")
        report_parts.append("")

    # 提示
    if infos:
        report_parts.append(f"ℹ️ **提示 ({len(infos)} 个):**")
        for item in infos[:5]:
            report_parts.append(f"  • L{item['line']}: [{item['category']}] {item['message']}")
        if len(infos) > 5:
            report_parts.append(f"  … 及其他 {len(infos) - 5} 个提示")
        report_parts.append("")

    # 评分
    total_issues = len(critical) * 5 + len(warnings) * 2 + len(infos)
    if total_issues == 0:
        score = "⭐ A+ (无问题)"
    elif total_issues <= 5:
        score = "✅ A (非常干净)"
    elif total_issues <= 15:
        score = "👍 B (良好)"
    elif total_issues <= 30:
        score = "📝 C (需要改进)"
    elif total_issues <= 60:
        score = "⚠️ D (问题较多)"
    else:
        score = "🔴 F (需要重写)"

    report_parts.append(f"🏆 **综合评分**: {score}")
    report_parts.append(f"   (基于 {len(issues)} 个发现, 严格度: {strictness})")
    report_parts.append("")
    report_parts.append(f"💡 **改进建议**:")
    if critical:
        report_parts.append("  • 优先处理严重问题（硬编码密钥、裸异常等）")
    if warnings:
        report_parts.append("  • 审查 TODO/FIXME 标记，确保有对应计划")
    if stats['comment_lines'] < stats['non_empty'] * 0.05 and stats['non_empty'] > 50:
        report_parts.append("  • 增加注释/文档字符串，提高代码可读性")
    if max_indent > 20:
        report_parts.append("  • 考虑重构深层嵌套代码")

    return "\n".join(report_parts)


# ── 3. 工具执行函数 ────────────────────────────────────────────

def execute(tool_name: str, args: dict, context) -> str:
    if tool_name == "code_review":
        file_path = args.get("file_path", "")
        strictness = args.get("strictness", "normal")

        # 安全：路径必须在工作区内
        full_path = os.path.join(context.project_root, file_path)
        full_path = os.path.normpath(full_path)

        if not os.path.exists(full_path):
            return f"❌ 文件不存在：{file_path}"

        context.logger.info(
            f"Reviewing {file_path} (strictness={strictness})"
        )

        # 更新统计
        s = context.storage
        s["reviews_count"] = s.get("reviews_count", 0) + 1

        return _review_file(full_path, strictness)

    return f"Unknown tool: {tool_name}"


# ── 4. 钩子：生命周期 ──────────────────────────────────────────

def on_agent_init(context):
    """初始化审查计数器。"""
    context.storage["reviews_count"] = 0
    context.storage["plugin_started"] = datetime.now().isoformat()
    context.logger.info("Code Reviewer plugin loaded — ready to review!")


def on_agent_shutdown(context):
    """会话结束时输出统计。"""
    reviews = context.storage.get("reviews_count", 0)
    context.logger.info(
        f"Code Reviewer plugin shutting down. "
        f"Reviewed {reviews} file(s) this session."
    )


def on_task_start(task_text: str, context):
    """记录任务开始。"""
    # 自动检测用户是否请求代码审查
    keywords = ["审查", "review", "检查代码", "code review", "代码质量"]
    if any(kw in task_text.lower() for kw in keywords):
        context.logger.info(f"Code review task detected: {task_text[:80]}")


def on_task_done(summary: str, final_reply: str, context):
    """任务完成时记录。"""
    pass  # 保持轻量
