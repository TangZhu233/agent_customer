"""
从 .env 文件读取所有配置项，其他模块从这里统一获取配置。
"""
import os
import sys
from dotenv import load_dotenv

# === 最早阶段：设置 HuggingFace 国内镜像 + SSL（必须在任何 ML 库导入前执行）===
# 面试知识点：
# - 为什么放在 settings.py 最前面？Python 模块是按导入顺序执行的。
#   settings.py 是整个项目第一个被导入的模块，在这里设置环境变量可以保证
#   后续所有 ML 库（sentence_transformers, huggingface_hub 等）都能读到正确配置。
# - 如果放在 rag.py 里，当 chromadb 或 langchain_chroma 先被导入时，
#   它们可能已经初始化了 huggingface_hub 的 httpx 客户端（带着默认配置），
#   后续再改环境变量就无效了。
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
os.environ['HF_HUB_DISABLE_SSL_VERIFY'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'  # 强制离线模式：模型已预下载到缓存，禁止联网验证
os.environ['CURL_CA_BUNDLE'] = ''

# 加载项目根目录下的 .env 文件
load_dotenv()


class Settings:
    """全局配置单例"""

    # --- DeepSeek API ---
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    # --- 数据库 ---
    # 数据库文件路径（相对于项目根目录）
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/customer.db")

    # --- 用户认证 ---
    # JWT 签名密钥（生产环境务必修改为强随机字符串）
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "agent-customer-dev-secret-change-in-production")
    JWT_EXPIRE_HOURS: int = int(os.getenv("JWT_EXPIRE_HOURS", "24"))

    # --- RAG / 向量检索 ---
    # 嵌入模型路径（本地路径，绕过 HuggingFace Hub 联网问题）
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "data/models/text2vec-base-chinese")
    # ChromaDB 持久化目录（相对于项目根目录）
    CHROMA_DB_PATH: str = os.getenv("CHROMA_DB_PATH", "data/chroma_db")
    # 向量检索返回数量
    VECTOR_SEARCH_K: int = int(os.getenv("VECTOR_SEARCH_K", "5"))

    # --- API 限流 ---
    # /chat 接口每分钟每用户最大请求数
    RATE_LIMIT_CHAT_PER_USER: str = os.getenv("RATE_LIMIT_CHAT_PER_USER", "30/minute")
    # /chat 接口每分钟每 IP 最大请求数
    RATE_LIMIT_CHAT_PER_IP: str = os.getenv("RATE_LIMIT_CHAT_PER_IP", "60/minute")

    # --- 对话历史 ---
    # 保留最近 N 条消息作为上下文（20 条约 10 轮对话）
    MAX_HISTORY_MESSAGES: int = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))
    # 字符数安全阈值：prompt 总字符数超过此值时自动从最早消息裁剪
    # 默认 80000 字符（中文字符 ÷2 ≈ token，约 40000 token，占 128K 窗口 30%，留足余量）
    MAX_HISTORY_CHAR_LIMIT: int = int(os.getenv("MAX_HISTORY_CHAR_LIMIT", "80000"))

    # --- 熔断器 (Circuit Breaker) ---
    # 是否启用熔断保护
    CB_ENABLED: bool = os.getenv("CB_ENABLED", "true").lower() == "true"
    # 连续失败次数阈值（触发熔断 CLOSED → OPEN）
    CB_FAILURE_THRESHOLD: int = int(os.getenv("CB_FAILURE_THRESHOLD", "5"))
    # 熔断后等待多少秒进入 HALF_OPEN 状态
    CB_RECOVERY_TIMEOUT: float = float(os.getenv("CB_RECOVERY_TIMEOUT", "30"))
    # HALF_OPEN 状态下允许的最大探测请求数
    CB_HALF_OPEN_MAX: int = int(os.getenv("CB_HALF_OPEN_MAX", "1"))
    # 单次 LLM 调用的超时时间（秒），应大于 tenacity 总重试时间（约 14s = 2+4+8）
    LLM_REQUEST_TIMEOUT: float = float(os.getenv("LLM_REQUEST_TIMEOUT", "30.0"))

    # --- Redis 语义缓存 ---
    # 是否启用 Redis 连接
    REDIS_ENABLED: bool = os.getenv("REDIS_ENABLED", "false").lower() == "true"
    # Redis 连接地址
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    # 缓存有效期（秒）
    REDIS_CACHE_TTL: int = int(os.getenv("REDIS_CACHE_TTL", "3600"))

    # --- 多路召回检索 ---
    # 检索模式：dense=仅向量(默认,向后兼容) | hybrid=向量+BM25融合
    RETRIEVAL_MODE: str = os.getenv("RETRIEVAL_MODE", "dense")
    # 是否启用 LLM 重排序
    RERANK_ENABLED: bool = os.getenv("RERANK_ENABLED", "false").lower() == "true"
    # RRF 融合常数 k（越大排名差异惩罚越小，学界标准 k=60）
    FUSION_K: int = int(os.getenv("FUSION_K", "60"))
    # 重排序候选文档数
    RERANK_TOP_K: int = int(os.getenv("RERANK_TOP_K", "5"))
    # 每条检索路径的召回数量（融合前拉取更多候选，融合后收敛至 VECTOR_SEARCH_K）
    MULTI_RECALL_K: int = int(os.getenv("MULTI_RECALL_K", "10"))


# 全局单例，其他模块直接 from config.settings import settings 使用
settings = Settings()
