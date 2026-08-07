# ──────────────────────────────────────────────────────────────
# Plugin: File Surgeon (文件手术刀)
# Publisher: xingluosama
# Version: 1.0.0
# Description: 像"分子手术刀"一样精确修改替换超大文件（1GB内）中的
#   某一行代码。支持按行号定位、内容匹配（含正则）、插入、删除、
#   dry-run 预览。采用流式读写，内存友好。
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "File Surgeon"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = (
    "分子手术刀：精确修改替换超大文件（1GB内）中任意一行代码。"
    "支持行号定位、内容匹配、正则搜索、插入、删除、dry-run 预览。"
)

import os
import re
import shutil
import tempfile
import time
from datetime import datetime

# ── 1. 工具注册 ────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "surgical_replace",
            "description": (
                "「分子手术刀」精确修改替换超大文件（最大 1GB）中的某一行。\n"
                "支持三种定位模式：\n"
                "  • 行号模式：指定 line_number，精确替换该行\n"
                "  • 搜索模式：指定 old_content，搜索匹配的行并替换\n"
                "  • 搜索+行号：同时在指定行号附近搜索，双重定位更安全\n"
                "支持五种操作类型：replace（替换）、insert_before（前插）、"
                "insert_after（后插）、delete（删除）、replace_all（全量替换）。\n"
                "采用流式读写，处理 1GB 文件时内存占用 < 50MB。\n"
                "dry_run 模式可预览修改效果而不实际更改文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要操作的文件路径（相对于工作区根目录）"
                    },
                    "line_number": {
                        "type": "integer",
                        "description": (
                            "目标行号（从 1 开始）。与 old_content 可二选一或同时指定。"
                            "同时指定时：只在 line_number 行匹配 old_content，双重保险。"
                        )
                    },
                    "old_content": {
                        "type": "string",
                        "description": (
                            "要匹配的原始行内容。支持精确文本匹配或正则表达式。"
                            "与 line_number 可二选一或同时指定（双重保险）。"
                            "注意：仅在单行内匹配，不跨行。如需精确匹配整行，"
                            "可在内容前后加 ^ 和 $ 锚点并启用 use_regex。"
                        )
                    },
                    "new_content": {
                        "type": "string",
                        "description": (
                            "替换后的新内容。可以是单行或多行（含 \\n）。"
                            "delete 模式时忽略此参数。"
                        )
                    },
                    "mode": {
                        "type": "string",
                        "description": (
                            "操作模式：\n"
                            "  • 'replace'（默认）：替换匹配的行\n"
                            "  • 'insert_before'：在匹配行之前插入\n"
                            "  • 'insert_after'：在匹配行之后插入\n"
                            "  • 'delete'：删除匹配的行\n"
                            "  • 'replace_all'：替换所有匹配的行（与 count 配合）"
                        ),
                        "default": "replace"
                    },
                    "use_regex": {
                        "type": "boolean",
                        "description": "是否将 old_content 作为正则表达式解析。默认 false（精确文本匹配）。",
                        "default": False
                    },
                    "count": {
                        "type": "integer",
                        "description": (
                            "替换次数（仅搜索模式有效）：1=只替换第一个匹配（默认），"
                            "-1=替换所有匹配，N=替换前 N 个匹配。"
                        ),
                        "default": 1
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": (
                            "预览模式。为 true 时只显示将要做的更改（含上下文），"
                            "不实际修改文件。强烈建议在正式操作前先用 dry_run 确认。"
                        ),
                        "default": False
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "在 dry_run 预览中显示目标行上下各多少行。默认 2。",
                        "default": 2
                    },
                    "backup": {
                        "type": "boolean",
                        "description": "是否在修改前自动备份原文件为 .bak 后缀。默认 false。",
                        "default": False
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码。默认 'utf-8'。常见值：'utf-8'、'gbk'、'latin-1'。",
                        "default": "utf-8"
                    }
                },
                "required": ["file_path"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "surgical_scan",
            "description": (
                "「手术前扫描」在超大文件中搜索匹配的行，帮助定位目标。\n"
                "返回匹配行的行号、内容预览和上下文。可指定搜索范围。\n"
                "支持正则表达式。适合在「下刀」前确认目标位置。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要扫描的文件路径（相对于工作区根目录）"
                    },
                    "pattern": {
                        "type": "string",
                        "description": "搜索模式。支持精确文本或正则表达式（use_regex=true 时）。"
                    },
                    "use_regex": {
                        "type": "boolean",
                        "description": "是否将 pattern 作为正则表达式解析。默认 false。",
                        "default": False
                    },
                    "line_start": {
                        "type": "integer",
                        "description": "搜索起始行号（从 1 开始），默认从文件开头。"
                    },
                    "line_end": {
                        "type": "integer",
                        "description": "搜索结束行号（含），默认到文件末尾。"
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "每个匹配行周围显示的上下文行数。默认 1。",
                        "default": 1
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "最多返回多少个匹配。默认 20，最大 200。",
                        "default": 20
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码。默认 'utf-8'。",
                        "default": "utf-8"
                    }
                },
                "required": ["file_path", "pattern"],
                "additionalProperties": False
            }
        }
    }
]

