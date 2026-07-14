"""
Pydantic 数据模型：定义 API 请求/响应的数据结构。
"""
from pydantic import BaseModel


# --- 请求模型 ---
class ChatRequest(BaseModel):
    """用户发来的聊天消息"""
    message: str
    user_id: int | None = None  # 可选，已登录用户传自己的ID


# --- 响应模型 ---
class ChatResponse(BaseModel):
    """Agent 返回的聊天回复"""
    reply: str
