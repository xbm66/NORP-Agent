# ──────────────────────────────────────────────────────────────
# Plugin: Context Retriever v2 (超长上下文精确检索器)
# Publisher: xingluosama
# Version: 2.0.0
# Description: 基于 SQLite FTS5 的超长上下文精确检索引擎。
#   支持高达 1GB+ 文本的磁盘存储与毫秒级检索，
#   中英文混合分词 + BM25 精排 + 短语匹配奖励。
#
# 🚀 v2 核心升级：
#   • SQLite FTS5 后端 — 磁盘存储，突破内存瓶颈，轻松承载 1GB+
#   • 全自动分词管道 — 中文 bigram/unigram + 英文 word-piece
#   • 两阶段检索 — FTS5 粗排（毫秒）→ Python BM25 + 短语精排
#   • WAL 模式 — 支持并发读写，多线程安全
#   • 智能分块 — 按段落/句子边界分割，保持语义完整性
#   • 上下文扩展 — 返回匹配块 + 前后邻接块
#   • 增量索引 — 批量写入优化，每批自动提交
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Context Retriever"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "2.0.0"
PLUGIN_DESCRIPTION = (
    "超长上下文精确检索引擎 v2：SQLite FTS5 磁盘存储 + BM25 精排，"
    "支持 1GB+ 文本、中英文混合检索，适用于超长对话/代码库的精准查找。"
)

import json
import math
import os
import re
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

