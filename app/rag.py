"""
RAG 管道模块：向量嵌入 + ChromaDB 存储 + 相似检索 + 知识库同步。

面试知识点：
- 为什么选 ChromaDB 而不是 FAISS/Pinecone？
  ChromaDB 是嵌入式向量数据库（纯 Python，无需额外服务部署），支持元数据过滤，
  适合中小规模（<100万条）本地部署场景。FAISS 是纯向量索引库不支持元数据过滤
  和持久化；Pinecone/Milvus 是分布式向量数据库，适合大规模生产环境但需要独立部署。
  本项目的服装知识库规模（几百到几千条文档）用 ChromaDB 嵌入式模式最合适。

- 为什么选 text2vec-base-chinese 而不是 OpenAI Embeddings？
  ① 本地运行零 API 成本  ② 中文优化（用中文语料训练）  ③ 数据不出服务器，隐私安全
  缺点：需要下载约 400MB 模型文件，首次加载慢。对于纯中文场景，效果不输 OpenAI。

- 为什么用单例模式加载 Embedding 模型？
  模型加载是重量级操作（加载权重、构建词汇表），每次请求都重新 load 会导致
  内存爆炸（多次加载模型副本）和响应延迟（每次加载 2-5 秒）。单例 + 懒加载是
  机器学习模型在 Web 服务中的标准实践。
"""
import threading
import os
import ssl

# （HuggingFace 镜像 + SSL 配置已移至 config/settings.py 最早阶段设置）

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from config.settings import settings
from app.logger import get_logger

rag_log = get_logger("rag")

# 单例
_embeddings: HuggingFaceEmbeddings | None = None
_vectorstore: Chroma | None = None
_write_lock = threading.Lock()  # ChromaDB SQLite 底层非线程安全写入保护


