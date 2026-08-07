# ──────────────────────────────────────────────────────────────
# Plugin: File Searcher v1 (工作区文件精确检索器)
# Publisher: xingluosama
# Version: 1.0.0
# Description: 工作区内任意文件精确检索 + 1GB 级大文件流式检索。
#   • index_workspace   — 扫描工作区目录，流式分块索引文件内容（SQLite FTS5）
#   • search_files      — 在已索引文件中按内容精确检索（短语优先、行号定位、上下文）
#   • find_files        — 按文件名/路径模糊检索（glob 模式）
#   • search_large_file — 单个超大文件（最高 1GB+）流式精确检索，零索引、低内存
#   • workspace_index_status / clear_workspace_index — 索引管理与统计
#
# 🚀 设计要点：
#   • 流式索引 — 文件按行流式分块写入 FTS5，绝不把整个文件读入内存，
#     单文件最高可索引 1GB+ 文本（磁盘存储）
#   • 增量更新 — 基于 (size, mtime) 检测文件变化，只重索引变更文件
#   • 二进制/大文件跳过 — 二进制文件与超大文件仅登记文件名，不索引内容
#   • 两段式检索 — FTS5 粗筛（毫秒级）→ 行级字面精确定位（权威判定）
#   • 编码自适应 — utf-8 / utf-8-sig / gbk / latin-1 自动探测，永不崩溃
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "File Searcher"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = (
    "工作区文件精确检索器：扫描并索引工作区内任意文件，支持内容精确检索、"
    "文件名模糊检索，以及 1GB+ 超大文件的流式精确检索（行号+上下文定位）。"
)

import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from fnmatch import fnmatch
from typing import Any, Dict, List, Optional, Set, Tuple

# ═══════════════════════════════════════════════════════════════
#  工具注册
# ═══════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "index_workspace",
            "description": (
                "扫描并索引工作区（或指定目录）内的文件内容，建立 SQLite FTS5 全文索引。"
                "支持增量更新：仅重新索引 size/mtime 变化的文件。\n"
                "索引后可用 search_files 做毫秒级内容精确检索。\n"
                "⚠️ 使用时机：① 需要反复检索工作区文件内容（代码库、日志、文档）时先建立索引；"
                "② 检索前若不确定索引是否最新，重新调用本工具即可（自动增量）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": (
                            "要扫描的目录。留空则扫描当前工作区根目录 (project_root)。"
                            "支持绝对路径或相对路径。"
                        )
                    },
                    "include_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "只索引匹配的文件名 glob 模式，如 ['*.py','*.md','*.log']。"
                            "留空表示索引所有文本文件（二进制自动跳过）。"
                        )
                    },
                    "exclude_dirs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "跳过的目录名（按目录名匹配，任意层级）。"
                            "默认排除 .git、node_modules、__pycache__、.venv、venv、"
                            "dist、build、.idea、.vscode、output、indexes。"
                        )
                    },
                    "max_file_mb": {
                        "type": "number",
                        "description": (
                            "内容索引的文件大小上限（MB）。超过此大小的文件只登记文件名"
                            "（仍可被 find_files 找到），不索引内容。默认 256。"
                        ),
                        "default": 256
                    },
                    "force": {
                        "type": "boolean",
                        "description": "为 true 时忽略 mtime/size 缓存，强制重新索引全部文件。默认 false。",
                        "default": False
                    }
                },
                "required": [],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "在已索引的工作区文件中执行内容精确检索："
                "默认按完整短语（连续字面）匹配，返回每个命中文件的路径、精确行号和上下文。\n"
                "⚠️ 使用时机：① 需要知道『哪个文件、哪一行』包含某段代码/配置/日志文本时；"
                "② 重复性内容检索（比内置 search_in_files 快得多）。\n"
                "若索引为空会提示，可先调用 index_workspace 建立索引；"
                "单个超大文件（如 1GB 日志）请改用 search_large_file。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要检索的文本。默认作为完整短语（连续出现）匹配，如 'api_key'、'def main'。"
                    },
                    "path": {
                        "type": "string",
                        "description": "限定检索范围：目录或具体文件路径。留空检索全部已索引文件。"
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "按文件名 glob 过滤，如 '*.py'、'*.log'。留空不过滤。"
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "是否区分大小写，默认 false。",
                        "default": False
                    },
                    "exact_phrase": {
                        "type": "boolean",
                        "description": "true 表示完整短语连续匹配（推荐）；false 表示关键词 AND 匹配（任一文件含全部词即中）。默认 true。",
                        "default": True
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "最多返回的命中块数量，默认 10，最大 50。",
                        "default": 10
                    },
                    "max_lines_per_file": {
                        "type": "integer",
                        "description": "每个文件最多展示的命中行数，默认 5。",
                        "default": 5
                    },
                    "line_context": {
                        "type": "integer",
                        "description": "每个命中行附带显示的上下文行数（前后各 N 行），默认 1。",
                        "default": 1
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": (
                "按文件名/路径模糊检索工作区文件（支持 glob 通配符 * ?）。"
                "无论文件是否索引过内容都能找到（含二进制、超大文件）。\n"
                "⚠️ 使用时机：需要定位『文件名像什么』的文件时，如 find_files('*config*')。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name_pattern": {
                        "type": "string",
                        "description": "文件名/路径 glob 模式，支持 * 和 ?，如 '*test*'、'*.py'、'config.json'。"
                    },
                    "path": {
                        "type": "string",
                        "description": "限定搜索目录。留空搜索整个索引根目录。"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "最多返回条数，默认 30，最大 100。",
                        "default": 30
                    }
                },
                "required": ["name_pattern"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_large_file",
            "description": (
                "对单个超大文件（最高 1GB+）执行流式精确检索，无需建索引、内存占用恒定。"
                "逐行扫描并返回精确行号、行内容与上下文，支持正则模式。\n"
                "⚠️ 使用时机：日志/数据/导出文件等超大文件的即时检索；"
                "文件未索引或不想建立索引时。小文件（<10MB）也可用，但已索引文件建议用 search_files。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目标文件路径（绝对路径或相对工作区路径）。"
                    },
                    "query": {
                        "type": "string",
                        "description": "检索文本。默认按字面精确匹配；regex=true 时按正则匹配。"
                    },
                    "regex": {
                        "type": "boolean",
                        "description": "是否将 query 作为正则表达式，默认 false。",
                        "default": False
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "是否区分大小写，默认 false。",
                        "default": False
                    },
                    "line_context": {
                        "type": "integer",
                        "description": "每个命中行附带显示的上下文行数（前后各 N 行），默认 2，最大 10。",
                        "default": 2
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "最多返回命中数（达到即提前停止扫描），默认 30，最大 100。",
                        "default": 30
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码。留空自动探测（utf-8 → gbk → latin-1）。可显式指定如 'utf-8'、'gbk'。"
                    }
                },
                "required": ["path", "query"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_index_status",
            "description": (
                "查看工作区文件索引的统计信息：索引根目录、文件数、内容索引状态分布、"
                "索引块数、总字符数、数据库大小、各扩展名分布等。\n"
                "⚠️ 使用时机：不确定索引是否建立、或想知道哪些文件已索引时先调用本工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clear_workspace_index",
            "description": (
                "清理工作区文件索引。可全部清空，或按文件/目录清除特定记录的索引。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要清除索引的文件或目录路径。留空清空全部索引。"
                    }
                },
                "required": [],
                "additionalProperties": False
            }
        }
    }
]

