"""
Redis 缓存管理器 —— 缓存 RAG 检索结果（非 LLM 最终回复）。

面试知识点：
- 为什么缓存 RAG 检索结果而不是 LLM 回复？
  ReAct Agent 的回复依赖对话历史、用户身份、工具调用结果，不是纯函数。
  同一句 "T-SU-WH-L 是什么" 在 A 的会话（前一条问了 SKU-2026-001）和
  B 的会话（独立提问）里，Agent 会给出不同措辞。缓存最终回复 = 上下文泄露。
  RAG 检索层是纯函数：同样的 (query, k, category, gender, mode) 永远返回
  同样的文档列表，与用户是谁、之前聊了什么完全无关 → 正确可缓存。

- Key 设计为什么是 MD5(query + category + gender + k + mode)？
  精确匹配：同样的检索参数 → 同样的结果。语义向量匹配留给 P3，
  需要额外的向量索引和阈值调优。"Make it work, make it right"——先做最简单正确的。

- 为什么所有 Redis 操作都 try/except 包裹？
  企业级 API 的黄金法则：缓存永远不能成为故障点。
  Redis 挂了？记录日志，继续查 ChromaDB。Redis 超时？同上。
  缓存是锦上添花，不是基本功能。
"""
import json
import hashlib
from app.logger import get_logger

cache_log = get_logger("cache")

# 尝试导入 redis，如果未安装则优雅降级
try:
    import redis.asyncio as aioredis
    _redis_available = True
except ImportError:
    aioredis = None
    _redis_available = False


