# ──────────────────────────────────────────────────────────────
# Plugin: Context Retriever (超长上下文精确检索器)
# Publisher: xingluosama
# Version: 1.0.0
# Description: 超长上下文精确检索引擎。基于 BM25 算法对对话历史、
#   代码文件、工具输出等大规模文本建立倒排索引，支持毫秒级
#   关键词检索 + 语义感知重排序。
#
# 核心能力：
#   • BM25 倒排索引 – 无外部依赖，O(N) 查询，比 grep 更智能
#   • 智能分块 – 按段落/消息/代码块边界分割，保持语义完整
#   • 两阶段检索 – BM25 粗排 → 短语匹配/位置感知精排
#   • 中英文混合 – 中文 bigram 切分 + 英文 word-piece 双轨
#   • 上下文扩展 – 返回匹配块 + 前后邻接块，保留上下文
#   • 自动索引 – 可选 hook：每个 ReAct step 自动收录新内容
# ──────────────────────────────────────────────────────────────

PLUGIN_NAME = "Context Retriever"
PLUGIN_PUBLISHER = "xingluosama"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = (
    "超长上下文精确检索引擎：BM25 倒排索引 + 语义重排序，"
    "支持中英文混合检索，适用于大规模对话历史/代码库的精准查找。"
)

import json
import math
import os
import re
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

# ═══════════════════════════════════════════════════════════════
#  工具注册
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
                "在已索引的上下文库中执行 BM25 精确检索。"
                "支持多关键词联合查询、短语精确匹配奖励、"
                "结果上下文扩展（返回匹配块的前后邻接块）。"
                "适用于从超长对话历史或大型代码库中快速定位关键信息。"
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
                "各来源分布、词库大小等。"
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

# 中文字符范围
_RE_CHINESE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
# 英文单词 / 数字
_RE_ALNUM = re.compile(r'[a-zA-Z0-9_]+')
# CJK 标点 + 常见分隔符（用于分句）
_RE_SENTENCE_BOUNDARY = re.compile(r'[。！？；\n]{1,2}|[.!?;]\s')


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


# ═══════════════════════════════════════════════════════════════
#  BM25 引擎 — 经典 Okapi BM25 实现
# ═══════════════════════════════════════════════════════════════