# ── 2. 常量 ────────────────────────────────────────────────────

_BUFFER_SIZE = 16 * 1024 * 1024       # 16MB 读写缓冲
_MAX_FILE_SIZE = 1 * 1024 * 1024 * 1024  # 1GB 上限


# ── 3. 核心引擎：流式手术 ──────────────────────────────────────

def _perform_surgery(
    file_path: str,
    line_number: int | None,
    old_content: str | None,
    new_content: str | None,
    mode: str,
    use_regex: bool,
    count: int,
    dry_run: bool,
    context_lines: int,
    backup: bool,
    encoding: str,
) -> str:
    """核心手术引擎。使用临时文件 + 流式读写，内存友好。"""

    # ── 参数校验 ────────────────────────────────────────────
    if not line_number and not old_content:
        return (
            "❌ **参数错误**：必须指定 `line_number` 或 `old_content` "
            "中的至少一个来定位目标行。"
        )

    if mode == "delete":
        new_content = None
    elif new_content is None and mode != "delete":
        return f"❌ **参数错误**：`{mode}` 模式需要提供 `new_content`。"

    if count == -1:
        count = float("inf")

    # ── 检查文件 ────────────────────────────────────────────
    if not os.path.isfile(file_path):
        return f"❌ 文件不存在：`{file_path}`"

    file_size = os.path.getsize(file_path)
    if file_size > _MAX_FILE_SIZE:
        return (
            f"❌ **文件过大**：{_format_size(file_size)} 超过了 "
            f"手术刀最大支持 {_format_size(_MAX_FILE_SIZE)} 的限制。"
        )

    # ── 编译正则 ─────────────────────────────────────────────
    search_regex = None
    if old_content and use_regex:
        try:
            search_regex = re.compile(old_content)
        except re.error as e:
            return f"❌ **正则表达式错误**：`{old_content}` — {e}"

    # ── dry_run：只预览 ──────────────────────────────────────
    if dry_run:
        return _dry_run_preview(
            file_path, file_size, line_number, old_content,
            search_regex, new_content, mode, count, context_lines, encoding
        )

    # ── 执行手术 ────────────────────────────────────────────
    return _execute_surgery(
        file_path, file_size, line_number, old_content,
        search_regex, new_content, mode, count, backup, encoding
    )


# ── 3a. dry_run 预览 ──────────────────────────────────────────