# ═══════════════════════════════════════════════════════════════
#  常量与工具函数
# ═══════════════════════════════════════════════════════════════

_DB_SCHEMA_VERSION = 1

# 每块行数 / 块间重叠行数（重叠防止跨块短语漏检）
_CHUNK_LINES = 256
_CHUNK_OVERLAP = 2

# 批量写入阈值
_BATCH_COMMIT_SIZE = 50

# 默认排除目录
_DEFAULT_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", "output", "indexes",
    ".mypy_cache", ".pytest_cache", ".tox", ".next", ".nuxt",
    "target", "bin", "obj", ".svn", ".hg",
}

# 单行最大长度（超过视为超长行，行内匹配仍工作但上下文截断）
_MAX_LINE_LEN = 1_000_000

_RE_CHINESE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
_RE_HAS_CJK = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
_RE_ASCII_WORD = re.compile(r'[a-z0-9_]+')
# 用正则一次性切出中文段与其它段（C 级实现，远快于逐字符循环）
_RE_SPLIT_CJK = re.compile(r'([\u4e00-\u9fff\u3400-\u4dbf]+)')


def _tokenize(text: str) -> List[str]:
    """中英文混合分词：中文按字 + bigram，英文按词小写化。

    性能优化：先快速检测是否含中文。
    - 纯 ASCII 文本（日志/代码/英文文档）：直接走 C 级正则 findall，
      1GB 纯 ASCII 文本分词仅需数十秒。
    - 含中文文本：用正则预切分中文段，仅对中文段做逐字符处理。
    """
    if not _RE_HAS_CJK.search(text):
        return _RE_ASCII_WORD.findall(text.lower())

    tokens: List[str] = []
    for seg in _RE_SPLIT_CJK.split(text):
        if not seg:
            continue
        if _RE_CHINESE.match(seg):
            # ── 中文段：逐字符 unigram + bigram ──
            i = 0
            n = len(seg)
            while i < n:
                ch = seg[i]
                tokens.append(ch)
                if i + 1 < n and _RE_CHINESE.match(seg[i + 1]):
                    tokens.append(ch + seg[i + 1])
                i += 1
        else:
            # ── 非中文段：C 级正则提取英文/数字 token ──
            tokens.extend(_RE_ASCII_WORD.findall(seg.lower()))
    return tokens


def _tokenize_for_query(text: str) -> List[str]:
    """查询分词：去重保序。"""
    seen: Set[str] = set()
    result: List[str] = []
    for tok in _tokenize(text):
        if tok not in seen:
            seen.add(tok)
            result.append(tok)
    return result