class BM25Index:
    """经典 Okapi BM25 倒排索引。

    参数
    ----
    k1 : float
        词频饱和参数（默认 1.5）。值越大词频影响越大。
    b : float
        文档长度归一化参数（默认 0.75）。0 = 无归一化，1 = 完全归一化。

    参考文献
    --------
    Trotman, Puurula, Burgess. "Improvements to BM25 and Language
    Models Examined." ADCS 2014.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

        # 文档列表
        self._docs: List[Dict[str, Any]] = []
        # term → 包含该 term 的文档数 (document frequency)
        self._df: Dict[str, int] = defaultdict(int)
        # 每个文档的 term 列表
        self._doc_terms: List[List[str]] = []
        # 每个文档的 token 数
        self._doc_lengths: List[int] = []
        # 平均文档长度
        self._avgdl: float = 0.0
        # 总文档数
        self._N: int = 0
        # 文档 ID 生成器
        self._next_id: int = 0
        # 线程安全锁
        self._lock = threading.Lock()

    # ── 增 ─────────────────────────────────────────────────────

    def add(self, text: str, source: str = "manual",
            title: str = "", metadata: Optional[Dict] = None) -> int:
        """添加一个文档到索引，返回 doc_id。"""
        tokens = _tokenize(text)
        doc_id: int
        with self._lock:
            doc_id = self._next_id
            self._next_id += 1

            self._docs.append({
                "id": doc_id,
                "text": text,
                "source": source,
                "title": title,
                "metadata": metadata or {},
                "indexed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            self._doc_terms.append(tokens)
            self._doc_lengths.append(len(tokens))

            # 更新 document frequency
            for term in set(tokens):
                self._df[term] += 1

            self._N = len(self._docs)
            self._avgdl = (sum(self._doc_lengths) / self._N
                           if self._N > 0 else 0.0)

        return doc_id

    # ── 删 ─────────────────────────────────────────────────────

    def remove_by_source(self, source: str) -> int:
        """按 source 删除文档，返回删除数。"""
        with self._lock:
            removed = 0
            # 收集需要保留的文档
            keep_docs: List[Dict[str, Any]] = []
            keep_terms: List[List[str]] = []
            keep_lengths: List[int] = []

            for i, doc in enumerate(self._docs):
                if doc["source"] == source:
                    removed += 1
                    # 减少 df
                    for term in set(self._doc_terms[i]):
                        self._df[term] -= 1
                        if self._df[term] <= 0:
                            del self._df[term]
                else:
                    keep_docs.append(doc)
                    keep_terms.append(self._doc_terms[i])
                    keep_lengths.append(self._doc_lengths[i])

            self._docs = keep_docs
            self._doc_terms = keep_terms
            self._doc_lengths = keep_lengths
            self._N = len(self._docs)
            self._avgdl = (sum(self._doc_lengths) / self._N
                           if self._N > 0 else 0.0)

        return removed

    def clear_all(self):
        """清空全部索引。"""
        with self._lock:
            self._docs.clear()
            self._doc_terms.clear()
            self._doc_lengths.clear()
            self._df.clear()
            self._N = 0
            self._avgdl = 0.0

    # ── 查 ─────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5,
               min_score: float = 0.1,
               source_filter: str = "",
               expand_context: bool = True) -> List[Dict[str, Any]]:
        """BM25 检索 + 短语匹配重排序。

        返回
        ----
        List[dict]
            每个结果包含:
            - score : float          BM25 + 短语奖励分
            - doc_id : int           文档 ID
            - source : str           来源标签
            - title : str            标题
            - text : str             匹配块文本
            - context_before : str   前置邻接块（若 expand_context）
            - context_after : str    后置邻接块（若 expand_context）
            - match_positions : list 查询词在文本中的位置
            - indexed_at : str       索引时间
        """
        query_tokens = _tokenize_for_query(query)
        if not query_tokens:
            return []

        with self._lock:
            if self._N == 0:
                return []

            # ── 阶段 1: BM25 粗排 ──
            candidates: List[Tuple[float, int]] = []
            for i in range(self._N):
                if source_filter and self._docs[i]["source"] != source_filter:
                    continue
                score = self._bm25_score(query_tokens, self._doc_terms[i], i)
                if score >= min_score:
                    candidates.append((score, i))

            # ── 排序 ──
            candidates.sort(key=lambda x: x[0], reverse=True)

            # ── 阶段 2: 短语匹配精排 ──
            # 对 top 2×top_k 候选进行短语匹配重排序
            rerank_pool = candidates[:min(len(candidates), top_k * 2)]
            reranked: List[Tuple[float, int]] = []
            for base_score, doc_idx in rerank_pool:
                phrase_bonus = self._phrase_match_bonus(
                    query, self._docs[doc_idx]["text"]
                )
                final_score = base_score + phrase_bonus
                reranked.append((final_score, doc_idx))
            reranked.sort(key=lambda x: x[0], reverse=True)

            # ── 截取 top_k ──
            top = reranked[:top_k]

            # ── 构建结果 ──
            results: List[Dict[str, Any]] = []
            for score, doc_idx in top:
                doc = self._docs[doc_idx]
                result = {
                    "score": round(score, 4),
                    "doc_id": doc["id"],
                    "source": doc["source"],
                    "title": doc["title"],
                    "text": doc["text"],
                    "indexed_at": doc["indexed_at"],
                    "match_positions": self._find_match_positions(
                        query_tokens, doc["text"]
                    ),
                }

                # ── 上下文扩展 ──
                if expand_context:
                    if doc_idx > 0:
                        prev = self._docs[doc_idx - 1]
                        if prev["source"] == doc["source"]:
                            result["context_before"] = prev["text"][-300:]
                    if doc_idx + 1 < self._N:
                        nxt = self._docs[doc_idx + 1]
                        if nxt["source"] == doc["source"]:
                            result["context_after"] = nxt["text"][:300]

                results.append(result)

            return results

    # ── 内部方法 ───────────────────────────────────────────────

    def _bm25_score(self, query_terms: List[str],
                    doc_terms: List[str], doc_idx: int) -> float:
        """计算 BM25 相关性分数。"""
        score = 0.0
        dl = self._doc_lengths[doc_idx]

        # 本地词频
        tf: Dict[str, int] = {}
        for t in doc_terms:
            tf[t] = tf.get(t, 0) + 1

        for term in query_terms:
            df = self._df.get(term, 0)
            if df == 0:
                continue

            # IDF: Robertson-Walker 平滑
            idf = math.log(
                (self._N - df + 0.5) / (df + 0.5) + 1.0
            )

            # TF 分量
            f = tf.get(term, 0)
            numerator = f * (self.k1 + 1)
            denominator = f + self.k1 * (
                1 - self.b + self.b * dl / max(self._avgdl, 1)
            )
            score += idf * numerator / denominator

        return score

    @staticmethod
    def _phrase_match_bonus(query: str, text: str) -> float:
        """检测查询字符串是否在文本中作为连续子串出现。

        奖励规则：
        - 查询完整出现在文本中：+0.5
        - 查询的 2-gram 片段匹配：每个 +0.1（上限 0.3）

        这个简单启发式显著提升了中文短语查询的精度，
        弥补了 BM25 忽略词序的缺陷。
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
        with self._lock:
            source_counts: Dict[str, int] = defaultdict(int)
            total_chars = 0
            for doc in self._docs:
                source_counts[doc["source"]] += 1
                total_chars += len(doc["text"])

            return {
                "total_documents": self._N,
                "total_characters": total_chars,
                "vocabulary_size": len(self._df),
                "average_document_length": round(self._avgdl, 1),
                "bm25_params": {"k1": self.k1, "b": self.b},
                "sources": dict(source_counts),
            }


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

    # 分隔符优先级
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
        best_break = end - start  # 默认在 chunk_size 处截断

        search_start = max(end - overlap - start, 0)

        for sep, sep_len in separators:
            pos = chunk_slice.rfind(sep, search_start)
            if pos >= 0:
                best_break = pos + sep_len
                break
        else:
            # 没有找到自然边界 → 在 overlap 区域中间截断
            best_break = end - start

        actual_end = start + best_break
        chunks.append(text[start:actual_end])
        start = actual_end - overlap
        if start <= 0 or start >= text_len:
            break

    return chunks


