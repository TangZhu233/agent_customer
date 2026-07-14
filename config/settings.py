"""
从 .env 文件读取所有配置项，其他模块从这里统一获取配置。
"""
import os
from dotenv import load_dotenv

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


# 全局单例，其他模块直接 from config.settings import settings 使用
settings = Settings()