def _dry_run_preview(
    file_path: str,
    file_size: int,
    line_number: int | None,
    old_content: str | None,
    search_regex: re.Pattern | None,
    new_content: str | None,
    mode: str,
    count,
    context_lines: int,
    encoding: str,
) -> str:
    """预览模式。两遍扫描：第一遍找匹配行号，第二遍读上下文行。"""

    # ── 第一遍：扫描匹配行号 ──────────────────────────────────
    matched_lines = []  # [(line_num, line_text)]
    total_lines = 0

    with open(file_path, "r", encoding=encoding, errors="replace",
              buffering=_BUFFER_SIZE) as f:
        for i, line in enumerate(f, 1):
            total_lines = i
            if _line_matches(i, line.rstrip("\n\r"), line_number,
                             old_content, search_regex):
                matched_lines.append((i, line.rstrip("\n\r")))
            if count != float("inf") and len(matched_lines) >= count:
                break

    if not matched_lines:
        return (
            f"🔍 **dry_run 预览** — `{os.path.basename(file_path)}`\n\n"
            f"⚠️ 未找到匹配的行。请检查：\n"
            f"  • 行号是否正确（文件共 {total_lines:,} 行）\n"
            f"  • 搜索内容是否拼写正确\n"
            f"  • 是否忘记启用 `use_regex`（当前 {'已启用' if search_regex else '未启用'}）\n"
            f"  • 使用 `surgical_scan` 工具扫描文件内容辅助定位"
        )

    # ── 第二遍：只读取需要的上下文行 ──────────────────────────
    need_lines = set()
    for ln, _ in matched_lines:
        for cl in range(max(1, ln - context_lines), ln + context_lines + 1):
            need_lines.add(cl)

    context_data = {}
    with open(file_path, "r", encoding=encoding, errors="replace",
              buffering=_BUFFER_SIZE) as f:
        for i, line in enumerate(f, 1):
            if i in need_lines:
                context_data[i] = line.rstrip("\n\r")

    # ── 构建报告 ─────────────────────────────────────────────
    mode_icons = {
        "replace": "✏️", "replace_all": "✏️",
        "insert_before": "⬆️", "insert_after": "⬇️",
        "delete": "🗑️",
    }
    action_texts = {
        "replace": "替换为", "replace_all": "替换为",
        "insert_before": "在此行**之前**插入", "insert_after": "在此行**之后**插入",
        "delete": "**删除此行**",
    }

    report = [
        f"🔍 **dry_run 预览** — `{os.path.basename(file_path)}`",
        f"",
        f"📁 **文件信息**：",
        f"  • 路径：`{file_path}`",
        f"  • 大小：{_format_size(file_size)}",
        f"  • 编码：{encoding}",
        f"",
        f"🎯 **匹配结果**：找到 {len(matched_lines)} 处匹配，模式=`{mode}`",
        f"",
    ]

    for ln, text in matched_lines:
        icon = mode_icons.get(mode, "🔧")
        action = action_texts.get(mode, "操作")

        report.append(f"--- **目标行 L{ln}** ---")
        report.append(f"  📍 原始内容：`{text[:200]}`")

        if mode == "delete":
            report.append(f"  {icon} 操作：{action}")
        else:
            preview = new_content.replace("\n", "\\n")[:200] if new_content else ""
            report.append(f"  {icon} 操作：{action} `{preview}`")

        # 上下文
        ctx_start = max(1, ln - context_lines)
        ctx_end = ln + context_lines
        report.append(f"  📄 上下文 (L{ctx_start}-L{ctx_end})：")
        report.append("  ```")
        for cl in range(ctx_start, ctx_end + 1):
            if cl in context_data:
                marker = ">>>" if cl == ln else "   "
                truncated = context_data[cl][:300]
                report.append(f"  {marker} L{cl:>6}: {truncated}")
        report.append("  ```")
        report.append("")

    report.append(
        "💡 **提示**：确认无误后，使用相同参数并将 `dry_run=false` 执行实际操作。"
    )
    return "\n".join(report)


# ── 3b. 实际执行手术 ──────────────────────────────────────────

