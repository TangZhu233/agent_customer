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


# 全局单例，其他模块直接 from config.settings import settings 使用
settings = Settings()