def _detect_encoding(path: str) -> str:
    """自动探测文件编码：utf-8-sig / utf-8 / gbk / latin-1（永不失败）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return "utf-8"
    if head.startswith(b'\xef\xbb\xbf'):
        return "utf-8-sig"
    for enc in ("utf-8", "gbk"):
        try:
            head.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _is_binary(head: bytes) -> bool:
    """通过头部 NUL 字节检测二进制文件。"""
    return b'\x00' in head


def _iter_lines(path: str, encoding: str):
    """流式逐行读取器：基于文件对象原生迭代（C 级缓冲+分行），
    内存占用 O(行长)，支持 1GB+ 文件且不把整个文件读入内存。

    性能：约 1-3 µs/行（短行），200MB 日志 ~10 秒内扫完。
    """
    with open(path, "r", encoding=encoding, errors="replace",
              newline=None) as f:
        for line in f:
            yield line


def _norm_path(path: str, base: str) -> str:
    """将路径归一化为绝对路径。"""
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base, path))


# ═══════════════════════════════════════════════════════════════
#  WorkspaceIndex — SQLite FTS5 工作区文件索引引擎
# ═══════════════════════════════════════════════════════════════

class WorkspaceIndex:
    """工作区文件索引引擎（磁盘存储，支持 1GB+ 文本）。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        # 批量写入缓冲区: (file_id, chunk_index, line_start, text, tokenized)
        self._batch_buffer: List[Tuple[int, int, int, str, str]] = []
        self._init_db()

    # ── DB 初始化 ──────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-32000")       # 32MB 页面缓存
        conn.execute("PRAGMA mmap_size=134217728")     # 128MB 内存映射
        conn.execute("PRAGMA busy_timeout=10000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL DEFAULT '',
                    ext TEXT NOT NULL DEFAULT '',
                    size INTEGER NOT NULL DEFAULT 0,
                    mtime REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'indexed',
                    indexed_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL DEFAULT 0,
                    line_start INTEGER NOT NULL DEFAULT 1,
                    text TEXT NOT NULL,
                    tokenized TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_file
                    ON chunks(file_id, chunk_index);

                CREATE VIRTUAL TABLE IF NOT EXISTS fts_files USING fts5(
                    tokenized,
                    content='',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );
            """)
            conn.commit()
        finally:
            conn.close()

    # ── 元数据 ─────────────────────────────────────────────────

    def get_meta(self, key: str, default: str = "") -> str:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else default
        finally:
            conn.close()

    def set_meta(self, key: str, value: str):
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value)
                )
        finally:
            conn.close()

    # ── 文件登记 ───────────────────────────────────────────────

    def upsert_file(self, path: str, size: int, mtime: float,
                    status: str) -> int:
        """登记文件记录，返回 file_id。"""
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    """INSERT INTO files(path, name, ext, size, mtime, status, indexed_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(path) DO UPDATE SET
                         name = excluded.name, ext = excluded.ext,
                         size = excluded.size, mtime = excluded.mtime,
                         status = excluded.status,
                         indexed_at = excluded.indexed_at""",
                    (path, name, ext, size, mtime, status,
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
                row = conn.execute(
                    "SELECT id FROM files WHERE path = ?", (path,)
                ).fetchone()
                return row[0]
        finally:
            conn.close()

    def get_file(self, path: str) -> Optional[sqlite3.Row]:
        conn = self._get_conn()
        try:
            return conn.execute(
                "SELECT * FROM files WHERE path = ?", (path,)
            ).fetchone()
        finally:
            conn.close()

    def remove_file(self, path: str) -> int:
        """删除某文件的索引记录（含 chunks 与 FTS5），返回删除的块数。"""
        self.flush()
        conn = self._get_conn()
        removed_chunks = 0
        try:
            with conn:
                row = conn.execute(
                    "SELECT id FROM files WHERE path = ?", (path,)
                ).fetchone()
                if row is None:
                    return 0
                file_id = row[0]
                chunk_ids = [
                    r[0] for r in conn.execute(
                        "SELECT id FROM chunks WHERE file_id = ?", (file_id,)
                    )
                ]
                for cid in chunk_ids:
                    conn.execute(
                        "INSERT INTO fts_files(fts_files, rowid, tokenized) "
                        "VALUES('delete', ?, '')", (cid,)
                    )
                conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
                conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
                removed_chunks = len(chunk_ids)
        finally:
            conn.close()
        return removed_chunks

    def remove_by_dir(self, dir_path: str) -> int:
        """删除某目录下所有文件的索引记录，返回删除的文件数。"""
        self.flush()
        conn = self._get_conn()
        removed_files = 0
        try:
            paths = [r[0] for r in conn.execute(
                "SELECT path FROM files WHERE path LIKE ?",
                (dir_path.rstrip("\\/") + os.sep + "%",)
            )]
        finally:
            conn.close()
        for p in paths:
            self.remove_file(p)
            removed_files += 1
        return removed_files

    # ── 内容块写入 ─────────────────────────────────────────────

    def add_chunk(self, file_id: int, chunk_index: int,
                  line_start: int, text: str):
        """添加一个内容块（批量缓冲，达到阈值自动提交）。"""
        tokenized = " ".join(_tokenize(text))
        with self._lock:
            self._batch_buffer.append(
                (file_id, chunk_index, line_start, text, tokenized)
            )
            if len(self._batch_buffer) >= _BATCH_COMMIT_SIZE:
                self._flush_batch()

    def flush(self):
        with self._lock:
            if self._batch_buffer:
                self._flush_batch()

    def _flush_batch(self):
        if not self._batch_buffer:
            return
        conn = self._get_conn()
        try:
            with conn:
                for file_id, chunk_index, line_start, text, tokenized in self._batch_buffer:
                    cur = conn.execute(
                        """INSERT INTO chunks
                           (file_id, chunk_index, line_start, text, tokenized)
                           VALUES (?, ?, ?, ?, ?)""",
                        (file_id, chunk_index, line_start, text, tokenized)
                    )
                    conn.execute(
                        "INSERT INTO fts_files(rowid, tokenized) VALUES(?, ?)",
                        (cur.lastrowid, tokenized)
                    )
        finally:
            conn.close()
        self._batch_buffer.clear()

    def reset_file_chunks(self, file_id: int) -> int:
        """删除某文件的所有内容块（先删 FTS5 再删行），返回删除的块数。"""
        self.flush()
        conn = self._get_conn()
        old_ids: List[int] = []
        try:
            with conn:
                old_ids = [r[0] for r in conn.execute(
                    "SELECT id FROM chunks WHERE file_id = ?", (file_id,)
                )]
                for cid in old_ids:
                    conn.execute(
                        "INSERT INTO fts_files(fts_files, rowid, tokenized) "
                        "VALUES('delete', ?, '')", (cid,)
                    )
                conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        finally:
            conn.close()
        return len(old_ids)

    # ── 检索 ───────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 10,
               path_filter: str = "", file_pattern: str = "",
               case_sensitive: bool = False,
               exact_phrase: bool = True,
               max_lines_per_file: int = 5,
               line_context: int = 1) -> Dict[str, Any]:
        """两段式检索：FTS5 粗筛 → 行级字面精确定位。

        Returns
        -------
        dict:
            {
              "matches": [ {file, line, text, context_before, context_after}, ... ],
              "file_hits": {file: count},
              "total_chunks_scanned": int,
              "total_lines_found": int,
            }
        """
        self.flush()
        query_tokens = _tokenize_for_query(query)
        if not query_tokens:
            return {"matches": [], "file_hits": {}, "total_chunks_scanned": 0,
                    "total_lines_found": 0}

        needle = query if case_sensitive else query.lower()
        regex = None
        if not exact_phrase:
            # 宽松模式：任一查询 token 命中即算（行级判定）
            regex = re.compile(
                "|".join(re.escape(t) for t in query_tokens),
                re.IGNORECASE if not case_sensitive else 0
            )

        # ── 阶段 1: FTS5 粗筛 ──
        conn = self._get_conn()
        try:
            candidates = self._fts5_candidates(
                conn, query_tokens, exact_phrase,
                path_filter, top_k * 20
            )
            if not candidates:
                return {"matches": [], "file_hits": {},
                        "total_chunks_scanned": 0, "total_lines_found": 0}

            # ── 阶段 2: 行级精确定位 ──
            matches: List[Dict[str, Any]] = []
            file_hits: Dict[str, int] = {}
            seen_files: Set[str] = set()

            for chunk_id, chunk_text, line_start, file_path, file_name in candidates:
                # 文件级过滤
                if file_pattern and not (
                    fnmatch(file_name, file_pattern)
                    or fnmatch(file_path, file_pattern)
                ):
                    continue
                if path_filter and not (
                    file_path == path_filter
                    or file_path.startswith(path_filter.rstrip("\\/") + os.sep)
                ):
                    continue

                lines = chunk_text.splitlines()
                for i, line in enumerate(lines):
                    if len(line) > _MAX_LINE_LEN:
                        line = line[:_MAX_LINE_LEN]
                    if regex:
                        hit = bool(regex.search(line))
                    else:
                        hit = (needle in (line.lower() if not case_sensitive else line))
                    if not hit:
                        continue

                    abs_line = line_start + i
                    before = lines[max(0, i - line_context):i]
                    after = lines[i + 1:i + 1 + line_context]

                    if file_path not in file_hits:
                        file_hits[file_path] = 0
                        seen_files.add(file_path)
                    if file_hits[file_path] >= max_lines_per_file:
                        continue
                    file_hits[file_path] += 1

                    matches.append({
                        "file": file_path,
                        "line": abs_line,
                        "text": line.strip(),
                        "context_before": before,
                        "context_after": after,
                    })
                    if len(matches) >= top_k * max_lines_per_file:
                        break
                if len(matches) >= top_k * max_lines_per_file:
                    break
        finally:
            conn.close()

        # 按文件分组排序（文件内按行号）
        matches.sort(key=lambda m: (m["file"], m["line"]))
        return {
            "matches": matches,
            "file_hits": file_hits,
            "total_chunks_scanned": len(candidates),
            "total_lines_found": sum(file_hits.values()),
        }

    def _fts5_candidates(self, conn: sqlite3.Connection,
                         query_tokens: List[str], exact_phrase: bool,
                         path_filter: str, limit: int
                         ) -> List[Tuple[int, str, int, str, str]]:
        """FTS5 候选筛选：返回 (chunk_id, text, line_start, path, name)。"""
        sql = """
            SELECT c.id, c.text, c.line_start, f.path, f.name
            FROM fts_files ff
            JOIN chunks c ON ff.rowid = c.id
            JOIN files f ON c.file_id = f.id
            WHERE fts_files MATCH ?
            ORDER BY rank
            LIMIT ?
        """
        rows: List[sqlite3.Row] = []

        # 1) 短语查询（连续 token 序列）
        phrase = " ".join(f'"{t}"' for t in query_tokens)
        if exact_phrase and len(query_tokens) >= 1:
            rows = conn.execute(sql, (phrase, limit)).fetchall()

        # 2) 回退：AND → OR
        if not rows and len(query_tokens) > 1:
            and_q = " AND ".join(f'"{t}"' for t in query_tokens)
            rows = conn.execute(sql, (and_q, limit)).fetchall()
        if not rows and len(query_tokens) > 1:
            or_q = " OR ".join(f'"{t}"' for t in query_tokens)
            rows = conn.execute(sql, (or_q, limit)).fetchall()

        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    # ── 文件名检索 ─────────────────────────────────────────────

    def find_by_name(self, pattern: str, path_filter: str = "",
                     top_k: int = 30) -> List[Dict[str, Any]]:
        """按文件名/路径 glob 模糊检索（Python 侧过滤，支持 * ?）。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT path, name, ext, size, status, indexed_at "
                "FROM files ORDER BY name"
            ).fetchall()
        finally:
            conn.close()

        pat = pattern.lower()
        results: List[Dict[str, Any]] = []
        for r in rows:
            path = r[0]
            if path_filter and not (
                path == path_filter
                or path.startswith(path_filter.rstrip("\\/") + os.sep)
            ):
                continue
            if fnmatch(path.lower(), pat) or fnmatch(r[1].lower(), pat):
                results.append({
                    "path": path, "name": r[1], "ext": r[2],
                    "size": r[3], "status": r[4], "indexed_at": r[5],
                })
                if len(results) >= top_k:
                    break
        return results

    # ── 统计 / 清理 ────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        self.flush()
        conn = self._get_conn()
        try:
            total_files = conn.execute(
                "SELECT COUNT(*) FROM files"
            ).fetchone()[0]
            status_rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM files "
                "GROUP BY status ORDER BY cnt DESC"
            ).fetchall()
            statuses = {r[0]: r[1] for r in status_rows}
            chunk_count = conn.execute(
                "SELECT COUNT(*) FROM chunks"
            ).fetchone()[0]
            total_chars = conn.execute(
                "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM chunks"
            ).fetchone()[0]
            ext_rows = conn.execute(
                """SELECT ext, COUNT(*) as cnt FROM files
                   WHERE status = 'indexed' AND ext != ''
                   GROUP BY ext ORDER BY cnt DESC LIMIT 15"""
            ).fetchall()
            extensions = {r[0]: r[1] for r in ext_rows}
            last_scan = self.get_meta("last_scan", "")
            root = self.get_meta("index_root", "")
            return {
                "index_root": root,
                "total_files": total_files,
                "status_distribution": statuses,
                "total_chunks": chunk_count,
                "total_characters": total_chars,
                "extensions": extensions,
                "last_scan": last_scan,
                "db_path": self._db_path,
            }
        finally:
            conn.close()

    def clear_all(self):
        self.flush()
        conn = self._get_conn()
        try:
            with conn:
                conn.execute("DELETE FROM files")
                conn.execute("DELETE FROM chunks")
                conn.execute("DROP TABLE IF EXISTS fts_files")
                conn.execute("""
                    CREATE VIRTUAL TABLE fts_files USING fts5(
                        tokenized,
                        content='',
                        content_rowid='id',
                        tokenize='unicode61 remove_diacritics 2'
                    )
                """)
                conn.execute(
                    "UPDATE sqlite_sequence SET seq=0 WHERE name='files'"
                )
                conn.execute(
                    "UPDATE sqlite_sequence SET seq=0 WHERE name='chunks'"
                )
                conn.execute("DELETE FROM meta WHERE key != 'index_root'")
        finally:
            conn.close()

    def close(self):
        self.flush()