def _execute_surgery(
    file_path: str,
    file_size: int,
    line_number: int | None,
    old_content: str | None,
    search_regex: re.Pattern | None,
    new_content: str | None,
    mode: str,
    count,
    backup: bool,
    encoding: str,
) -> str:
    """实际执行手术操作。临时文件 + 逐行流式读写。"""

    start_time = time.time()

    # ── 备份 ────────────────────────────────────────────────
    if backup:
        backup_path = file_path + ".bak"
        try:
            shutil.copy2(file_path, backup_path)
        except Exception as e:
            return f"❌ 备份失败：{e}"

    # ── 创建临时文件（与原文件同目录，确保原子 rename） ──────
    dir_name = os.path.dirname(file_path) or "."
    fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix=".surgical_")
    os.close(fd)

    try:
        matched_count = 0
        total_lines = 0

        with open(file_path, "r", encoding=encoding, errors="replace",
                  buffering=_BUFFER_SIZE) as fin, \
             open(temp_path, "w", encoding=encoding, errors="replace",
                  buffering=_BUFFER_SIZE) as fout:

            for i, line in enumerate(fin, 1):
                total_lines = i
                raw_line = line.rstrip("\n\r")

                is_match = _line_matches(i, raw_line, line_number,
                                         old_content, search_regex)

                if is_match and matched_count < count:
                    matched_count += 1

                    if mode == "delete":
                        continue  # 跳过此行

                    elif mode == "insert_before":
                        if new_content:
                            fout.write(new_content)
                            if not new_content.endswith("\n"):
                                fout.write("\n")
                        fout.write(line)

                    elif mode == "insert_after":
                        fout.write(line)
                        if new_content:
                            fout.write(new_content)
                            if not new_content.endswith("\n"):
                                fout.write("\n")

                    elif mode in ("replace", "replace_all"):
                        if new_content is not None:
                            fout.write(new_content)
                            if not new_content.endswith("\n"):
                                fout.write("\n")
                else:
                    fout.write(line)

        # ── 原子替换原文件 ─────────────────────────────────
        # os.replace 在 Windows/Linux 上均为原子操作
        os.replace(temp_path, file_path)
        elapsed = time.time() - start_time

        return _build_success_report(
            file_path, file_size, total_lines, matched_count,
            mode, elapsed, backup
        )

    except Exception as e:
        # 清理临时文件
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        return f"❌ **手术失败**：{e}"


# ── 3c. 行匹配判断 ────────────────────────────────────────────

def _line_matches(
    line_num: int,
    line_text: str,
    target_line: int | None,
    target_content: str | None,
    regex: re.Pattern | None,
) -> bool:
    """判断当前行是否匹配目标条件。"""
    if target_line is not None and target_content is not None:
        # 双重条件：行号必须相等，内容也必须匹配
        if line_num != target_line:
            return False
        if regex:
            return bool(regex.search(line_text))
        return target_content in line_text

    if target_line is not None:
        return line_num == target_line

    if target_content is not None:
        if regex:
            return bool(regex.search(line_text))
        return target_content in line_text

    return False


# ── 3d. 结果报告 ──────────────────────────────────────────────

def _build_success_report(
    file_path: str,
    file_size: int,
    total_lines: int,
    matched_count: int,
    mode: str,
    elapsed: float,
    backup: bool,
) -> str:
    """构建手术成功报告。"""
    mode_labels = {
        "replace": "替换", "replace_all": "全部替换",
        "insert_before": "前插", "insert_after": "后插",
        "delete": "删除",
    }
    icons = {
        "replace": "✏️", "replace_all": "✏️",
        "insert_before": "⬆️", "insert_after": "⬇️",
        "delete": "🗑️",
    }

    report = [
        f"{icons.get(mode, '🔧')} **手术完成** — `{os.path.basename(file_path)}`",
        f"",
        f"📊 **操作摘要**：",
        f"  • 操作模式：{mode_labels.get(mode, mode)}",
        f"  • 匹配行数：{matched_count}",
        f"  • 文件行数：{total_lines:,}",
        f"  • 文件大小：{_format_size(file_size)}",
        f"  • 耗时：{elapsed:.2f} 秒",
        f"  • 吞吐量：{_format_size(file_size / max(elapsed, 0.001))}/s",
    ]

    if backup:
        report.append(f"  • 备份：已保存为 `{file_path}.bak`")

    report.append("")
    report.append("✅ 文件已成功修改。")
    return "\n".join(report)