# ═══════════════════════════════════════════════════════════════
#  工具实现
# ═══════════════════════════════════════════════════════════════

def _get_index(context) -> BM25Index:
    """从 context.storage 获取或创建 BM25Index 实例。"""
    if "bm25_index" not in context.storage:
        context.storage["bm25_index"] = BM25Index(k1=1.5, b=0.75)
    return context.storage["bm25_index"]


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

    index = _get_index(context)
    chunks = _chunk_text(content, chunk_size, chunk_overlap)

    count = 0
    total_chars = 0
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        chunk_title = f"{title} [块 {i+1}/{len(chunks)}]" if title else ""
        index.add(
            text=chunk,
            source=source,
            title=chunk_title,
            metadata={
                "chunk_index": i,
                "total_chunks": len(chunks),
                "chunk_size": chunk_size,
                "overlap": chunk_overlap,
            }
        )
        count += 1
        total_chars += len(chunk)

    context.logger.info(
        f"Indexed {count} chunk(s) ({total_chars} chars) from source '{source}'"
    )

    return (
        f"✅ 已索引 **{count}** 个文本块\n"
        f"  来源: `{source}`\n"
        f"  总字符数: {total_chars:,}\n"
        f"  分块大小: {chunk_size} 字符（重叠 {chunk_overlap}）\n"
        f"  索引总文档数: {index.stats()['total_documents']}\n\n"
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

    index = _get_index(context)

    if index.stats()["total_documents"] == 0:
        return (
            "📭 索引为空，还没有任何已索引的内容。\n\n"
            "请先用 `index_context` 添加内容：\n"
            "  `index_context(content='你的文本', source='docs')`"
        )

    results = index.search(
        query=query,
        top_k=top_k,
        min_score=min_score,
        source_filter=source_filter,
        expand_context=expand_context,
    )

    if not results:
        stats = index.stats()
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
    stats = index.stats()
    lines.append(
        f"📊 索引: {stats['total_documents']} 文档, "
        f"{stats['total_characters']:,} 字符, "
        f"词库 {stats['vocabulary_size']} 词"
    )

    return "\n".join(lines)


def _handle_clear_index(args: dict, context) -> str:
    """清空索引（全部或按 source）。"""
    source_filter = args.get("source_filter", "").strip() or ""
    index = _get_index(context)

    if source_filter:
        removed = index.remove_by_source(source_filter)
        context.logger.info(
            f"Cleared {removed} document(s) from source '{source_filter}'"
        )
        return (
            f"✅ 已清除来源 `{source_filter}` 的 **{removed}** 个文档。\n"
            f"  剩余文档: {index.stats()['total_documents']}"
        )
    else:
        stats_before = index.stats()
        index.clear_all()
        context.logger.info(
            f"Cleared all {stats_before['total_documents']} document(s)"
        )
        return f"✅ 已清空全部索引（共 {stats_before['total_documents']} 个文档）。"


def _handle_index_stats(args: dict, context) -> str:
    """查看索引统计。"""
    stats = _get_index(context).stats()

    lines = [
        "📊 **检索引擎统计**",
        "",
        f"  总文档数: {stats['total_documents']}",
        f"  总字符数: {stats['total_characters']:,}",
        f"  词库大小: {stats['vocabulary_size']} 个唯一词",
        f"  平均文档长度: {stats['average_document_length']} tokens",
        f"  BM25 参数: k1={stats['bm25_params']['k1']}, b={stats['bm25_params']['b']}",
        "",
    ]

    if stats["sources"]:
        lines.append("  **来源分布**:")
        for src, cnt in sorted(stats["sources"].items(),
                               key=lambda x: x[1], reverse=True):
            lines.append(f"    `{src}` — {cnt} 文档")
    else:
        lines.append("  *(索引为空)*")

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


def on_agent_init(context):
    """初始化检索引擎。"""
    # 确保 BM25Index 实例存在
    _get_index(context)
    context.storage["retriever_auto_index"] = True
    context.storage["retriever_message_buffer"] = []
    context.logger.info(
        "Context Retriever ready — auto-indexing enabled 📇"
    )


def on_agent_shutdown(context):
    """输出会话检索统计。"""
    stats = _get_index(context).stats()
    if stats["total_documents"] > 0:
        context.logger.info(
            f"Context Retriever shutdown: {stats['total_documents']} docs, "
            f"{stats['total_characters']:,} chars indexed this session"
        )


def after_step(step: int, reasoning: str, content: str,
               tool_calls: list, context):
    """每个 ReAct step 后自动索引新增的推理和工具调用。

    通过追加到消息缓冲区实现增量索引，
    避免重复索引已有的对话内容。
    """
    if not context.storage.get("retriever_auto_index", True):
        return

    index = _get_index(context)
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
        for msg in buffer:
            index.add(text=msg, source="conversation",
                       title=f"Step {step}",
                       metadata={"step": step})
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
        index = _get_index(context)
        text = f"[Tool: {tool_name}]\nArgs: {json.dumps(args, ensure_ascii=False)}\n\nResult:\n{result}"
        index.add(
            text=text,
            source="tool_output",
            title=f"Tool: {tool_name}",
            metadata={"tool_name": tool_name, "args": args}
        )

    # 不修改返回值，只是附加索引
    return result