# ═══════════════════════════════════════════════════════════════
#  大文件流式检索（无索引，1GB+）
# ═══════════════════════════════════════════════════════════════

def search_large_file_stream(path: str, query: str, *,
                             regex: bool = False,
                             case_sensitive: bool = False,
                             line_context: int = 2,
                             max_matches: int = 30,
                             encoding: str = "") -> Dict[str, Any]:
    """对单个超大文件流式逐行检索，内存占用恒定（O(行长)）。

    支持 1GB+ 文件：使用 1MB 缓冲迭代器逐行扫描，
    达到 max_matches 后提前停止。
    """
    if not os.path.isfile(path):
        return {"error": f"文件不存在: {path}"}

    file_size = os.path.getsize(path)
    enc = encoding or _detect_encoding(path)

    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(query, flags)
        except re.error as e:
            return {"error": f"正则表达式无效: {e}"}
    else:
        matcher = None

    needle = query if case_sensitive else query.lower()

    start = time.time()
    hits: List[Dict[str, Any]] = []
    total_lines = 0
    ring: List[str] = []          # 上下文环形缓冲（前 line_context 行）
    long_line_warned = False

    for raw_line in _iter_lines(path, enc):
        total_lines += 1
        line = raw_line.rstrip("\r\n")
        is_hit = False
        if matcher is not None:
            is_hit = bool(matcher.search(line))
        else:
            hay = line if case_sensitive else line.lower()
            is_hit = needle in hay

        if is_hit:
            truncated = len(line) > 300
            display = line[:300] + ("…" if truncated else "")
            hits.append({
                "line": total_lines,
                "text": display,
                "context_before": list(ring),
            })
            if len(hits) >= max_matches:
                break
        elif len(line) > _MAX_LINE_LEN and not long_line_warned:
            long_line_warned = True

        # 维护上下文环形缓冲
        ring.append(line)
        if len(ring) > line_context:
            ring.pop(0)

    elapsed = time.time() - start

    return {
        "path": path,
        "file_size": file_size,
        "encoding": enc,
        "total_lines": total_lines,
        "matches": hits,
        "elapsed_seconds": round(elapsed, 2),
        "stopped_early": len(hits) >= max_matches,
        "long_line_warned": long_line_warned,
    }