def _get_embeddings() -> HuggingFaceEmbeddings:
    """懒加载嵌入模型（单例）"""
    global _embeddings
    if _embeddings is None:
        rag_log.info("加载嵌入模型: %s", settings.EMBEDDING_MODEL)
        _embeddings = HuggingFaceEmbeddings(
            model_name=settings.EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        rag_log.info("嵌入模型加载完成")
    return _embeddings


def _get_vectorstore() -> Chroma:
    """获取或创建 ChromaDB 向量存储（单例）"""
    global _vectorstore
    if _vectorstore is None:
        os.makedirs(settings.CHROMA_DB_PATH, exist_ok=True)
        embeddings = _get_embeddings()
        _vectorstore = Chroma(
            collection_name="clothing_knowledge",
            embedding_function=embeddings,
            persist_directory=settings.CHROMA_DB_PATH,
        )
        rag_log.info("ChromaDB 向量存储已初始化: %s", settings.CHROMA_DB_PATH)
    return _vectorstore


# ==================== 文档同步操作 ====================

def add_document(doc_id: int, title: str, content: str, category: str, gender: str = "通用") -> None:
    """将文档嵌入后添加到 ChromaDB"""
    global _vectorstore
    with _write_lock:
        vs = _get_vectorstore()
        vs.add_texts(
            texts=[content],
            metadatas=[{
                "doc_id": str(doc_id),
                "title": title,
                "category": category,
                "gender": gender,
            }],
            ids=[f"doc_{doc_id}"],
        )
        # 写入后重置单例，确保下次检索从磁盘读取最新数据
        _vectorstore = None
    rag_log.info("向量已添加: doc_id=%d title=%s category=%s gender=%s", doc_id, title, category, gender)


def update_document(doc_id: int, title: str, content: str, category: str, gender: str = "通用") -> None:
    """更新文档：先删除旧向量，再添加新向量"""
    global _vectorstore
    with _write_lock:
        vs = _get_vectorstore()
        chroma_id = f"doc_{doc_id}"
        # 删除旧向量（如果存在）
        try:
            vs.delete(ids=[chroma_id])
        except Exception:
            pass  # 首次添加时可能不存在
        # 添加新向量
        vs.add_texts(
            texts=[content],
            metadatas=[{
                "doc_id": str(doc_id),
                "title": title,
                "category": category,
                "gender": gender,
            }],
            ids=[chroma_id],
        )
        # 写入后重置单例，确保下次检索从磁盘读取最新数据
        _vectorstore = None
    rag_log.info("向量已更新: doc_id=%d title=%s gender=%s", doc_id, title, gender)


def delete_document(doc_id: int) -> None:
    """从 ChromaDB 删除文档向量"""
    global _vectorstore
    with _write_lock:
        vs = _get_vectorstore()
        try:
            vs.delete(ids=[f"doc_{doc_id}"])
            # 写入后重置单例，确保下次检索从磁盘读取最新数据
            _vectorstore = None
            rag_log.info("向量已删除: doc_id=%d", doc_id)
        except Exception as e:
            rag_log.warning("删除向量失败: doc_id=%d error=%s", doc_id, e)


# ==================== 检索操作 ====================

def _dense_search(query: str, k: int | None = None, category: str | None = None, gender: str | None = None) -> list[dict]:
    """
    向量相似检索。

    参数:
        query: 用户查询文本
        k: 返回结果数（默认取配置）
        category: 可选，按分类过滤（如 "尺码指南"）
        gender: 可选，按性别过滤（"男"/"女"/"通用"/"儿童"）

    返回:
        [{doc_id, title, content, category, gender, score}, ...]
        score 为距离值，越小越相似（0 = 完全匹配）

    面试知识点：
    - similarity_search_with_score 返回 (Document, score) 元组
    - score 取决于距离函数：ChromaDB 默认用 L2 距离或余弦距离
      sentence-transformers 的 normalize_embeddings=True 会将向量归一化，
      此时余弦相似度 = 1 - score（即 score 越小越相似）
    - filter 参数利用 ChromaDB 的元数据过滤能力，在检索阶段直接
      缩小候选集，比检索后再手动过滤效率高得多（减少无效计算）
    """
    import time
    k = k or settings.VECTOR_SEARCH_K
    vs = _get_vectorstore()

    # 构建组合过滤条件
    where_filter = None
    conditions = []
    if category:
        conditions.append({"category": category})
    if gender:
        conditions.append({"gender": gender})

    if len(conditions) == 1:
        where_filter = conditions[0]
    elif len(conditions) > 1:
        where_filter = {"$and": conditions}

    start = time.perf_counter()
    try:
        results = vs.similarity_search_with_score(query, k=k, filter=where_filter)
    except Exception as e:
        # 如果组合过滤失败（如旧数据无 gender 字段），降级为只按 category 过滤
        if where_filter and gender:
            rag_log.warning("组合过滤失败，降级为仅 category 过滤: %s", e)
            fallback_filter = {"category": category} if category else None
            try:
                results = vs.similarity_search_with_score(query, k=k, filter=fallback_filter)
            except Exception as e2:
                rag_log.error("降级检索也失败: %s", e2)
                return []
        else:
            rag_log.error("向量检索异常: %s", e)
            return []

    # 如果 gender 过滤返回空结果，降级去除 gender 过滤后重试
    # （旧 ChromaDB 数据可能没有 gender 元数据字段，导致过滤条件匹配不到任何文档）
    if len(results) == 0 and gender and where_filter:
        fallback_filter = None
        single_condition = None
        if category:
            single_condition = {"category": category}
        if where_filter != single_condition:  # 确实有 gender 过滤在起作用
            rag_log.warning(
                "gender 过滤返回空结果，降级去除 gender 重试: gender=%s category=%s",
                gender, category or "全部",
            )
            fallback_filter = single_condition
            try:
                results = vs.similarity_search_with_score(query, k=k, filter=fallback_filter)
            except Exception as e2:
                rag_log.error("降级检索异常: %s", e2)

    elapsed = round((time.perf_counter() - start) * 1000, 2)

    docs = []
    for doc, score in results:
        docs.append({
            "doc_id": int(doc.metadata.get("doc_id", 0)),
            "title": doc.metadata.get("title", ""),
            "content": doc.page_content,
            "category": doc.metadata.get("category", ""),
            "gender": doc.metadata.get("gender", "通用"),
            "score": round(score, 4),
        })

    rag_log.info(
        "向量检索: query='%s' category=%s gender=%s k=%d → %d条结果 %.2fms",
        query[:50], category or "全部", gender or "全部", k, len(docs), elapsed,
    )
    return docs


async def search_similar(query: str, k: int | None = None, category: str | None = None, gender: str | None = None) -> list[dict]:
    """
    向量相似检索（统一入口）。

    根据 RETRIEVAL_MODE 配置自动路由：
    - dense: 原始单路稠密检索（ChromaDB only，向后兼容）
    - hybrid: 多路召回 + RRF 融合 + 可选 LLM 重排序

    参数和返回值格式与 _dense_search 保持一致。

    缓存策略：RAG 检索结果是确定性函数（同样的检索参数 → 同样的文档列表），
    与用户身份、对话上下文无关，因此在此层做 Redis 缓存是正确的。
    KB 更新时通过 _invalidate_caches_and_reindex 批量失效。
    """
    import asyncio
    from config.settings import settings

    k = k or settings.VECTOR_SEARCH_K

    # 1. 查缓存
    if settings.REDIS_ENABLED:
        from app.cache import get_redis_cache
        cache = get_redis_cache()
        if cache.is_available:
            cached = await cache.get(query, category=category, gender=gender, k=k)
            if cached is not None:
                return cached

    # 2. 执行检索
    if settings.RETRIEVAL_MODE == "hybrid":
        from app.retrieval import hybrid_search
        results = await hybrid_search(query, k=k, category=category, gender=gender)
    else:
        # 默认 dense 模式（向后兼容）
        results = _dense_search(query, k=k, category=category, gender=gender)

    # 3. 写缓存（fire-and-forget，不阻塞返回）
    if settings.REDIS_ENABLED and results:
        from app.cache import get_redis_cache
        cache = get_redis_cache()
        if cache.is_available:
            asyncio.create_task(
                cache.set(query, results, category=category, gender=gender, k=k)
            )

    return results


def rebuild_bm25_index():
    """从 SQLite 重建 BM25 稀疏索引。在知识库变更后调用。"""
    from app.database import get_all_documents
    from app.retrieval import get_bm25_retriever

    docs = get_all_documents()
    if docs:
        bm25 = get_bm25_retriever()
        doc_dicts = [
            {
                "doc_id": d["id"],
                "title": d["title"],
                "content": d["content"],
                "category": d.get("category", "通用"),
                "gender": d.get("gender", "通用"),
            }
            for d in docs
        ]
        bm25.build_index(doc_dicts)
    else:
        rag_log.info("知识库为空，跳过 BM25 索引构建")


# ==================== 知识库初始化 ====================

def seed_knowledge_base() -> int:
    """
    将种子数据写入 SQLite + ChromaDB（幂等：已有数据则跳过）。

    返回写入的文档数量。
    """
    from app.database import create_document, get_all_documents
    from app.kb_seed_data import DEFAULT_DOCUMENTS

    # 检查是否已有数据
    existing = get_all_documents()
    if existing:
        rag_log.info("知识库已有 %d 条文档，跳过种子初始化", len(existing))
        return 0

    count = 0
    for doc in DEFAULT_DOCUMENTS:
        gender = doc.get("gender", "通用")
        doc_id = create_document(doc["title"], doc["content"], doc["category"], gender)
        add_document(doc_id, doc["title"], doc["content"], doc["category"], gender)
        count += 1

    rag_log.info("知识库种子初始化完成: %d 条文档", count)
    return count


def reindex_knowledge_base() -> int:
    """
    从 SQLite 重建整个 ChromaDB 向量索引（保留 SQLite 数据不变）。

    用途：
    - 修复旧向量缺失 gender 等新增元数据字段的问题
    - 向量损坏或数据不一致时的修复工具

    返回重建的文档数量。
    """
    global _vectorstore
    from app.database import get_all_documents

    docs = get_all_documents()
    if not docs:
        rag_log.info("SQLite 中无文档，跳过重建")
        return 0

    with _write_lock:
        vs = _get_vectorstore()
        # 获取所有已有向量 ID 并删除
        try:
            existing_ids = vs.get()["ids"]
            if existing_ids:
                vs.delete(ids=existing_ids)
                rag_log.info("已删除 %d 条旧向量", len(existing_ids))
        except Exception as e:
            rag_log.warning("删除旧向量时出错（可能本来为空）: %s", e)

        # 从 SQLite 重新索引所有文档
        count = 0
        for doc in docs:
            gender = doc.get("gender", "通用")
            vs.add_texts(
                texts=[doc["content"]],
                metadatas=[{
                    "doc_id": str(doc["id"]),
                    "title": doc["title"],
                    "category": doc["category"],
                    "gender": gender,
                }],
                ids=[f"doc_{doc['id']}"],
            )
            count += 1

        # 重置单例，确保下次检索从磁盘读取最新数据
        _vectorstore = None

    rag_log.info("向量索引重建完成: %d 条文档", count)
    return count