class RedisCache:
    """Redis 缓存管理器 —— 连接池 + 优雅降级。

    所有操作都是非阻塞的 async，失败不抛异常只记日志。
    设计为 FastAPI lifespan 事件驱动的单例：startup 创建连接，shutdown 释放。
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        default_ttl: int = 3600,
        enabled: bool = True,
    ):
        self.redis_url = redis_url
        self.default_ttl = default_ttl
        self.enabled = enabled and _redis_available
        self._client: "aioredis.Redis | None" = None
        self._available = False

    @property
    def is_available(self) -> bool:
        return self._available and self.enabled

    async def connect(self) -> bool:
        """创建连接池并测试连通性。

        在 FastAPI startup 事件中调用。连接失败不阻止应用启动。
        返回 True 表示连接成功，False 表示已降级。
        """
        if not self.enabled:
            cache_log.info("Redis 缓存未启用（REDIS_ENABLED=false 或 redis 库未安装）")
            return False

        try:
            # 使用连接池（而非直连），支持自动重连和健康检查
            pool = aioredis.ConnectionPool.from_url(
                self.redis_url,
                max_connections=10,
                socket_connect_timeout=3,
                socket_timeout=2,
                socket_keepalive=True,
                health_check_interval=30,
                retry_on_timeout=True,
                protocol=2,  # Redis 5.x Windows 版不支持 RESP3，强制 RESP2
            )
            self._client = aioredis.Redis(
                connection_pool=pool,
                decode_responses=True,
                protocol=2,
            )
            await self._client.ping()
            self._available = True
            cache_log.info("Redis 连接成功: %s", self.redis_url)
            return True
        except Exception as e:
            cache_log.warning("Redis 不可用，缓存已禁用: %s", e)
            self._available = False
            self._client = None
            return False

    async def disconnect(self):
        """关闭连接。在 FastAPI shutdown 事件中调用。"""
        if self._client:
            try:
                await self._client.close()
                cache_log.info("Redis 连接已关闭")
            except Exception:
                pass
            self._client = None
        self._available = False

    @staticmethod
    def _normalize_query(query: str) -> str:
        """归一化查询文本以保证缓存键稳定。

        LLM 调用 search_knowledge_base 时会对用户原始问题重新措辞，
        同一个问题可能产生 "纯棉T恤洗涤" / "纯棉T恤 洗涤" / "纯棉 T 恤 洗涤"
        等变体。中文无词间空格，去空格 + 小写后可消除这些无意义差异。
        """
        import re
        q = query.strip().lower()
        q = re.sub(r'\s+', '', q)           # 去所有空白
        q = re.sub(r'[，。！？、；：""''（）—…《》]', '', q)  # 去中文标点
        return q

    def _cache_key(
        self,
        query: str,
        category: str | None = None,
        gender: str | None = None,
        k: int = 5,
    ) -> str:
        """生成缓存键：MD5(normalized_query + category + gender + k + retrieval_mode)。

        RAG 检索结果是确定性函数——同样的检索参数永远返回同样的文档列表。
        包含 retrieval_mode 是因为 dense/hybrid 对同一查询返回不同排序。
        归一化 query 是因为 LLM 可能对同一问题产生不同措辞变体。
        """
        from config.settings import settings
        nq = self._normalize_query(query)
        raw = (
            f"{nq}|cat={category or 'all'}|gen={gender or 'all'}"
            f"|k={k}|mode={settings.RETRIEVAL_MODE}"
        )
        return f"rag:cache:{hashlib.md5(raw.encode()).hexdigest()[:16]}"

    async def get(
        self,
        query: str,
        category: str | None = None,
        gender: str | None = None,
        k: int = 5,
    ) -> list[dict] | None:
        """查询 RAG 缓存。命中返回文档列表，未命中返回 None。"""
        if not self.is_available:
            return None
        try:
            key = self._cache_key(query, category, gender, k)
            data = await self._client.get(key)
            if data:
                cache_log.info("RAG 缓存命中: %s", key)
                return json.loads(data)
            cache_log.info("RAG 缓存未命中: query='%s'", query[:40])
        except Exception as e:
            cache_log.warning("Redis GET 异常: %s", e)
        return None

    async def set(
        self,
        query: str,
        results: list[dict],
        category: str | None = None,
        gender: str | None = None,
        k: int = 5,
        ttl: int | None = None,
    ):
        """写入 RAG 缓存。失败静默忽略。"""
        if not self.is_available:
            return
        try:
            key = self._cache_key(query, category, gender, k)
            await self._client.setex(
                key,
                ttl or self.default_ttl,
                json.dumps(results, ensure_ascii=False),
            )
            cache_log.info("RAG 缓存写入: %s (%ds TTL, %d 条文档)", key, ttl or self.default_ttl, len(results))
        except Exception as e:
            cache_log.warning("Redis SET 异常: %s", e)

    async def invalidate(self, pattern: str = "rag:cache:*") -> int:
        """批量失效缓存（知识库更新时调用）。返回删除的 key 数量。"""
        if not self.is_available:
            return 0
        try:
            keys = []
            async for key in self._client.scan_iter(match=pattern, count=100):
                keys.append(key)
            if keys:
                deleted = await self._client.delete(*keys)
                cache_log.info("Redis 缓存失效: %d 条 (pattern=%s)", deleted, pattern)
                return deleted
        except Exception as e:
            cache_log.warning("Redis SCAN/DEL 异常: %s", e)
        return 0


# ==================== 全局单例 ====================

_redis_cache: RedisCache | None = None


def get_redis_cache() -> RedisCache:
    """获取 Redis 缓存单例。首次调用时从 settings 读取配置创建。"""
    global _redis_cache
    if _redis_cache is None:
        from config.settings import settings
        _redis_cache = RedisCache(
            redis_url=settings.REDIS_URL,
            default_ttl=settings.REDIS_CACHE_TTL,
            enabled=settings.REDIS_ENABLED,
        )
    return _redis_cache


async def init_cache() -> bool:
    """初始化缓存（FastAPI startup 调用）。"""
    cache = get_redis_cache()
    return await cache.connect()


async def close_cache():
    """关闭缓存（FastAPI shutdown 调用）。"""
    global _redis_cache
    if _redis_cache is not None:
        await _redis_cache.disconnect()
        _redis_cache = None