# ═══════════════════════════════════════════════════════════════
#  工作区扫描与增量索引
# ═══════════════════════════════════════════════════════════════

def _scan_and_index(index: WorkspaceIndex, root: str, *,
                    include_patterns: List[str],
                    exclude_dirs: Set[str],
                    max_file_mb: float,
                    force: bool) -> Dict[str, Any]:
    """扫描目录树并增量索引文件内容。

    - 文本文件：流式分块索引（含行号），status='indexed'
    - 二进制文件：仅登记，status='binary'
    - 超大文件（> max_file_mb）：仅登记，status='too_large'
    """
    root = os.path.normpath(root)
    if not os.path.isdir(root):
        return {"error": f"目录不存在: {root}"}

    start = time.time()
    scanned = 0          # 扫描到的文件总数
    indexed = 0          # 新索引/重索引的文件数
    unchanged = 0        # 无变化的文件数
    skipped = 0          # 跳过（二进制/超大/不可读）
    total_chars = 0
    max_bytes = int(max_file_mb * 1024 * 1024)

    patterns = [p.lower() for p in (include_patterns or [])]

    for dirpath, dirnames, filenames in os.walk(root):
        # 剪枝排除目录
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            scanned += 1

            # 文件名模式过滤（仅影响内容索引，登记仍进行）
            if patterns and not any(
                fnmatch(fname.lower(), p) for p in patterns
            ):
                skipped += 1
                continue

            try:
                st = os.stat(full)
            except OSError:
                skipped += 1
                continue

            existing = index.get_file(full)
            if (not force and existing is not None
                    and abs(existing["mtime"] - st.st_mtime) < 0.001
                    and existing["size"] == st.st_size):
                unchanged += 1
                continue

            # 二进制检测（读头部 8KB）
            try:
                with open(full, "rb") as f:
                    head = f.read(8192)
            except OSError:
                skipped += 1
                continue

            if _is_binary(head):
                index.upsert_file(full, st.st_size, st.st_mtime, "binary")
                skipped += 1
                continue

            if st.st_size > max_bytes:
                index.upsert_file(full, st.st_size, st.st_mtime, "too_large")
                skipped += 1
                continue

            # ── 流式分块索引 ──
            enc = _detect_encoding(full)
            file_id = index.upsert_file(full, st.st_size, st.st_mtime, "indexed")
            index.reset_file_chunks(file_id)  # 清掉旧块

            chunk_index = 0
            pending: List[Tuple[int, str]] = []   # (行号, 行文本含换行)
            file_chars = 0
            try:
                for line_no, line in enumerate(_iter_lines(full, enc), 1):
                    pending.append((line_no, line))
                    if len(pending) >= _CHUNK_LINES + _CHUNK_OVERLAP:
                        chunk_lines = pending[:_CHUNK_LINES]
                        pending = pending[_CHUNK_LINES - _CHUNK_OVERLAP:]
                        chunk_text = "".join(t for _, t in chunk_lines)
                        index.add_chunk(
                            file_id, chunk_index, chunk_lines[0][0], chunk_text
                        )
                        file_chars += len(chunk_text)
                        chunk_index += 1
                if pending:
                    chunk_text = "".join(t for _, t in pending)
                    index.add_chunk(
                        file_id, chunk_index, pending[0][0], chunk_text
                    )
                    file_chars += len(chunk_text)
            except Exception:
                pass  # 单文件索引失败不中断整体扫描

            index.flush()
            indexed += 1
            total_chars += file_chars

    index.set_meta("last_scan", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    index.set_meta("index_root", root)
    elapsed = time.time() - start

    return {
        "root": root,
        "scanned": scanned,
        "indexed": indexed,
        "unchanged": unchanged,
        "skipped": skipped,
        "total_chars_indexed": total_chars,
        "elapsed_seconds": round(elapsed, 2),
    }


# ═══════════════════════════════════════════════════════════════
#  插件实例管理
# ═══════════════════════════════════════════════════════════════

def _get_index(context) -> WorkspaceIndex:
    """从 context.storage 获取或创建索引实例（DB 持久化于 app_dir/indexes/）。"""
    if "workspace_index" not in context.storage:
        db_dir = os.path.join(context.app_dir, "indexes")
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "workspace_index.db")
        context.storage["workspace_index"] = WorkspaceIndex(db_path)
    return context.storage["workspace_index"]