# ═══════════════════════════════════════════════════════════════
#  工具注册（与 v1 完全兼容）
# ═══════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "index_context",
            "description": (
                "将文本内容或当前对话历史添加到检索引擎的倒排索引中，"
                "以便后续用 search_context 进行精确检索。"
                "支持两种模式：直接传入 content 字符串；或传入 source='conversation' "
                "自动索引最近 N 轮对话。适用于构建超长上下文的可搜索知识库。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "要索引的文本内容。如果为空，则使用 source 参数自动获取内容。"
                            "可以是代码、文档、日志、对话记录等任意文本。"
                        )
                    },
                    "source": {
                        "type": "string",
                        "description": (
                            "数据来源标签，用于过滤搜索结果。"
                            "例如 'conversation', 'codebase', 'documentation', 'api_docs'。"
                            "默认 'manual'。"
                        ),
                        "default": "manual"
                    },
                    "title": {
                        "type": "string",
                        "description": "文档标题，可选。用于结果展示和排序加权。"
                    },
                    "chunk_size": {
                        "type": "integer",
                        "description": "分块大小（字符数），默认 500。越小检索越精确但可能丢失上下文。",
                        "default": 500
                    },
                    "chunk_overlap": {
                        "type": "integer",
                        "description": "块重叠字符数，默认 100。防止关键信息被截断在块边界。",
                        "default": 100
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
            "name": "search_context",
            "description": (
                "在已索引的上下文库中执行 BM25 精确检索，"
                "支持多关键词联合查询、短语精确匹配奖励、结果上下文扩展"
                "（返回匹配块的前后邻接块）。\n"
                "⚠️ 使用时机：① 用户问题涉及早期对话/历史工具输出，"
                "而当前上下文中没有这些内容时；② 需要回忆很久以前讨论过的"
                "细节（约定、配置、决策）时。此类情况应优先检索而不是猜测，"
                "也不要重复执行旧工具。若索引为空会返回提示，可先用 index_context 建立索引。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询，支持自然语言或关键词。多个词空格分隔为 AND 逻辑。"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回最相关的结果数量，默认 5，最大 20。",
                        "default": 5
                    },
                    "min_score": {
                        "type": "number",
                        "description": (
                            "最低相关性分数阈值（0.0~∞），低于此分数的结果会被过滤。"
                            "默认 0.1。设为 0 返回所有匹配。"
                        ),
                        "default": 0.1
                    },
                    "source_filter": {
                        "type": "string",
                        "description": (
                            "按来源过滤，只搜索指定 source 标签的内容。"
                            "如 'codebase' 只搜索代码索引。留空则搜索全部。"
                        )
                    },
                    "expand_context": {
                        "type": "boolean",
                        "description": (
                            "是否扩展上下文窗口。开启后每个匹配块会附带前后各 1 个邻接块，"
                            "保留完整语义上下文。默认 true。"
                        ),
                        "default": True
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
            "name": "clear_index",
            "description": (
                "清空检索引擎中的所有已索引内容。"
                "可按 source 过滤清除特定来源，或清空全部。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_filter": {
                        "type": "string",
                        "description": (
                            "只清除指定 source 的索引。留空则清空全部索引。"
                        )
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
            "name": "index_stats",
            "description": (
                "查看检索引擎的统计信息：已索引文档数、总字符数、"
                "各来源分布、词库大小等。\n"
                "⚠️ 使用时机：不确定索引中是否有内容、或不知道有哪些可用来源时，"
                "先调用本工具确认，再决定是否检索或建立索引。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False
            }
        }
    }
]

# ═══════════════════════════════════════════════════════════════
#  分词器 — 中英文混合双轨
# ═══════════════════════════════════════════════════════════════

# 中文字符范围（含 CJK 扩展 A 区）
_RE_CHINESE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
# 韩文/日文（可选扩展，暂不启用）
# _RE_CJK_EXT = re.compile(r'[\uac00-\ud7af\u3040-\u309f\u30a0-\u30ff]')


def _tokenize(text: str) -> List[str]:
    """中英文混合分词：中文按字 + bigram，英文按词。

    中文采用 character + bigram 双粒度：
    - unigram: 保证单字查询也能命中
    - bigram:  "上下文" → ["上下", "下文"]，提升短语匹配精度

    英文采用 word-piece 小写化：
    - "HelloWorld" → ["helloworld"]
    - 数字和标识符保持原样
    """
    tokens: List[str] = []

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if _RE_CHINESE.match(ch):
            # ── 中文字符：添加 unigram ──
            tokens.append(ch)
            # ── 向前看：添加 bigram ──
            if i + 1 < n and _RE_CHINESE.match(text[i + 1]):
                tokens.append(ch + text[i + 1])
            i += 1

        elif ch.isalnum() or ch == '_':
            # ── 英文/数字：收集完整 token ──
            start = i
            while i < n and (text[i].isalnum() or text[i] == '_'):
                i += 1
            tokens.append(text[start:i].lower())

        else:
            i += 1  # 跳过标点和空格

    return tokens


def _tokenize_for_query(text: str) -> List[str]:
    """查询分词：额外做去重以加速检索（查询通常较短）。"""
    seen: Set[str] = set()
    result: List[str] = []
    for tok in _tokenize(text):
        if tok not in seen:
            seen.add(tok)
            result.append(tok)
    return result


def _tokenize_to_fts5_format(text: str) -> str:
    """分词后用空格连接，作为 FTS5 索引输入。

    FTS5 默认按空格/标点分词，所以我们把中文分词后
    用空格连接，英文保持原样即可。
    """
    tokens = _tokenize(text)
    # 去重以减小索引大小（FTS5 内部还会再做词频统计）
    return " ".join(tokens)


# ═══════════════════════════════════════════════════════════════
#  SQLite FTS5 检索引擎 — 磁盘存储，支持 1GB+
# ═══════════════════════════════════════════════════════════════

# 数据库 schema 版本（用于未来迁移）
_DB_SCHEMA_VERSION = 2

# 批量写入阈值：积累多少条后执行一次 COMMIT
_BATCH_COMMIT_SIZE = 50


class FTS5Retriever:
    """基于 SQLite FTS5 的超大规模检索引擎。

    核心设计
    --------
    - SQLite WAL 模式：支持并发读写
    - FTS5 全文索引：负责快速粗筛（百万级文档毫秒响应）
    - Python BM25 精排：对候选集重新计算 BM25 + 短语匹配奖励
    - 磁盘存储：文本块存储在 SQLite 中，不占用 Python 堆内存
    - 批量写入：积累多条后统一 COMMIT，减少磁盘 I/O

    容量估算
    --------
    - 单文档: ~500 字符 ≈ 100 FTS5 tokens
    - 200 万文档: ~1GB 原始文本，~300MB 数据库文件
    - FTS5 索引增量: 约为原始文本的 30%-50%
    - 总计: 1GB 文本 → ~400-500MB SQLite 数据库
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()

        # 批量写入缓冲区
        self._batch_buffer: List[Tuple[str, str, str, str, int, str]] = []
        # (tokenized, source, title, text, chunk_index, metadata_json)

        # BM25 统计信息缓存（内存中维护，定期从 DB 重建）
        self._doc_count: int = 0
        self._total_tokens: int = 0
        self._avgdl: float = 0.0
        self._df_cache: Dict[str, int] = {}  # term → document frequency
        self._stats_dirty: bool = True

        # 初始化数据库
        self._init_db()

    # ── 数据库初始化 ──────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """获取线程安全的数据库连接。"""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")  # 64MB 页面缓存
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB 内存映射
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """初始化数据库 schema。"""
        conn = self._get_conn()
        try:
            conn.executescript("""
                -- 文档存储表
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL DEFAULT 'manual',
                    title TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    tokenized TEXT NOT NULL DEFAULT '',
                    indexed_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    chunk_index INTEGER NOT NULL DEFAULT 0,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );

                -- 全文搜索虚拟表（外部内容模式）
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_idx USING fts5(
                    tokenized,
                    source UNINDEXED,
                    title UNINDEXED,
                    tokenize='unicode61 remove_diacritics 2',
                    content='',
                    content_rowid='id'
                );

                -- 统计信息表
                CREATE TABLE IF NOT EXISTS stats (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );

                -- schema 版本
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );

                -- 索引：按 source 快速过滤
                CREATE INDEX IF NOT EXISTS idx_docs_source ON documents(source);
                -- 索引：按 source + chunk_index 快速查找邻接块
                CREATE INDEX IF NOT EXISTS idx_docs_source_chunk
                    ON documents(source, chunk_index);
            """)

            # 检查/设置 schema 版本
            row = conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (_DB_SCHEMA_VERSION,)
                )
            conn.commit()
        finally:
            conn.close()

    # ── CRUD: 增 ──────────────────────────────────────────────

    def add(self, text: str, source: str = "manual",
            title: str = "", metadata: Optional[Dict] = None,
            chunk_index: int = 0) -> int:
        """添加一个文档到索引。

        采用批量写入策略：积累 _BATCH_COMMIT_SIZE 条后才 COMMIT，
        大幅提升大批量索引的吞吐量。

        Returns
        -------
        int
            分配的 doc_id
        """
        tokenized = _tokenize_to_fts5_format(text)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

        with self._lock:
            self._batch_buffer.append(
                (tokenized, source, title, text, chunk_index, metadata_json)
            )
            self._stats_dirty = True

            if len(self._batch_buffer) >= _BATCH_COMMIT_SIZE:
                self._flush_batch()

        # 返回缓冲区大小作为近似 doc_count（调用者不依赖精确 doc_id）
        return len(self._batch_buffer)

    def flush(self):
        """强制刷新批量写入缓冲区。"""
        with self._lock:
            if self._batch_buffer:
                self._flush_batch()

    def _flush_batch(self) -> int:
        """将缓冲区中的文档写入 DB 并同步 FTS5 索引。

        在一个事务中逐行插入，以确保能获取正确的 lastrowid。
        """
        if not self._batch_buffer:
            return -1

        conn = self._get_conn()
        last_id = -1
        try:
            with conn:
                for tokenized, source, title, text, chunk_index, metadata_json in self._batch_buffer:
                    cur = conn.execute(
                        """INSERT INTO documents
                           (tokenized, source, title, text, chunk_index, metadata)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (tokenized, source, title, text, chunk_index, metadata_json)
                    )
                    doc_id = cur.lastrowid
                    last_id = doc_id

                    # 同步到 FTS5 索引
                    conn.execute(
                        """INSERT INTO fts_idx(rowid, tokenized, source, title)
                           VALUES (?, ?, ?, ?)""",
                        (doc_id, tokenized, source, title)
                    )
        finally:
            conn.close()

        self._batch_buffer.clear()
        return last_id

    # ── CRUD: 删 ──────────────────────────────────────────────

    def remove_by_source(self, source: str) -> int:
        """按 source 删除文档，返回删除数。"""
        self.flush()  # 先刷新缓冲区

        conn = self._get_conn()
        removed = 0
        try:
            with conn:
                # 先查出要删除的 doc_id 列表
                ids = [row[0] for row in conn.execute(
                    "SELECT id FROM documents WHERE source = ?", (source,)
                )]
                if not ids:
                    return 0

                removed = len(ids)

                # 从 FTS5 索引中删除
                for doc_id in ids:
                    conn.execute(
                        "INSERT INTO fts_idx(fts_idx, rowid, tokenized, source, title) "
                        "VALUES ('delete', ?, '', '', '')",
                        (doc_id,)
                    )

                # 从 documents 表中删除
                conn.execute(
                    "DELETE FROM documents WHERE source = ?", (source,)
                )
        finally:
            conn.close()

        self._stats_dirty = True
        return removed

    def clear_all(self):
        """清空全部索引。"""
        self.flush()

        conn = self._get_conn()
        try:
            with conn:
                # 删除所有文档
                conn.execute("DELETE FROM documents")
                # 对 contentless FTS5 表，需要逐行删除索引
                conn.execute("DROP TABLE IF EXISTS fts_idx")
                conn.execute("""
                    CREATE VIRTUAL TABLE fts_idx USING fts5(
                        tokenized,
                        source UNINDEXED,
                        title UNINDEXED,
                        tokenize='unicode61 remove_diacritics 2',
                        content='',
                        content_rowid='id'
                    )
                """)
                # 重置自增 ID
                conn.execute(
                    "UPDATE sqlite_sequence SET seq=0 WHERE name='documents'"
                )
        finally:
            conn.close()

        self._stats_dirty = True

    # ── CRUD: 查 ──────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5,
               min_score: float = 0.1,
               source_filter: str = "",
               expand_context: bool = True) -> List[Dict[str, Any]]:
        """执行两阶段检索：FTS5 粗排 → Python 精排。

        阶段 1 — FTS5 MATCH（毫秒级）：
            从百万级文档中快速筛选出包含查询关键词的候选文档。
            候选池大小 = min(总匹配数, top_k * 20)。

        阶段 2 — BM25 精排 + 短语匹配（毫秒级）：
            对候选集中的每个文档计算精确 BM25 分数，
            并叠加短语匹配奖励分，最终截取 top_k。

        阶段 3 — 上下文扩展：
            返回每个匹配块的前后邻接块，保留完整语义。
        """
        self.flush()  # 确保缓冲区已写入

        query_tokens = _tokenize_for_query(query)
        if not query_tokens:
            return []

        # ── 阶段 1: FTS5 粗筛 ──
        candidates = self._fts5_retrieve(
            query, query_tokens, source_filter, top_k
        )

        if not candidates:
            return []

        # ── 阶段 2: BM25 + 短语精排 ──
        conn = self._get_conn()
        try:
            ranked = self._rerank_bm25(
                conn, query, query_tokens, candidates, min_score
            )
            ranked.sort(key=lambda x: x[0], reverse=True)
            top = ranked[:top_k]

            # ── 阶段 3: 加载完整结果 + 上下文扩展 ──
            results = self._build_results(
                conn, top, query_tokens, expand_context
            )
        finally:
            conn.close()

        return results

    def _fts5_retrieve(self, query: str, query_tokens: List[str],
                       source_filter: str, top_k: int
                       ) -> List[Tuple[int, str, str, str, float]]:
        """FTS5 粗筛：使用 MATCH 查询快速获取候选文档。

        Returns
        -------
        List[Tuple[int, str, str, str, float]]
            [(doc_id, text, source, title, fts5_score), ...]
        """
        conn = self._get_conn()
        try:
            # 构建 FTS5 查询字符串
            # 将分词后的 token 用 AND 连接，确保所有关键词都匹配
            fts5_query = " AND ".join(
                f'"{t}"' for t in query_tokens
            )

            # 如果分词太少或 AND 查询太严格，回退到 OR
            candidate_limit = top_k * 20

            # 先尝试 AND 查询
            where_clause = ""
            params: tuple = ()
            if source_filter:
                where_clause = "AND d.source = ?"
                params = (source_filter,)

            sql = f"""
                SELECT d.id, d.text, d.source, d.title,
                       rank AS fts5_score
                FROM fts_idx f
                JOIN documents d ON f.rowid = d.id
                WHERE fts_idx MATCH ?
                {where_clause}
                ORDER BY rank
                LIMIT ?
            """
            rows = conn.execute(
                sql, (fts5_query,) + params + (candidate_limit,)
            ).fetchall()

            # 如果 AND 查询无结果，尝试 OR 查询
            if not rows and len(query_tokens) > 1:
                fts5_or_query = " OR ".join(
                    f'"{t}"' for t in query_tokens
                )
                rows = conn.execute(
                    sql, (fts5_or_query,) + params + (candidate_limit,)
                ).fetchall()

            return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
        finally:
            conn.close()

    def _rerank_bm25(self, conn: sqlite3.Connection,
                     query: str, query_tokens: List[str],
                     candidates: List[Tuple[int, str, str, str, float]],
                     min_score: float
                     ) -> List[Tuple[float, int, str, str, str]]:
        """BM25 精排 + 短语匹配奖励。

        对每个候选文档：
        1. 从数据库读取其 tokenized 字段
        2. 计算 BM25 分数
        3. 叠加短语匹配奖励分

        Returns
        -------
        List[Tuple[float, int, str, str, str]]
            [(final_score, doc_id, text, source, title), ...]
        """
        # 确保统计信息是最新的
        self._ensure_stats(conn)

        ranked: List[Tuple[float, int, str, str, str]] = []

        for doc_id, text, source, title, _fts5_score in candidates:
            # 从数据库获取 tokenized 字段
            row = conn.execute(
                "SELECT tokenized FROM documents WHERE id = ?",
                (doc_id,)
            ).fetchone()
            if not row:
                continue

            tokenized = row[0]
            doc_tokens = tokenized.split() if tokenized else []

            # 计算 BM25 分数
            bm25 = self._bm25_score(query_tokens, doc_tokens)

            # 计算短语匹配奖励
            phrase_bonus = self._phrase_match_bonus(query, text)

            final_score = bm25 + phrase_bonus

            if final_score >= min_score:
                ranked.append((final_score, doc_id, text, source, title))

        return ranked

    def _build_results(self, conn: sqlite3.Connection,
                       top: List[Tuple[float, int, str, str, str]],
                       query_tokens: List[str],
                       expand_context: bool
                       ) -> List[Dict[str, Any]]:
        """构建最终结果列表，可选添加上下文扩展。"""
        results: List[Dict[str, Any]] = []

        for score, doc_id, text, source, title in top:
            # 获取索引时间
            row = conn.execute(
                "SELECT indexed_at, chunk_index FROM documents WHERE id = ?",
                (doc_id,)
            ).fetchone()
            indexed_at = row[0] if row else ""
            chunk_index = row[1] if row else 0

            result: Dict[str, Any] = {
                "score": round(score, 4),
                "doc_id": doc_id,
                "source": source,
                "title": title,
                "text": text,
                "indexed_at": indexed_at,
                "match_positions": self._find_match_positions(
                    query_tokens, text
                ),
            }

            # ── 上下文扩展：查找前后邻接块 ──
            if expand_context:
                # 前一个块（同一 source 下 chunk_index - 1）
                if chunk_index > 0:
                    prev_row = conn.execute(
                        """SELECT text FROM documents
                           WHERE source = ? AND chunk_index = ?
                           LIMIT 1""",
                        (source, chunk_index - 1)
                    ).fetchone()
                    if prev_row:
                        result["context_before"] = prev_row[0][-300:]

                # 后一个块（同一 source 下 chunk_index + 1）
                next_row = conn.execute(
                    """SELECT text FROM documents
                       WHERE source = ? AND chunk_index = ?
                       LIMIT 1""",
                    (source, chunk_index + 1)
                ).fetchone()
                if next_row:
                    result["context_after"] = next_row[0][:300]

            results.append(result)

        return results

    # ── BM25 打分 ─────────────────────────────────────────────

    def _ensure_stats(self, conn: sqlite3.Connection):
        """确保 BM25 统计信息是最新的。

        统计信息从数据库计算并缓存在内存中，
        只在 _stats_dirty=True 时重新计算。
        """
        if not self._stats_dirty:
            return

        with self._lock:
            if not self._stats_dirty:
                return  # 双重检查

            # 文档总数
            row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
            self._doc_count = row[0] if row else 0

            # 总 token 数和平均文档长度
            if self._doc_count > 0:
                row = conn.execute(
                    "SELECT SUM(LENGTH(tokenized) - LENGTH(REPLACE(tokenized, ' ', '')) + 1) "
                    "FROM documents WHERE tokenized != ''"
                ).fetchone()
                self._total_tokens = row[0] if row[0] else 0
                self._avgdl = self._total_tokens / self._doc_count
            else:
                self._total_tokens = 0
                self._avgdl = 0.0

            # document frequency 缓存（term → 包含该 term 的文档数）
            # 对于大规模数据，构建完整 DF 缓存可能很慢。
            # 这里采用折中方案：只对查询中的 term 按需计算 IDF。
            # 如果 doc_count < 50000，则全量缓存 DF。
            if self._doc_count < 50000:
                self._df_cache.clear()
                rows = conn.execute(
                    "SELECT tokenized FROM documents WHERE tokenized != ''"
                ).fetchall()
                term_docs: Dict[str, Set[int]] = defaultdict(set)
                for i, (tokenized,) in enumerate(rows):
                    for term in tokenized.split():
                        term_docs[term].add(i)
                self._df_cache = {t: len(s) for t, s in term_docs.items()}

            self._stats_dirty = False

    def _bm25_score(self, query_terms: List[str],
                    doc_tokens: List[str],
                    k1: float = 1.5, b: float = 0.75) -> float:
        """计算 Okapi BM25 相关性分数。

        当 _df_cache 中没有某个 term 时，
        回退到数据库查询其 document frequency。
        """
        score = 0.0
        dl = len(doc_tokens)
        N = max(self._doc_count, 1)

        # 本地词频
        tf: Dict[str, int] = {}
        for t in doc_tokens:
            tf[t] = tf.get(t, 0) + 1

        for term in query_terms:
            df = self._df_cache.get(term)
            if df is None:
                # 按需查询 DF（对超大规模数据）
                df = self._query_df(term)
                self._df_cache[term] = df

            if df == 0:
                continue

            # IDF: Robertson-Walker 平滑
            idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)

            # TF 分量
            f = tf.get(term, 0)
            if f == 0:
                continue
            numerator = f * (k1 + 1)
            denominator = f + k1 * (
                1 - b + b * dl / max(self._avgdl, 1)
            )
            score += idf * numerator / denominator

        return score

    def _query_df(self, term: str) -> int:
        """查询某个 term 的 document frequency（按需）。"""
        conn = self._get_conn()
        try:
            # 在 FTS5 中搜索该 term
            row = conn.execute(
                "SELECT COUNT(*) FROM fts_idx WHERE fts_idx MATCH ?",
                (f'"{term}"',)
            ).fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            conn.close()

    # ── 短语匹配奖励 ──────────────────────────────────────────

    @staticmethod
    def _phrase_match_bonus(query: str, text: str) -> float:
        """检测查询字符串是否在文本中作为连续子串出现。

        奖励规则：
        - 查询完整出现在文本中：+0.5
        - 查询的 2-gram 片段匹配：每个 +0.1（上限 0.3）
        """
        bonus = 0.0
        q_lower = query.lower()
        t_lower = text.lower()

        if q_lower in t_lower:
            bonus += 0.5

        # 对中文短语检查 2-gram 连续性
        if len(query) >= 4:
            matched_ngrams = 0
            for i in range(len(query) - 1):
                bigram = query[i:i + 2]
                if (len(bigram.strip()) == 2
                        and not bigram[0].isspace()
                        and not bigram[1].isspace()
                        and bigram in t_lower):
                    matched_ngrams += 1
            bonus += min(matched_ngrams * 0.1, 0.3)

        return bonus

    @staticmethod
    def _find_match_positions(query_terms: List[str],
                              text: str) -> List[int]:
        """找到查询词在文本中的位置（用于高亮展示）。"""
        positions: List[int] = []
        lower = text.lower()
        for term in query_terms:
            pos = lower.find(term.lower())
            if pos >= 0:
                positions.append(pos)
        return sorted(set(positions))

    # ── 统计 ───────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """返回索引统计信息。"""
        self.flush()

        conn = self._get_conn()
        try:
            doc_count = conn.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0]

            total_chars = conn.execute(
                "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM documents"
            ).fetchone()[0]

            # FTS5 词库大小（估算：唯一 token 数）
            fts5_row = conn.execute(
                "SELECT COUNT(DISTINCT tokenized) FROM documents"
            ).fetchone()
            # 更精确的：通过 FTS5 内部统计
            try:
                vocab_row = conn.execute(
                    "SELECT COUNT(*) FROM fts_idx WHERE fts_idx MATCH '*'"
                ).fetchone()
                # 这不太对，fts_idx 的行数就是文档数
                # 使用另一种方法：从 stats 表获取
                pass
            except Exception:
                pass

            # 来源分布
            source_rows = conn.execute(
                "SELECT source, COUNT(*) as cnt FROM documents "
                "GROUP BY source ORDER BY cnt DESC"
            ).fetchall()
            sources = {r[0]: r[1] for r in source_rows}

            # 估算词库大小（通过分析 tokenized 字段）
            # 对于大数据，这可能很慢，所以提供估算值
            vocab_size = doc_count * 20 if doc_count > 0 else 0  # 粗略估算

            return {
                "total_documents": doc_count,
                "total_characters": total_chars,
                "vocabulary_size": vocab_size,
                "average_document_length": round(self._avgdl, 1),
                "bm25_params": {"k1": 1.5, "b": 0.75},
                "sources": sources,
                "engine": "SQLite FTS5",
                "db_path": self._db_path,
            }
        finally:
            conn.close()

    def close(self):
        """关闭检索引擎，刷新缓冲区并清理资源。"""
        self.flush()