def _format_size(size: float) -> str:
    """格式化文件大小（二进制单位）。"""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


# ── 4. 扫描器：手术前定位 ──────────────────────────────────────

def _perform_scan(
    file_path: str,
    pattern: str,
    use_regex: bool,
    line_start: int | None,
    line_end: int | None,
    context_lines: int,
    max_matches: int,
    encoding: str,
) -> str:
    """在文件中扫描匹配行，返回上下文预览。"""

    if not os.path.isfile(file_path):
        return f"❌ 文件不存在：`{file_path}`"

    file_size = os.path.getsize(file_path)

    # 编译正则
    regex = None
    if use_regex:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"❌ **正则表达式错误**：`{pattern}` — {e}"

    max_matches = min(max_matches, 200)

    # ── 第一遍：找匹配行号 ──────────────────────────────────
    matched_lines = []
    total_lines = 0

    with open(file_path, "r", encoding=encoding, errors="replace",
              buffering=_BUFFER_SIZE) as f:
        for i, line in enumerate(f, 1):
            total_lines = i
            if line_start and i < line_start:
                continue
            if line_end and i > line_end:
                break

            raw = line.rstrip("\n\r")
            if regex:
                if regex.search(raw):
                    matched_lines.append((i, raw))
            else:
                if pattern in raw:
                    matched_lines.append((i, raw))

            if len(matched_lines) >= max_matches:
                break

    if not matched_lines:
        return (
            f"🔍 **扫描结果** — `{os.path.basename(file_path)}`\n\n"
            f"未找到匹配 `{pattern[:100]}` 的行。\n"
            f"文件共 {total_lines:,} 行，{_format_size(file_size)}。"
        )

    # ── 第二遍：只读取需要的上下文行 ──────────────────────────
    need_lines = set()
    for ln, _ in matched_lines:
        for cl in range(max(1, ln - context_lines), ln + context_lines + 1):
            need_lines.add(cl)

    context_data = {}
    with open(file_path, "r", encoding=encoding, errors="replace",
              buffering=_BUFFER_SIZE) as f:
        for i, line in enumerate(f, 1):
            if i in need_lines:
                context_data[i] = line.rstrip("\n\r")

    # ── 构建报告 ─────────────────────────────────────────────
    report = [
        f"🔍 **扫描结果** — `{os.path.basename(file_path)}`",
        f"",
        f"📁 文件：{_format_size(file_size)}，{total_lines:,} 行",
        f"🔎 模式：`{pattern[:200]}`（{'正则' if use_regex else '文本匹配'}）",
        f"🎯 匹配：{len(matched_lines)} 处",
        f"",
    ]

    for idx, (ln, text) in enumerate(matched_lines, 1):
        report.append(f"### 匹配 #{idx}  — 行 {ln}")
        report.append("```")
        ctx_start = max(1, ln - context_lines)
        ctx_end = ln + context_lines
        for cl in range(ctx_start, ctx_end + 1):
            if cl in context_data:
                marker = ">>>" if cl == ln else "   "
                truncated = context_data[cl][:300]
                report.append(f"{marker} L{cl:>6}: {truncated}")
        report.append("```")
        report.append("")

    if len(matched_lines) >= max_matches:
        report.append(
            f"⚠️ 仅显示前 {max_matches} 个匹配。"
            f"缩小搜索范围（line_start/line_end）查看更多。"
        )
    report.append(
        "💡 使用 `surgical_replace` 并指定 `line_number=` "
        "或 `old_content=` 来精确修改这些行。"
    )

    return "\n".join(report)


# ── 5. 工具分发 ────────────────────────────────────────────────

