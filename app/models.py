"""
Pydantic 数据模型：定义 API 请求/响应的数据结构。
"""
from pydantic import BaseModel, Field


# ==================== 聊天（原有 + 增强）====================

class ChatRequest(BaseModel):
    """用户发来的聊天消息"""
    message: str
    user_id: int | None = None           # 可选，已登录用户传自己的ID（向后兼容）
    session_id: int | None = None        # 新增：会话ID，用于多轮对话
    enable_rag: bool = True              # 新增：是否启用知识库检索


class Citation(BaseModel):
    """知识库引用片段"""
    title: str
    snippet: str
    category: str


class ChatResponse(BaseModel):
    """Agent 返回的聊天回复"""
    reply: str
    citations: list[Citation] | None = None  # 新增：知识库引用列表


# ==================== 认证 ====================

class RegisterRequest(BaseModel):
    """用户注册请求"""
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)


class LoginRequest(BaseModel):
    """用户登录请求"""
    username: str
    password: str


class LoginResponse(BaseModel):
    """登录成功响应"""
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str
    is_admin: bool


class ChangePasswordRequest(BaseModel):
    """修改密码请求"""
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=100)


class UserInfo(BaseModel):
    """当前用户信息"""
    user_id: int
    username: str
    is_admin: bool


# ==================== 会话 ====================

class SessionCreateRequest(BaseModel):
    """创建会话请求"""
    title: str | None = None


class SessionCreateResponse(BaseModel):
    """创建会话响应"""
    id: int
    session_title: str
    created_at: str


class SessionListItem(BaseModel):
    """会话列表项"""
    id: int
    session_title: str
    created_at: str
    updated_at: str
    message_count: int


class SessionMessage(BaseModel):
    """会话消息"""
    id: int
    role: str
    content: str
    citations: list[Citation] | None = None
    created_at: str


# ==================== 知识库管理 ====================

class DocumentCreate(BaseModel):
    """创建文档请求"""
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    category: str = Field(default="通用", max_length=50)
    gender: str = Field(default="通用", max_length=10)


class DocumentUpdate(BaseModel):
    """更新文档请求（全部可选）"""
    title: str | None = Field(None, max_length=200)
    content: str | None = None
    category: str | None = Field(None, max_length=50)
    gender: str | None = Field(None, max_length=10)


class DocumentResponse(BaseModel):
    """文档响应"""
    id: int
    title: str
    content: str
    category: str
    gender: str
    created_at: str
    updated_at: str


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    total: int
    page: int
    items: list[DocumentResponse]