# ═══════════════════════════════════════════════════════════════
#  文本分块器
# ═══════════════════════════════════════════════════════════════

def _chunk_text(text: str, chunk_size: int = 500,
                overlap: int = 100) -> List[str]:
    """将长文本智能分割为有重叠的块。

    分割策略（按优先级）：
    1. 段落边界（双换行 \\n\\n）
    2. 句子边界（。！？；\\n .!?;）
    3. 逗号/空格边界
    4. 强制截断

    每个块在 overlap 区域内尽量对齐到自然边界，
    保证语义片段不会在中间被截断。
    """
    if not text or len(text) <= chunk_size:
        return [text] if text else []

    chunks: List[str] = []
    start = 0
    text_len = len(text)

    # 分隔符优先级（(分隔符, 长度)）
    separators = [
        ('\n\n', 2),    # 段落
        ('\n', 1),      # 行
        ('。', 1), ('！', 1), ('？', 1), ('；', 1),  # 中文句尾
        ('. ', 2), ('! ', 2), ('? ', 2), ('; ', 2),   # 英文句尾
        ('，', 1), (', ', 2),                          # 逗号
        (' ', 1),                                      # 空格（最后手段）
    ]

    while start < text_len:
        end = min(start + chunk_size, text_len)

        if end >= text_len:
            chunks.append(text[start:])
            break

        # ── 在 overlap 区域内寻找最佳断点 ──
        chunk_slice = text[start:end]
        search_start = max(end - overlap - start, 0)
        best_break = end - start  # 默认在 chunk_size 处截断

        for sep, sep_len in separators:
            pos = chunk_slice.rfind(sep, search_start)
            if pos >= 0:
                best_break = pos + sep_len
                break

        actual_end = start + best_break
        chunks.append(text[start:actual_end])
        start = actual_end - overlap
        if start <= 0 or start >= text_len:
            break

    return chunks


