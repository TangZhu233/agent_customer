"""
多路召回 + 重排序引擎 — 稠密向量（ChromaDB）+ 稀疏关键词（BM25）→ RRF 融合 → LLM 重排序。

面试知识点：
- 为什么单路向量检索不够？
  1. 精确关键词匹配差（货号"SKU-2026-001"可能被编码为无关向量）
  2. 中文同义词/缩写（"棉T" vs "纯棉T恤"）语义编码不稳定
  3. 短查询（3-5字）向量表示信息量不足

- 为什么 RRF 而不是加权分数融合？
  RRF（Reciprocal Rank Fusion）不需要分数归一化——稠密检索的余弦距离和
  BM25 的统计分数不在同一尺度，直接加权需要对两者分别做 min-max 归一化。
  RRF 只关心排名，天然免疫分数尺度差异。k=60 是学界标准参数。

- 为什么用 LLM 重排序而不是专用 cross-encoder 模型？
  28 篇文档的候选池通常 5-10 篇，DeepSeek API 调用约 500ms。
  cross-encoder 模型（如 bge-reranker-large）需要下载 1.3GB，需要 GPU
  才能达到合理速度。复用现有 API 零额外部署成本，且可独立开关。
"""
import time
from app.logger import get_logger

retrieval_log = get_logger("retrieval")

# 延迟导入——这些包可能未安装
_jieba_loaded = False


def _ensure_jieba():
    """延迟加载 jieba 分词器（首次调用 ~200ms 初始化词典）"""
    global _jieba_loaded
    if not _jieba_loaded:
        import jieba
        jieba.setLogLevel(20)  # 抑制 jieba 的 DEBUG 日志
        # 预热：首次 cut 最慢，之后快
        jieba.lcut("预热分词器")
        _jieba_loaded = True


def _tokenize(text: str) -> list[str]:
    """jieba 分词 + 去停用词"""
    _ensure_jieba()
    import jieba
    # 中文停用词
    stop_words = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
        "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
        "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
        "什么", "怎么", "如何", "为什么", "吗", "呢", "吧", "啊", "哦", "嗯",
    }
    import jieba
    return [w for w in jieba.lcut(text) if w.strip() and w not in stop_words]


class BM25Retriever:
    """BM25 关键词检索器——按 category 分桶建索引，支持预过滤。

    使用 rank_bm25 库（纯 Python Okapi BM25 实现） + jieba 中文分词。
    28 篇文档建索引 <10ms，搜索 <5ms。
    """

    def __init__(self):
        self._indexes: dict[str, "BM25Okapi"] = {}     # category → BM25Okapi
        self._docs_by_category: dict[str, list[dict]] = {}  # category → 文档列表
        self._all_docs: list[dict] = []                 # 全量文档（用于 category=None 的查询）
        self._all_index: "BM25Okapi | None" = None

    def build_index(self, documents: list[dict]):
        """从文档列表构建 BM25 索引。

        Args:
            documents: [{doc_id, title, content, category, gender}, ...]
        """
        from rank_bm25 import BM25Okapi

        self._all_docs = documents
        self._indexes = {}
        self._docs_by_category = {}

        # 全量索引
        all_tokens = [_tokenize(d["content"]) for d in documents]
        self._all_index = BM25Okapi(all_tokens) if all_tokens else None

        # 按 category 分桶索引
        for doc in documents:
            cat = doc.get("category", "通用")
            if cat not in self._docs_by_category:
                self._docs_by_category[cat] = []
            self._docs_by_category[cat].append(doc)

        for cat, docs in self._docs_by_category.items():
            tokenized = [_tokenize(d["content"]) for d in docs]
            self._indexes[cat] = BM25Okapi(tokenized) if tokenized else None

        retrieval_log.info(
            "BM25 索引构建完成: %d 篇文档, %d 个分类",
            len(documents), len(self._docs_by_category),
        )

    def search(self, query: str, k: int = 10, category: str | None = None) -> list[dict]:
        """BM25 关键词检索。

        Args:
            query: 用户查询文本
            k: 返回结果数
            category: 可选分类过滤（None 表示不过滤）

        Returns:
            [{doc_id, title, content, category, gender, score}, ...]
            score 为 BM25 分数，越高越相关。
        """
        tokens = _tokenize(query)
        if not tokens:
            return []

        # 选择索引
        if category and category in self._indexes and self._indexes[category]:
            index = self._indexes[category]
            docs = self._docs_by_category[category]
        elif self._all_index:
            index = self._all_index
            docs = self._all_docs
        else:
            return []

        # BM25 打分
        scores = index.get_scores(tokens)

        # 按分数降序排列，取 top-k
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:k]

        results = []
        for idx, score in ranked:
            if score <= 0:
                continue
            doc = docs[idx]
            results.append({
                "doc_id": doc["doc_id"],
                "title": doc["title"],
                "content": doc["content"],
                "category": doc.get("category", ""),
                "gender": doc.get("gender", "通用"),
                "score": round(float(score), 4),
            })

        return results