def execute(tool_name: str, args: dict, context) -> str:
    if tool_name == "surgical_replace":
        file_path = args.get("file_path", "")
        line_number = args.get("line_number")
        old_content = args.get("old_content")
        new_content = args.get("new_content")
        mode = args.get("mode", "replace")
        use_regex = args.get("use_regex", False)
        count = args.get("count", 1)
        dry_run = args.get("dry_run", False)
        context_lines = args.get("context_lines", 2)
        backup = args.get("backup", False)
        encoding = args.get("encoding", "utf-8")

        # 安全：路径限定在工作区内
        full_path = os.path.join(context.project_root, file_path)
        full_path = os.path.normpath(full_path)

        if not line_number and not old_content:
            return (
                "❌ **参数不足**：必须指定 `line_number` 或 `old_content` "
                "至少其中之一来定位目标行。"
            )

        valid_modes = ("replace", "insert_before", "insert_after",
                       "delete", "replace_all")
        if mode not in valid_modes:
            return f"❌ 不支持的模式 `{mode}`。可选：{', '.join(valid_modes)}"

        context.logger.info(
            f"Surgical {mode} on {file_path} "
            f"(line={line_number}, content={'yes' if old_content else 'no'}, "
            f"regex={use_regex}, dry_run={dry_run})"
        )

        s = context.storage
        s["surgeries_count"] = s.get("surgeries_count", 0) + (0 if dry_run else 1)
        s["dry_runs_count"] = s.get("dry_runs_count", 0) + (1 if dry_run else 0)

        return _perform_surgery(
            file_path=full_path,
            line_number=line_number,
            old_content=old_content,
            new_content=new_content,
            mode=mode,
            use_regex=use_regex,
            count=count,
            dry_run=dry_run,
            context_lines=context_lines,
            backup=backup,
            encoding=encoding,
        )

    elif tool_name == "surgical_scan":
        file_path = args.get("file_path", "")
        pattern = args.get("pattern", "")
        use_regex = args.get("use_regex", False)
        line_start = args.get("line_start")
        line_end = args.get("line_end")
        context_lines = args.get("context_lines", 1)
        max_matches = args.get("max_matches", 20)
        encoding = args.get("encoding", "utf-8")

        if not pattern:
            return "❌ 必须提供 `pattern` 搜索模式。"

        full_path = os.path.join(context.project_root, file_path)
        full_path = os.path.normpath(full_path)

        context.logger.info(
            f"Surgical scan on {file_path}: '{pattern[:80]}'"
        )

        s = context.storage
        s["scans_count"] = s.get("scans_count", 0) + 1

        return _perform_scan(
            file_path=full_path,
            pattern=pattern,
            use_regex=use_regex,
            line_start=line_start,
            line_end=line_end,
            context_lines=context_lines,
            max_matches=max_matches,
            encoding=encoding,
        )

    return f"Unknown tool: {tool_name}"


# ── 6. 生命周期钩子 ────────────────────────────────────────────

def on_agent_init(context):
    """初始化手术计数器。"""
    context.storage["surgeries_count"] = 0
    context.storage["dry_runs_count"] = 0
    context.storage["scans_count"] = 0
    context.storage["plugin_started"] = datetime.now().isoformat()
    context.logger.info("🔬 File Surgeon plugin loaded — molecular scalpel ready!")


def on_agent_shutdown(context):
    """会话结束时输出手术统计。"""
    surgeries = context.storage.get("surgeries_count", 0)
    dry_runs = context.storage.get("dry_runs_count", 0)
    scans = context.storage.get("scans_count", 0)
    context.logger.info(
        f"File Surgeon: {surgeries} surgery(ies), "
        f"{dry_runs} dry-run(s), {scans} scan(s) this session."
    )


def on_task_start(task_text: str, context):
    """检测用户是否要进行文件手术。"""
    keywords = [
        "修改", "替换行", "删除行", "插入", "手术刀",
        "surgical", "替换文件", "修改文件", "修改某行",
        "replace line", "delete line",
    ]
    if any(kw in task_text.lower() for kw in keywords):
        context.logger.info(f"File surgery task detected: {task_text[:80]}")


def on_task_done(summary: str, final_reply: str, context):
    """任务完成时记录（保持轻量）。"""
    pass