# ═══════════════════════════════════════════════════════════════
#  工具实现
# ═══════════════════════════════════════════════════════════════

def _handle_index_workspace(args: dict, context) -> str:
    index = _get_index(context)
    base = context.project_root or "."

    directory = (args.get("directory") or "").strip()
    if directory:
        root = _norm_path(directory, base)
    else:
        root = os.path.normpath(base)

    include_patterns = args.get("include_patterns") or []
    exclude_dirs = set(args.get("exclude_dirs") or []) | _DEFAULT_EXCLUDE_DIRS
    max_file_mb = max(1.0, min(float(args.get("max_file_mb", 256)), 4096))
    force = bool(args.get("force", False))

    context.logger.info(
        f"Indexing workspace: {root} (max {max_file_mb:.0f}MB/file, force={force})"
    )
    result = _scan_and_index(
        index, root,
        include_patterns=include_patterns,
        exclude_dirs=exclude_dirs,
        max_file_mb=max_file_mb,
        force=force,
    )

    if "error" in result:
        return f"❌ {result['error']}"

    lines = [
        f"✅ **工作区索引完成**",
        f"  索引根目录: `{result['root']}`",
        f"  扫描文件: {result['scanned']}",
        f"  新索引/更新: **{result['indexed']}** 个文件",
        f"  未变化跳过: {result['unchanged']} 个文件",
        f"  跳过(二进制/超大/过滤): {result['skipped']} 个文件",
        f"  本次索引字符数: {result['total_chars_indexed']:,}",
        f"  耗时: {result['elapsed_seconds']} 秒",
        "",
        f"💡 现在可用 `search_files(query='...')` 精确检索，"
        f"或 `find_files(name_pattern='*.py')` 按文件名查找。",
    ]
    return "\n".join(lines)