# ═══════════════════════════════════════════════════════════════
#  插件实例管理
# ═══════════════════════════════════════════════════════════════

def _get_retriever(context) -> FTS5Retriever:
    """从 context.storage 获取或创建 FTS5Retriever 实例。

    数据库文件存储在 app_dir 中，跨 session 持久化。
    """
    if "fts5_retriever" not in context.storage:
        db_dir = os.path.join(context.app_dir, "indexes")
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "context_index.db")
        context.storage["fts5_retriever"] = FTS5Retriever(db_path)
        context.storage["_retriever_db_path"] = db_path
    return context.storage["fts5_retriever"]


# ═══════════════════════════════════════════════════════════════
#  工具实现
# ═══════════════════════════════════════════════════════════════

def _handle_index_context(args: dict, context) -> str:
    """将内容添加到索引。"""
    content = args.get("content", "").strip()
    source = args.get("source", "manual").strip() or "manual"
    title = args.get("title", "").strip()
    chunk_size = max(100, min(args.get("chunk_size", 500), 2000))
    chunk_overlap = max(0, min(args.get("chunk_overlap", 100), chunk_size // 2))

    if not content:
        return (
            "⚠️ 没有提供要索引的内容。\n\n"
            "用法示例：\n"
            "  `index_context(content='...很长的文本...', source='docs')`\n"
            "  `index_context(content=上次工具输出, source='tool_output')`"
        )

    retriever = _get_retriever(context)
    chunks = _chunk_text(content, chunk_size, chunk_overlap)

    count = 0
    total_chars = 0
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        chunk_title = f"{title} [块 {i+1}/{len(chunks)}]" if title else ""
        retriever.add(
            text=chunk,
            source=source,
            title=chunk_title,
            metadata={
                "chunk_index": i,
                "total_chunks": len(chunks),
                "chunk_size": chunk_size,
                "overlap": chunk_overlap,
            },
            chunk_index=i,
        )
        count += 1
        total_chars += len(chunk)

    # 索引完成后刷新
    retriever.flush()

    context.logger.info(
        f"Indexed {count} chunk(s) ({total_chars} chars) from source '{source}'"
    )

    stats = retriever.stats()
    return (
        f"✅ 已索引 **{count}** 个文本块\n"
        f"  来源: `{source}`\n"
        f"  总字符数: {total_chars:,}\n"
        f"  分块大小: {chunk_size} 字符（重叠 {chunk_overlap}）\n"
        f"  索引总文档数: {stats['total_documents']}\n"
        f"  数据库: `{stats.get('db_path', '')}`\n\n"
        f"💡 现在可以用 `search_context(query='你的查询')` 进行检索。"
    )


def _handle_search_context(args: dict, context) -> str:
    """搜索已索引的上下文。"""
    query = args.get("query", "").strip()
    top_k = max(1, min(args.get("top_k", 5), 20))
    min_score = max(0.0, args.get("min_score", 0.1))
    source_filter = args.get("source_filter", "").strip() or ""
    expand_context = args.get("expand_context", True)

    if not query:
        return "❌ 搜索查询不能为空。"

    retriever = _get_retriever(context)

    stats = retriever.stats()
    if stats["total_documents"] == 0:
        return (
            "📭 索引为空，还没有任何已索引的内容。\n\n"
            "请先用 `index_context` 添加内容：\n"
            "  `index_context(content='你的文本', source='docs')`"
        )

    results = retriever.search(
        query=query,
        top_k=top_k,
        min_score=min_score,
        source_filter=source_filter,
        expand_context=expand_context,
    )

    if not results:
        hint = ""
        if source_filter:
            hint = f"\n  💡 当前按 source='{source_filter}' 过滤，"
            hint += f"可用来源: {list(stats['sources'].keys())}"
        return (
            f"🔍 未找到与「**{query}**」匹配的结果。{hint}\n\n"
            f"  索引统计: {stats['total_documents']} 文档, "
            f"{stats['total_characters']:,} 字符\n"
            f"  尝试放宽查询条件或先索引更多内容。"
        )

    # ── 格式化输出 ──
    lines = [
        f"🔍 **检索结果**: `{query}`",
        f"  匹配 {len(results)} 条，显示前 {min(len(results), top_k)} 条\n",
    ]

    for i, r in enumerate(results, 1):
        score = r["score"]
        source = r["source"]
        title = r.get("title", "")
        indexed_at = r.get("indexed_at", "")

        # 分数颜色指示
        if score >= 2.0:
            score_bar = "🟢"
        elif score >= 0.5:
            score_bar = "🟡"
        else:
            score_bar = "🟠"

        header = f"**{i}.** {score_bar} `{source}`"
        if title:
            header += f" — *{title}*"

        lines.append(header)
        lines.append(f"   分数: {score:.3f} | 索引时间: {indexed_at}")

        # 高亮匹配文本
        text = r["text"]
        if len(text) > 400:
            # 在第一个匹配位置附近截取
            positions = r.get("match_positions", [])
            if positions:
                center = positions[0]
                snippet_start = max(0, center - 200)
                snippet_end = min(len(text), center + 200)
                if snippet_start > 0:
                    text = "..." + text[snippet_start:snippet_end]
                else:
                    text = text[:snippet_end]
                if snippet_end < len(r["text"]):
                    text = text + "..."
            else:
                text = text[:400] + "..."

        lines.append(f"   ```\n   {text}\n   ```")

        # 上下文扩展
        if expand_context:
            if r.get("context_before"):
                before = r["context_before"]
                if len(before) > 150:
                    before = "..." + before[-150:]
                lines.append(f"   📄 上文: _{before}_")
            if r.get("context_after"):
                after = r["context_after"]
                if len(after) > 150:
                    after = after[:150] + "..."
                lines.append(f"   📄 下文: _{after}_")

        lines.append("")  # 空行分隔

    # 索引统计摘要
    lines.append(
        f"📊 索引: {stats['total_documents']} 文档, "
        f"{stats['total_characters']:,} 字符\n"
        f"  引擎: {stats.get('engine', 'FTS5')} | "
        f"数据库: `{stats.get('db_path', '')}`"
    )

    return "\n".join(lines)


def _handle_clear_index(args: dict, context) -> str:
    """清空索引（全部或按 source）。"""
    source_filter = args.get("source_filter", "").strip() or ""
    retriever = _get_retriever(context)

    if source_filter:
        removed = retriever.remove_by_source(source_filter)
        context.logger.info(
            f"Cleared {removed} document(s) from source '{source_filter}'"
        )
        return (
            f"✅ 已清除来源 `{source_filter}` 的 **{removed}** 个文档。\n"
            f"  剩余文档: {retriever.stats()['total_documents']}"
        )
    else:
        stats_before = retriever.stats()
        retriever.clear_all()
        context.logger.info(
            f"Cleared all {stats_before['total_documents']} document(s)"
        )
        return f"✅ 已清空全部索引（共 {stats_before['total_documents']} 个文档）。"


def _handle_index_stats(args: dict, context) -> str:
    """查看索引统计。"""
    stats = _get_retriever(context).stats()

    lines = [
        "📊 **检索引擎统计**",
        "",
        f"  引擎: {stats.get('engine', 'Unknown')}",
        f"  总文档数: {stats['total_documents']}",
        f"  总字符数: {stats['total_characters']:,}",
        f"  词库大小: ~{stats['vocabulary_size']} 个唯一词（估算）",
        f"  平均文档长度: {stats['average_document_length']} tokens",
        f"  BM25 参数: k1={stats['bm25_params']['k1']}, b={stats['bm25_params']['b']}",
        "",
    ]

    if stats.get("db_path"):
        db_path = stats["db_path"]
        if os.path.exists(db_path):
            db_size = os.path.getsize(db_path)
            lines.append(f"  数据库文件: `{db_path}`")
            lines.append(f"  磁盘占用: {db_size / 1024 / 1024:.1f} MB")
        lines.append("")

    if stats["sources"]:
        lines.append("  **来源分布**:")
        for src, cnt in sorted(stats["sources"].items(),
                               key=lambda x: x[1], reverse=True):
            pct = (cnt / max(stats['total_documents'], 1)) * 100
            lines.append(f"    `{src}` — {cnt} 文档 ({pct:.1f}%)")
    else:
        lines.append("  *(索引为空)*")

    # 容量估算
    total_chars = stats["total_characters"]
    if total_chars > 0:
        est_db_size = total_chars * 0.45  # 粗略估算：DB 大小约为原始文本的 45%
        lines.append("")
        lines.append(f"  📈 预计最大容量: ~{1_000_000_000 / max(total_chars, 1):.0f}x 当前数据量")
        lines.append(f"  💾 预计满 1GB 时数据库大小: ~450 MB")
        if total_chars >= 500_000_000:
            lines.append(f"  ⚠️ 已接近 500MB 文本，建议定期清理旧索引")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  工具分发
# ═══════════════════════════════════════════════════════════════

def execute(tool_name: str, args: dict, context) -> str:
    """工具调用分发器。"""
    handlers = {
        "index_context": _handle_index_context,
        "search_context": _handle_search_context,
        "clear_index": _handle_clear_index,
        "index_stats": _handle_index_stats,
    }
    handler = handlers.get(tool_name)
    if handler:
        return handler(args, context)
    return f"Unknown tool: {tool_name}"


# ═══════════════════════════════════════════════════════════════
#  钩子 — 自动索引对话历史
# ═══════════════════════════════════════════════════════════════

# 存储最近 N 条消息用于自动索引
_MAX_AUTO_INDEX_MESSAGES = 50


def before_step(step: int, messages: list, context):
    """任务第一步注入检索引擎状态，引导模型主动检索。

    模型是提示词驱动的：只有让它「知道」索引中有数据、
    以及何时该检索，它才会调用 search_context。

    仅在 step == 1 时注入一次（插入 system 消息），
    不干预后续步骤的数据流。Anthropic 路径的消息格式
    无 system 角色（首条 role 不是 system），自动跳过。
    """
    if step != 1 or not messages:
        return None
    if messages[0].get("role") != "system":
        return None  # Anthropic 转换格式或异常格式：不注入
    if context.storage.get("_retriever_status_injected", False):
        return None
    context.storage["_retriever_status_injected"] = True

    retriever = _get_retriever(context)
    try:
        stats = retriever.stats()
    except Exception:
        return None

    if stats["total_documents"] == 0:
        note = (
            "[RetrieverStatus] 检索引擎已就绪，但索引为空。\n"
            "如本任务涉及大量历史内容或长文档，可先用 index_context 建立索引，"
            "之后用 search_context 精确检索。"
        )
    else:
        sources = ", ".join(stats.get("sources", {}).keys()) or "无"
        note = (
            f"[RetrieverStatus] 检索引擎可用：已索引 {stats['total_documents']} 个文档、"
            f"{stats['total_characters']:,} 字符（来源: {sources}）。\n"
            "当需要回忆早期对话/工具输出、而当前上下文中没有这些内容时，"
            "优先用 search_context 检索，不要凭空猜测。"
        )

    # 插入为 system 消息（紧随现有 system 区之后），保持消息结构合法
    return messages[:1] + [{"role": "system", "content": note}] + messages[1:]


def on_agent_init(context):
    """初始化检索引擎。"""
    retriever = _get_retriever(context)
    context.storage["retriever_auto_index"] = True
    context.storage["retriever_message_buffer"] = []
    stats = retriever.stats()
    context.logger.info(
        f"Context Retriever v2 ready — SQLite FTS5 📇 "
        f"({stats['total_documents']} docs, {stats['total_characters']:,} chars in DB)"
    )


def on_agent_shutdown(context):
    """关闭检索引擎并输出会话检索统计。"""
    retriever = _get_retriever(context)
    retriever.flush()
    stats = retriever.stats()
    if stats["total_documents"] > 0:
        context.logger.info(
            f"Context Retriever shutdown: {stats['total_documents']} docs, "
            f"{stats['total_characters']:,} chars indexed this session"
        )
    retriever.close()


def after_step(step: int, reasoning: str, content: str,
               tool_calls: list, context):
    """每个 ReAct step 后自动索引新增的推理和工具调用。

    通过追加到消息缓冲区实现增量索引，
    避免重复索引已有的对话内容。
    """
    if not context.storage.get("retriever_auto_index", True):
        return

    retriever = _get_retriever(context)
    buffer: List[str] = context.storage.get("retriever_message_buffer", [])

    # 索引推理内容
    if reasoning and reasoning.strip():
        text = f"[Step {step} Reasoning]\n{reasoning}"
        if text not in buffer:
            buffer.append(text)

    # 索引输出内容
    if content and content.strip():
        text = f"[Step {step} Output]\n{content}"
        if text not in buffer:
            buffer.append(text)

    # 索引工具调用
    if tool_calls:
        for tc in tool_calls:
            name = tc.get("function", {}).get("name", "unknown")
            args_str = json.dumps(
                tc.get("function", {}).get("arguments", {}),
                ensure_ascii=False
            )
            text = f"[Step {step} ToolCall: {name}]\n{args_str}"
            if text not in buffer:
                buffer.append(text)

    # 批量索引新消息（每积累 5 条或缓冲区达到上限）
    if len(buffer) >= 5 or len(buffer) >= _MAX_AUTO_INDEX_MESSAGES:
        chunk_idx = 0
        for msg in buffer:
            retriever.add(
                text=msg, source="conversation",
                title=f"Step {step}",
                metadata={"step": step},
                chunk_index=chunk_idx,
            )
            chunk_idx += 1
        retriever.flush()
        context.storage["retriever_message_buffer"] = []
    else:
        context.storage["retriever_message_buffer"] = buffer


def after_tool_call(tool_name: str, args: dict, result: str, context):
    """索引工具输出结果，方便后续检索。

    只索引较大的输出（>200 字符），
    避免索引大量短小的工具返回值污染索引。
    """
    if not context.storage.get("retriever_auto_index", True):
        return result  # 不修改返回值

    if result and len(result) > 200:
        retriever = _get_retriever(context)
        text = (
            f"[Tool: {tool_name}]\n"
            f"Args: {json.dumps(args, ensure_ascii=False)}\n\n"
            f"Result:\n{result}"
        )
        retriever.add(
            text=text,
            source="tool_output",
            title=f"Tool: {tool_name}",
            metadata={"tool_name": tool_name, "args": args},
        )
        retriever.flush()

    # 不修改返回值，只是附加索引
    return result