def reciprocal_rank_fusion(
    dense_results: list[dict],
    sparse_results: list[dict],
    k: int = 60,
) -> list[dict]:
    """RRF（Reciprocal Rank Fusion）融合两路检索结果。

    公式: RRFscore(d) = Σ 1/(k + rank_i(d))
    其中 rank_i(d) 是文档 d 在第 i 路检索中的排名（从 1 开始），
    k 是平滑常数（默认 60，来自原始论文）。

    两路结果去重后按 RRF 分数降序排列。
    """
    rrf_scores: dict[int, float] = {}    # doc_id → RRF score
    doc_map: dict[int, dict] = {}        # doc_id → 文档元数据

    # 稠密路排名
    for rank, doc in enumerate(dense_results, start=1):
        did = doc["doc_id"]
        rrf_scores[did] = rrf_scores.get(did, 0) + 1.0 / (k + rank)
        if did not in doc_map:
            doc_map[did] = doc

    # 稀疏路排名
    for rank, doc in enumerate(sparse_results, start=1):
        did = doc["doc_id"]
        rrf_scores[did] = rrf_scores.get(did, 0) + 1.0 / (k + rank)
        if did not in doc_map:
            doc_map[did] = doc

    # 按 RRF 分数降序排列
    fused = []
    for did, score in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True):
        doc = doc_map[did].copy()
        doc["score"] = round(score, 6)
        fused.append(doc)

    retrieval_log.info(
        "RRF 融合: dense=%d sparse=%d → fused=%d",
        len(dense_results), len(sparse_results), len(fused),
    )
    return fused