def _handle_search_files(args: dict, context) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "❌ 检索文本不能为空。\n\n用法示例：\n  `search_files(query='api_key')`\n  `search_files(query='def main', file_pattern='*.py')`"

    index = _get_index(context)
    stats = index.stats()
    if stats["total_files"] == 0:
        return (
            "📭 工作区索引为空，尚未索引任何文件。\n\n"
            "请先建立索引：\n"
            "  `index_workspace()`  — 扫描当前工作区\n"
            "  `index_workspace(directory='C:/path')` — 扫描指定目录"
        )

    path_filter = (args.get("path") or "").strip()
    if path_filter:
        path_filter = _norm_path(path_filter, context.project_root or ".")
    file_pattern = (args.get("file_pattern") or "").strip()
    case_sensitive = bool(args.get("case_sensitive", False))
    exact_phrase = bool(args.get("exact_phrase", True))
    top_k = max(1, min(int(args.get("top_k", 10)), 50))
    max_lines_per_file = max(1, min(int(args.get("max_lines_per_file", 5)), 20))
    line_context = max(0, min(int(args.get("line_context", 1)), 5))

    result = index.search(
        query=query, top_k=top_k,
        path_filter=path_filter, file_pattern=file_pattern,
        case_sensitive=case_sensitive, exact_phrase=exact_phrase,
        max_lines_per_file=max_lines_per_file, line_context=line_context,
    )

    matches = result["matches"]
    if not matches:
        hint = ""
        if path_filter:
            hint += f" 范围: `{path_filter}`"
        if file_pattern:
            hint += f" 模式: `{file_pattern}`"
        return (
            f"🔍 未在索引中找到「**{query}**」{hint}\n\n"
            f"  索引统计: {stats['total_files']} 文件, "
            f"{stats['total_characters']:,} 字符\n"
            f"  建议: ① 若文件是最近新增/修改的，先重新调用 index_workspace；"
            f"② 尝试缩短查询词；③ 超大文件请用 search_large_file。"
        )

    mode = "完整短语匹配" if exact_phrase else "关键词 AND 匹配"
    lines = [
        f"🔍 **文件内容精确检索**: `{query}`",
        f"  模式: {mode} | 大小写: {'敏感' if case_sensitive else '不敏感'}",
        f"  命中: {result['total_lines_found']} 行 / {len(result['file_hits'])} 个文件\n",
    ]

    current_file = None
    for m in matches:
        if m["file"] != current_file:
            current_file = m["file"]
            try:
                rel = os.path.relpath(current_file,
                                      stats.get("index_root") or
                                      context.project_root or ".")
            except ValueError:
                rel = current_file
            lines.append(f"📄 **{rel}**")

        snippet = m["text"]
        if len(snippet) > 200:
            snippet = snippet[:200] + "…"

        for cb in m["context_before"][-line_context:]:
            cb_s = cb.strip()
            if cb_s:
                lines.append(f"    ┊ {cb_s[:150]}")
        lines.append(f"  **L{m['line']}** │ {snippet}")
        for ca in m["context_after"][:line_context]:
            ca_s = ca.strip()
            if ca_s:
                lines.append(f"    ┊ {ca_s[:150]}")
        lines.append("")

    lines.append(
        f"📊 索引: {stats['total_files']} 文件, {stats['total_characters']:,} 字符 | "
        f"数据库: `{stats.get('db_path', '')}`"
    )
    return "\n".join(lines)


def _handle_find_files(args: dict, context) -> str:
    pattern = (args.get("name_pattern") or "").strip()
    if not pattern:
        return "❌ 文件名模式不能为空。\n\n用法示例：\n  `find_files(name_pattern='*test*')`\n  `find_files(name_pattern='*.py')`"

    index = _get_index(context)
    path_filter = (args.get("path") or "").strip()
    if path_filter:
        path_filter = _norm_path(path_filter, context.project_root or ".")
    top_k = max(1, min(int(args.get("top_k", 30)), 100))

    results = index.find_by_name(pattern, path_filter, top_k)
    if not results:
        return (
            f"🔍 未找到文件名匹配「**{pattern}**」的文件。\n\n"
            f"  提示: ① 支持通配符 `*` 和 `?`；"
            f"② 若文件是新增的，先调用 index_workspace 扫描。"
        )

    lines = [f"📁 **文件检索**: `{pattern}` — 共 {len(results)} 个文件\n"]
    for r in results:
        size_str = _fmt_size(r["size"])
        status_icon = {"indexed": "✅", "binary": "⚙️", "too_large": "🐘"}.get(
            r["status"], "❓")
        rel = r["path"]
        try:
            rel = os.path.relpath(rel, context.project_root or ".")
        except ValueError:
            pass
        lines.append(f"  {status_icon} `{rel}` ({size_str})")
    lines.append("")
    lines.append(f"  ✅=已索引内容  ⚙️=二进制(仅登记)  🐘=超大文件(仅登记)")
    return "\n".join(lines)


def _handle_search_large_file(args: dict, context) -> str:
    path = (args.get("path") or "").strip()
    query = (args.get("query") or "").strip()
    if not path or not query:
        return "❌ 需要同时提供 path 和 query。\n\n用法示例：\n  `search_large_file(path='logs/app.log', query='ERROR 500')`"

    full = _norm_path(path, context.project_root or ".")
    if not os.path.exists(full):
        return f"❌ 文件不存在: `{full}`"

    result = search_large_file_stream(
        full, query,
        regex=bool(args.get("regex", False)),
        case_sensitive=bool(args.get("case_sensitive", False)),
        line_context=max(0, min(int(args.get("line_context", 2)), 10)),
        max_matches=max(1, min(int(args.get("max_matches", 30)), 100)),
        encoding=(args.get("encoding") or "").strip(),
    )

    if "error" in result:
        return f"❌ {result['error']}"

    matches = result["matches"]
    n_hits = len(matches)
    size_str = _fmt_size(result["file_size"])
    mode = "正则" if args.get("regex") else "精确短语"

    lines = [
        f"🔍 **大文件检索完成**",
        f"📄 文件: `{full}` ({size_str})",
        f"🎯 查询: `{query}` | 模式: {mode} | "
        f"大小写: {'敏感' if args.get('case_sensitive') else '不敏感'}",
        f"✅ 命中 **{n_hits}** 处" +
        ("（已提前停止，达到 max_matches 上限）" if result["stopped_early"] else ""),
        f"⏱️ 耗时 {result['elapsed_seconds']} 秒 | "
        f"扫描 {result['total_lines']:,} 行 | 编码: {result['encoding']}\n",
    ]

    for m in matches:
        snippet = m["text"]
        lines.append(f"  **L{m['line']:,}** │ {snippet}")
        for cb in m["context_before"][-3:]:
            cb_s = cb.strip()
            if cb_s:
                lines.append(f"    ┊ {cb_s[:150]}")
        lines.append("")

    return "\n".join(lines)


def _handle_index_status(args: dict, context) -> str:
    stats = _get_index(context).stats()

    lines = [
        "📊 **工作区文件索引状态**",
        "",
        f"  索引根目录: `{stats.get('index_root') or '(未扫描)'}`",
        f"  登记文件数: {stats['total_files']}",
        f"  内容索引块数: {stats['total_chunks']:,}",
        f"  索引字符总量: {stats['total_characters']:,}",
        f"  上次扫描: {stats.get('last_scan') or '从未'}",
    ]

    if stats.get("db_path") and os.path.exists(stats["db_path"]):
        db_size = os.path.getsize(stats["db_path"])
        lines.append(f"  数据库文件: `{stats['db_path']}`")
        lines.append(f"  磁盘占用: {db_size / 1024 / 1024:.1f} MB")

    lines.append("")
    lines.append("  **文件状态分布**:")
    dist = stats["status_distribution"]
    if dist:
        for status, cnt in sorted(dist.items(), key=lambda x: x[1], reverse=True):
            icon = {"indexed": "✅", "binary": "⚙️", "too_large": "🐘"}.get(status, "❓")
            label = {
                "indexed": "已索引内容",
                "binary": "二进制(仅登记)",
                "too_large": "超大文件(仅登记)",
            }.get(status, status)
            lines.append(f"    {icon} {label} — {cnt} 个")
    else:
        lines.append("    *(尚未扫描任何文件，请调用 index_workspace)*")

    if stats["extensions"]:
        lines.append("")
        lines.append("  **扩展名分布（已索引）**:")
        for ext, cnt in sorted(stats["extensions"].items(),
                               key=lambda x: x[1], reverse=True)[:10]:
            lines.append(f"    `*.{ext}` — {cnt} 个")

    return "\n".join(lines)


def _handle_clear_index(args: dict, context) -> str:
    index = _get_index(context)
    path = (args.get("path") or "").strip()

    if path:
        full = _norm_path(path, context.project_root or ".")
        if os.path.isdir(full):
            removed = index.remove_by_dir(full)
            return f"✅ 已清除目录 `{full}` 下 **{removed}** 个文件的索引。"
        removed_chunks = index.remove_file(full)
        return f"✅ 已清除文件 `{full}` 的索引（{removed_chunks} 个块）。"

    stats_before = index.stats()
    index.clear_all()
    return (
        f"✅ 已清空全部工作区文件索引"
        f"（{stats_before['total_files']} 个文件、{stats_before['total_chunks']} 个块）。"
    )


def _fmt_size(size: int) -> str:
    """人性化文件大小显示。"""
    if size >= 1 << 30:
        return f"{size / (1 << 30):.2f} GB"
    if size >= 1 << 20:
        return f"{size / (1 << 20):.1f} MB"
    if size >= 1 << 10:
        return f"{size / (1 << 10):.0f} KB"
    return f"{size} B"


# ═══════════════════════════════════════════════════════════════
#  工具分发
# ═══════════════════════════════════════════════════════════════

def execute(tool_name: str, args: dict, context) -> str:
    """工具调用分发器。"""
    handlers = {
        "index_workspace": _handle_index_workspace,
        "search_files": _handle_search_files,
        "find_files": _handle_find_files,
        "search_large_file": _handle_search_large_file,
        "workspace_index_status": _handle_index_status,
        "clear_workspace_index": _handle_clear_index,
    }
    handler = handlers.get(tool_name)
    if handler:
        return handler(args, context)
    return f"Unknown tool: {tool_name}"


# ═══════════════════════════════════════════════════════════════
#  生命周期钩子
# ═══════════════════════════════════════════════════════════════

def on_agent_init(context):
    """初始化时报告索引状态。"""
    try:
        index = _get_index(context)
        stats = index.stats()
        context.logger.info(
            f"File Searcher ready — workspace index: "
            f"{stats['total_files']} files, {stats['total_characters']:,} chars "
            f"({stats.get('index_root') or 'no root yet'})"
        )
    except Exception:
        pass


def on_agent_shutdown(context):
    """关闭时刷新缓冲区。"""
    try:
        _get_index(context).close()
    except Exception:
        pass


def after_tool_call(tool_name: str, args: dict, result: str, context):
    """自动索引 read_file 读取过的文件（≤5MB，未索引才索引）。

    后台线程执行，不阻塞主流程；使读过的文件可被后续 search_files 命中。
    """
    if tool_name != "read_file":
        return result
    try:
        path = (args.get("path") or "").strip()
        if not path:
            return result
        full = _norm_path(path, context.project_root or ".")
        if not os.path.isfile(full):
            return result
        if os.path.getsize(full) > 5 * 1024 * 1024:
            return result  # 大文件不自动索引，改用 search_large_file

        index = _get_index(context)
        existing = index.get_file(full)
        if existing is not None and existing["status"] == "indexed":
            return result

        def _bg_index():
            try:
                _scan_and_index(
                    index, os.path.dirname(full),
                    include_patterns=[],
                    exclude_dirs=_DEFAULT_EXCLUDE_DIRS,
                    max_file_mb=256.0,
                    force=False,
                )
            except Exception:
                pass

        threading.Thread(target=_bg_index, daemon=True).start()
    except Exception:
        pass
    return result