async def llm_rerank(
    query: str,
    candidates: list[dict],
    top_k: int = 5,
) -> list[dict]:
    """用 DeepSeek LLM 对候选文档重排序。

    原理：构造 prompt 列出候选文档的 ID + 摘要，让 LLM 按相关性排序。
    DeepSeek 返回排序后的 ID 列表，按此顺序重排文档并返回 top_k。

    如果 LLM 解析失败，返回原始顺序（不抛异常，降级处理）。
    """
    if len(candidates) <= top_k:
        return candidates

    from langchain_deepseek import ChatDeepSeek
    from config.settings import settings

    # 构建重排序 prompt
    doc_list = []
    for i, doc in enumerate(candidates, 1):
        snippet = doc["content"][:200].replace("\n", " ")
        doc_list.append(
            f"[{i}] 标题: {doc['title']} | 分类: {doc.get('category', '')} | "
            f"内容: {snippet}"
        )

    prompt = (
        f"用户问题：{query}\n\n"
        f"以下是检索到的文档列表，请按与用户问题的相关度从高到低排序。\n"
        f"只返回排序后的文档编号列表，格式如：3,1,5,2,4\n\n"
        + "\n".join(doc_list)
    )

    try:
        llm = ChatDeepSeek(
            model_name=settings.DEEPSEEK_MODEL,
            api_key=settings.DEEPSEEK_API_KEY,
            api_base=settings.DEEPSEEK_BASE_URL,
            temperature=0,  # 排序任务不需要创造性
        )
        response = await llm.ainvoke(prompt)
        content = response.content.strip()

        # 解析返回的编号列表
        import re
        numbers = re.findall(r'\d+', content)
        order = [int(n) - 1 for n in numbers if 0 < int(n) <= len(candidates)]

        if not order:
            retrieval_log.warning("LLM 重排序解析失败，返回原始顺序: %s", content[:100])
            return candidates[:top_k]

        reranked = [candidates[i] for i in order if i < len(candidates)]
        retrieval_log.info(
            "LLM 重排序: %d → %d 篇 (order=%s)",
            len(candidates), min(top_k, len(reranked)),
            [int(n) for n in numbers[:10]],
        )

        # 标记为重排序结果
        for doc in reranked:
            doc["_reranked"] = True

        return reranked[:top_k]

    except Exception as e:
        retrieval_log.warning("LLM 重排序失败，降级返回原始顺序: %s", e)
        return candidates[:top_k]


# ==================== 全局单例 ====================

_bm25_retriever: BM25Retriever | None = None


def get_bm25_retriever() -> BM25Retriever:
    """获取 BM25 检索器单例"""
    global _bm25_retriever
    if _bm25_retriever is None:
        _bm25_retriever = BM25Retriever()
    return _bm25_retriever


def invalidate_bm25_index():
    """强制下次 get_bm25_retriever() 调用时重建索引"""
    global _bm25_retriever
    _bm25_retriever = None
    retrieval_log.info("BM25 索引已失效，下次检索时将重建")


# ==================== 主入口 ====================

async def hybrid_search(
    query: str,
    k: int = 5,
    category: str | None = None,
    gender: str | None = None,
) -> list[dict]:
    """多路召回 + RRF 融合 + LLM 重排序（主入口）。

    流程：
    1. 稠密路：调用 rag._dense_search（现有 ChromaDB），拉取 MULTI_RECALL_K 篇
    2. 稀疏路：调用 BM25Retriever.search，拉取 MULTI_RECALL_K 篇
    3. RRF 融合：两路结果合并去重，按 RRF 分数排序
    4. LLM 重排序（可选）：对融合后 Top-N 用 DeepSeek 重排序
    5. 返回最终 Top-K

    Args:
        query: 用户查询文本
        k: 最终返回结果数
        category: 分类过滤
        gender: 性别过滤

    Returns:
        [{doc_id, title, content, category, gender, score}, ...]
    """
    from config.settings import settings
    from app.rag import _dense_search

    recall_k = settings.MULTI_RECALL_K
    start = time.perf_counter()

    # 第 1 路：稠密向量检索
    dense_results = _dense_search(query, k=recall_k, category=category, gender=gender)

    # 第 2 路：BM25 关键词检索
    bm25 = get_bm25_retriever()
    sparse_results = bm25.search(query, k=recall_k, category=category)

    # RRF 融合
    fused = reciprocal_rank_fusion(
        dense_results, sparse_results, k=settings.FUSION_K,
    )

    # LLM 重排序（可选）
    if settings.RERANK_ENABLED and len(fused) > k:
        rerank_top = max(k, settings.RERANK_TOP_K)
        final = await llm_rerank(query, fused[:rerank_top], top_k=k)
    else:
        final = fused[:k]

    elapsed = round((time.perf_counter() - start) * 1000, 2)
    retrieval_log.info(
        "混合检索完成: query='%s' dense=%d sparse=%d fused=%d final=%d %.2fms",
        query[:50], len(dense_results), len(sparse_results),
        len(fused), len(final), elapsed,
    )

    return final
