"""
FastAPI 入口：启动 HTTP 服务，提供完整的服装电商智能客服系统。
包含：认证 / 聊天(RAG) / 会话管理 / 知识库管理 / 限流 / 全局异常处理。

启动命令：uvicorn app.main:app --reload
"""
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse
from app.models import (
    ChatRequest, ChatResponse,
    RegisterRequest, LoginRequest, LoginResponse,
    ChangePasswordRequest, UserInfo,
    SessionCreateRequest, SessionCreateResponse,
    SessionListItem, SessionMessage,
    DocumentCreate, DocumentUpdate, DocumentResponse, DocumentListResponse,
    Citation,
)
from app.agent import chat as agent_chat
from app.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, get_optional_user, require_admin,
)
from app.middleware import register_middleware as setup_middleware
from app.logger import get_logger

app_log = get_logger("app")
auth_log = get_logger("auth")

app = FastAPI(title="服装电商智能客服", version="0.2.0")

# 注册中间件栈（全局异常捕获 + 请求计时 + 限流）
setup_middleware(app)


# ==================== 启动事件 ====================

@app.on_event("startup")
async def startup():
    from app.database import migrate_database
    migrate_database()
    app_log.info("服装电商智能客服 v0.2.1 启动")


# ==================== 认证路由 ====================

@app.post("/auth/register")
async def register(req: RegisterRequest):
    """用户注册"""
    from app.database import create_auth_user, get_auth_user_by_username

    if get_auth_user_by_username(req.username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    user_id = create_auth_user(req.username, hash_password(req.password), is_admin=0)
    auth_log.info("新用户注册: id=%d username=%s", user_id, req.username)
    return {"id": user_id, "username": req.username, "message": "注册成功"}


@app.post("/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """用户登录，返回 JWT 令牌"""
    from app.database import get_auth_user_by_username

    user = get_auth_user_by_username(req.username)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user["id"], user["username"], bool(user["is_admin"]))
    auth_log.info("用户登录: id=%d username=%s", user["id"], user["username"])
    return LoginResponse(
        access_token=token,
        user_id=user["id"],
        username=user["username"],
        is_admin=bool(user["is_admin"]),
    )


@app.post("/auth/change-password")
async def change_password(req: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    """修改密码（需提供旧密码验证）"""
    from app.database import get_auth_user_by_id, update_password

    db_user = get_auth_user_by_id(user["user_id"])
    if not db_user or not verify_password(req.old_password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="旧密码错误")
    update_password(user["user_id"], hash_password(req.new_password))
    auth_log.info("密码修改: user_id=%d", user["user_id"])
    return {"message": "密码修改成功"}


@app.get("/auth/me", response_model=UserInfo)
async def me(user: dict = Depends(get_current_user)):
    """获取当前登录用户信息"""
    return UserInfo(user_id=user["user_id"], username=user["username"], is_admin=user["is_admin"])


# ==================== 会话路由 ====================

@app.get("/sessions", response_model=list[SessionListItem])
async def list_sessions(user: dict = Depends(get_current_user)):
    """获取当前用户的所有会话列表"""
    from app.database import get_user_sessions
    sessions = get_user_sessions(user["user_id"])
    return [SessionListItem(**s) for s in sessions]


@app.post("/sessions", response_model=SessionCreateResponse)
async def create_session(req: SessionCreateRequest = None, user: dict = Depends(get_current_user)):
    """创建新会话"""
    from app.database import create_session as db_create_session
    title = req.title if req and req.title else "新对话"
    session_id = db_create_session(user["user_id"], title)
    return SessionCreateResponse(
        id=session_id,
        session_title=title,
        created_at="",  # 前端不依赖此字段
    )


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: int, user: dict = Depends(get_current_user)):
    """删除会话及其所有消息"""
    from app.database import delete_session as db_delete_session

    if not db_delete_session(session_id, user["user_id"]):
        raise HTTPException(status_code=404, detail="会话不存在或无权操作")
    app_log.info("会话已删除: id=%d user=%d", session_id, user["user_id"])
    return {"message": "会话已删除"}


@app.get("/sessions/{session_id}/messages", response_model=list[SessionMessage])
async def get_session_messages(session_id: int, user: dict = Depends(get_current_user)):
    """获取会话历史消息（含引用）"""
    from app.database import get_session_by_id, get_session_messages as db_get_messages

    session = get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if session["user_id"] != user["user_id"]:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    messages = db_get_messages(session_id, user["user_id"])
    result = []
    for m in messages:
        citations = None
        if m.get("citations"):
            citations = [Citation(**c) for c in m["citations"]]
        result.append(SessionMessage(
            id=m["id"],
            role=m["role"],
            content=m["content"],
            citations=citations,
            created_at=m["created_at"],
        ))
    return result


# ==================== 知识库管理路由（管理员专用）====================

@app.get("/admin/kb/documents", response_model=DocumentListResponse)
async def admin_list_documents(
    category: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: dict = Depends(require_admin),
):
    """管理员：分页查询知识库文档"""
    from app.database import list_documents
    items, total = list_documents(category=category, page=page, page_size=page_size)
    return DocumentListResponse(
        total=total,
        page=page,
        items=[DocumentResponse(**d) for d in items],
    )


@app.post("/admin/kb/documents")
async def admin_create_document(
    req: DocumentCreate,
    user: dict = Depends(require_admin),
):
    """管理员：添加知识库文档并同步到向量库"""
    from app.database import create_document
    from app.rag import add_document as rag_add

    doc_id = create_document(req.title, req.content, req.category, req.gender)
    rag_add(doc_id, req.title, req.content, req.category, req.gender)
    app_log.info("管理员添加文档: id=%d title=%s gender=%s", doc_id, req.title, req.gender)
    return {"id": doc_id, "message": "文档添加成功"}


@app.put("/admin/kb/documents/{doc_id}")
async def admin_update_document(
    doc_id: int,
    req: DocumentUpdate,
    user: dict = Depends(require_admin),
):
    """管理员：更新知识库文档并重新同步向量"""
    from app.database import get_document_by_id, update_document
    from app.rag import update_document as rag_update

    doc = get_document_by_id(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    update_document(doc_id, req.title, req.content, req.category, req.gender)
    # 获取更新后的文档用于重新嵌入
    updated = get_document_by_id(doc_id)
    rag_update(doc_id, updated["title"], updated["content"], updated["category"], updated.get("gender", "通用"))
    app_log.info("管理员更新文档: id=%d", doc_id)
    return {"message": "文档更新成功"}


@app.delete("/admin/kb/documents/{doc_id}")
async def admin_delete_document(
    doc_id: int,
    user: dict = Depends(require_admin),
):
    """管理员：删除知识库文档并从向量库移除"""
    from app.database import delete_document
    from app.rag import delete_document as rag_delete

    if not delete_document(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    rag_delete(doc_id)
    app_log.info("管理员删除文档: id=%d", doc_id)
    return {"message": "文档删除成功"}


@app.post("/admin/kb/init")
async def admin_init_kb(user: dict = Depends(require_admin)):
    """管理员：初始化知识库种子数据（幂等）"""
    from app.rag import seed_knowledge_base
    count = seed_knowledge_base()
    return {"message": "知识库初始化完成" if count else "知识库已有数据，跳过初始化", "count": count}


@app.get("/admin/kb/categories")
async def admin_get_categories(user: dict = Depends(require_admin)):
    """管理员：获取所有文档分类"""
    from app.database import get_categories
    return get_categories()


# ==================== 聊天路由 ====================

@app.post("/chat", response_model=ChatResponse)
async def chat_api(req: ChatRequest, user: dict | None = Depends(get_optional_user)):
    """
    对话接口（向后兼容：未登录也可使用基础功能）。

    面试知识点：
    - Depends(get_optional_user) 实现了"可选认证"模式。
      与 Depends(get_current_user) 不同，它不抛出 401 而是返回 None，
      让同一个端点同时支持登录用户（会话持久化 + RAG）和匿名用户（基础查询）。
    - 这是 FastAPI 依赖注入的一个优雅用法：依赖函数返回 None 在框架中
      完全合法，路由函数据此切换行为。
    """
    user_id = user["user_id"] if user else None

    # 调用 Agent（带 RAG 引用）
    reply, citations = await agent_chat(
        message=req.message,
        user_id=user_id or req.user_id,
        session_id=req.session_id,
        enable_rag=req.enable_rag,
    )

    # 持久化消息（仅登录用户 + 有会话时）
    if user_id and req.session_id:
        import json
        from app.database import save_message, get_session_by_id

        session = get_session_by_id(req.session_id)
        if session and session["user_id"] == user_id:
            save_message(req.session_id, user_id, "user", req.message)
            citations_json = json.dumps(citations, ensure_ascii=False) if citations else None
            save_message(req.session_id, user_id, "assistant", reply, citations_json)

    # 构建响应
    citations_out = None
    if citations:
        citations_out = [Citation(**c) for c in citations]

    return ChatResponse(reply=reply, citations=citations_out)


# ==================== 聊天页面（多页面 SPA）====================

@app.get("/", response_class=HTMLResponse)
def chat_page():
    """
    多页面前端：登录/注册 → 聊天(侧栏+引用) → 管理后台。
    单 HTML 文件，CSS display 切换页面，Vanilla JS，无前端框架依赖。

    面试知识点：
    - 为什么不用 React/Vue？对于管理后台类应用，如果交互复杂度不高
      （表单 + 列表 + 切换），Vanilla JS 完全可以胜任，且首次加载快、
      无编译步骤、浏览器原生支持。当页面交互复杂到需要组件化、状态管理时
      才需要前端框架。这体现了"按需选型"的工程思维。
    """
    return HTMLResponse(content=CHAT_PAGE)


# ==================== 健康检查 ====================

@app.get("/health")
def health():
    """健康检查"""
    return {"status": "ok"}


# ==================== 内联 HTML 前端 ====================

CHAT_PAGE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>服装电商智能客服</title>
    <style>
        :root {
            --primary: #4f6ef7;
            --primary-light: #6c8cff;
            --bg: #f0f2f5;
            --white: #fff;
            --text: #333;
            --text-light: #999;
            --border: #e8e8e8;
            --danger: #e74c3c;
            --success: #27ae60;
            --cite-bg: #fef9e7;
            --sidebar-w: 260px;
            --header-h: 56px;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", sans-serif;
            background: var(--bg);
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }

        /* ======== 通用 ======== */
        .page { width: 100%; height: 100vh; display: none; }
        .page.active { display: flex; }
        .btn {
            padding: 10px 24px; border: none; border-radius: 8px; font-size: 14px;
            cursor: pointer; transition: all .2s; font-weight: 500;
        }
        .btn-primary { background: var(--primary); color: #fff; }
        .btn-primary:hover { background: #3d5ce5; }
        .btn-danger { background: var(--danger); color: #fff; }
        .btn-danger:hover { background: #c0392b; }
        .btn-sm { padding: 6px 14px; font-size: 12px; border-radius: 6px; }
        .btn-outline { background: #fff; color: var(--primary); border: 1px solid var(--primary); }
        .btn-outline:hover { background: #f0f3ff; }
        input, select, textarea {
            padding: 10px 14px; border: 1px solid var(--border); border-radius: 8px;
            font-size: 14px; outline: none; transition: border .2s; font-family: inherit;
        }
        input:focus, select:focus, textarea:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(79,110,247,.1); }
        textarea { resize: vertical; min-height: 80px; }

        /* ======== 登录页 ======== */
        #page-login {
            justify-content: center; align-items: center;
            background: linear-gradient(135deg, #4f6ef7 0%, #6c8cff 50%, #a78bfa 100%);
        }
        .auth-card {
            background: var(--white); border-radius: 20px; padding: 40px 36px;
            width: 400px; max-width: 92vw; box-shadow: 0 20px 60px rgba(0,0,0,.15);
        }
        .auth-card h2 { text-align: center; margin-bottom: 8px; color: var(--text); font-size: 22px; }
        .auth-card .subtitle { text-align: center; color: var(--text-light); margin-bottom: 24px; font-size: 13px; }
        .auth-tabs { display: flex; gap: 0; margin-bottom: 24px; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); }
        .auth-tabs button {
            flex: 1; padding: 10px; border: none; background: #fafafa; cursor: pointer;
            font-size: 14px; transition: all .2s; font-weight: 500;
        }
        .auth-tabs button.active { background: var(--primary); color: #fff; }
        .auth-form { display: flex; flex-direction: column; gap: 16px; }
        .auth-form label { font-size: 13px; color: #666; font-weight: 500; }
        .auth-form .error-msg { color: var(--danger); font-size: 12px; display: none; }

        /* ======== 聊天页 ======== */
        #page-chat { flex-direction: row; }
        .sidebar {
            width: var(--sidebar-w); background: var(--white); border-right: 1px solid var(--border);
            display: flex; flex-direction: column; flex-shrink: 0;
        }
        .sidebar-header {
            padding: 16px 18px; font-weight: 600; font-size: 15px;
            border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;
        }
        .sidebar .new-btn {
            margin: 12px; padding: 10px; border: 2px dashed var(--border); border-radius: 10px;
            background: none; cursor: pointer; font-size: 13px; color: var(--primary);
            transition: all .2s; text-align: center;
        }
        .sidebar .new-btn:hover { border-color: var(--primary); background: #f8f9ff; }
        .session-list { flex: 1; overflow-y: auto; padding: 0 8px; }
        .session-item {
            padding: 12px 14px; border-radius: 10px; cursor: pointer; font-size: 13px;
            margin-bottom: 4px; transition: all .15s; color: #555;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .session-item:hover { background: #f0f2f5; }
        .session-item.active { background: #eef1ff; color: var(--primary); font-weight: 600; }
        .session-item { display: flex; justify-content: space-between; align-items: center; }
        .session-item .del-session {
            visibility: hidden; font-size: 16px; color: #ccc; cursor: pointer;
            padding: 2px 6px; border-radius: 4px; line-height: 1;
        }
        .session-item:hover .del-session { visibility: visible; }
        .session-item .del-session:hover { color: var(--danger); background: #fde8e8; }
        .chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
        .topbar {
            height: var(--header-h); background: var(--white); border-bottom: 1px solid var(--border);
            display: flex; align-items: center; justify-content: space-between; padding: 0 24px;
        }
        .topbar .brand { font-weight: 700; font-size: 16px; color: var(--primary); }
        .topbar .user-info { display: flex; align-items: center; gap: 12px; font-size: 13px; }
        .topbar .user-info a { color: var(--primary); cursor: pointer; text-decoration: none; }
        .topbar .user-info a:hover { text-decoration: underline; }
        .messages {
            flex: 1; overflow-y: auto; padding: 20px 24px; display: flex;
            flex-direction: column; gap: 14px; background: #f8f9fb;
        }
        .msg { max-width: 75%; padding: 12px 16px; border-radius: 14px; line-height: 1.65; word-break: break-word; font-size: 14px; }
        .msg.user { align-self: flex-end; background: var(--primary); color: #fff; border-bottom-right-radius: 4px; }
        .msg.ai { align-self: flex-start; background: var(--white); color: var(--text); border-bottom-left-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
        .msg.ai pre { background: #e8ecf1; padding: 8px 12px; border-radius: 8px; overflow-x: auto; font-size: 13px; margin: 6px 0; }
        .msg.ai code { background: #e8ecf1; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
        .cite-block { margin-top: 10px; border-top: 1px solid #eee; padding-top: 8px; }
        .cite-toggle {
            font-size: 12px; color: var(--primary); cursor: pointer; user-select: none;
            display: flex; align-items: center; gap: 4px;
        }
        .cite-toggle:hover { opacity: .8; }
        .cite-list { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
        .cite-item {
            background: var(--cite-bg); border-radius: 8px; padding: 8px 12px;
            border-left: 3px solid var(--primary); font-size: 12px;
        }
        .cite-item .cite-cat {
            display: inline-block; background: var(--primary); color: #fff;
            padding: 1px 8px; border-radius: 10px; font-size: 11px; margin-right: 6px;
        }
        .cite-item .cite-title { font-weight: 600; color: #555; }
        .cite-item .cite-snippet { color: #777; margin-top: 4px; line-height: 1.5; }
        .input-area {
            display: flex; padding: 14px 24px; border-top: 1px solid var(--border);
            gap: 12px; background: var(--white);
        }
        .input-area input {
            flex: 1; padding: 12px 18px; border-radius: 24px;
            border: 1px solid #ddd; font-size: 14px;
        }
        .input-area button {
            padding: 10px 28px; background: var(--primary); color: #fff;
            border: none; border-radius: 24px; font-size: 14px; cursor: pointer;
            transition: opacity .2s; white-space: nowrap; font-weight: 500;
        }
        .input-area button:hover { opacity: .85; }
        .input-area button:disabled { opacity: .5; cursor: not-allowed; }
        .loading { display: flex; gap: 5px; padding: 12px 16px; align-self: flex-start; }
        .loading span { width: 8px; height: 8px; background: #aaa; border-radius: 50%; animation: bounce 1.4s infinite ease-in-out both; }
        .loading span:nth-child(1) { animation-delay: -.32s; }
        .loading span:nth-child(2) { animation-delay: -.16s; }
        @keyframes bounce { 0%,80%,100% { transform: scale(0); } 40% { transform: scale(1); } }

        /* ======== 管理后台 ======== */
        #page-admin { flex-direction: row; }
        .admin-sidebar {
            width: 200px; background: #1e293b; color: #cbd5e1; display: flex;
            flex-direction: column; padding-top: 16px; flex-shrink: 0;
        }
        .admin-sidebar .nav-item {
            padding: 14px 20px; cursor: pointer; font-size: 14px; transition: all .15s;
            border-left: 3px solid transparent;
        }
        .admin-sidebar .nav-item:hover { background: #334155; color: #fff; }
        .admin-sidebar .nav-item.active { background: #334155; color: #fff; border-left-color: var(--primary); }
        .admin-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .admin-topbar {
            height: var(--header-h); background: var(--white); border-bottom: 1px solid var(--border);
            display: flex; align-items: center; justify-content: space-between; padding: 0 24px;
        }
        .admin-content { flex: 1; overflow-y: auto; padding: 24px; }
        .admin-content table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.04); }
        .admin-content th { background: #f8f9fb; padding: 12px 16px; text-align: left; font-size: 13px; font-weight: 600; color: #555; border-bottom: 2px solid var(--border); }
        .admin-content td { padding: 10px 16px; font-size: 13px; border-bottom: 1px solid #f0f0f0; }
        .admin-content td.actions { display: flex; gap: 6px; }
        .admin-toolbar { display: flex; gap: 12px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; }
        .admin-toolbar select { min-width: 140px; }
        .modal-overlay {
            position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,.4);
            display: none; justify-content: center; align-items: center; z-index: 100;
        }
        .modal-overlay.show { display: flex; }
        .modal {
            background: #fff; border-radius: 16px; padding: 28px; width: 500px;
            max-width: 92vw; max-height: 85vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,.2);
        }
        .modal h3 { margin-bottom: 20px; }
        .modal .form-group { margin-bottom: 14px; }
        .modal .form-group label { display: block; font-size: 13px; color: #666; margin-bottom: 4px; font-weight: 500; }
        .modal .form-group input, .modal .form-group select, .modal .form-group textarea { width: 100%; }
        .modal .form-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 20px; }

        /* ======== 响应式 ======== */
        @media (max-width: 768px) {
            .sidebar { display: none; }
            .msg { max-width: 90%; }
            .auth-card { padding: 28px 20px; }
            .admin-sidebar { width: 120px; }
        }

        /* Toast */
        .toast {
            position: fixed; top: 20px; right: 20px; z-index: 200;
            padding: 12px 20px; border-radius: 10px; color: #fff; font-size: 14px;
            animation: slideIn .3s ease; max-width: 360px;
        }
        .toast.success { background: var(--success); }
        .toast.error { background: var(--danger); }
        @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
    </style>
</head>
<body>

<!-- ==================== 登录/注册页 ==================== -->
<div id="page-login" class="page active">
    <div class="auth-card">
        <h2>👗 服装电商智能客服</h2>
        <p class="subtitle">AI 驱动的智能客服，随时为您解答服装疑问</p>
        <div class="auth-tabs">
            <button id="tab-login" class="active" onclick="switchAuthTab('login')">登录</button>
            <button id="tab-register" onclick="switchAuthTab('register')">注册</button>
        </div>
        <form id="form-login" class="auth-form" onsubmit="handleLogin(event)">
            <label>用户名</label>
            <input id="login-username" placeholder="请输入用户名" required autocomplete="username">
            <label>密码</label>
            <input id="login-password" type="password" placeholder="请输入密码" required autocomplete="current-password">
            <p class="error-msg" id="login-error"></p>
            <button type="submit" class="btn btn-primary" style="width:100%">登 录</button>
        </form>
        <form id="form-register" class="auth-form" style="display:none" onsubmit="handleRegister(event)">
            <label>用户名</label>
            <input id="reg-username" placeholder="请设置用户名" required minlength="1" maxlength="50">
            <label>密码（至少6位）</label>
            <input id="reg-password" type="password" placeholder="请设置密码" required minlength="6">
            <p class="error-msg" id="reg-error"></p>
            <button type="submit" class="btn btn-primary" style="width:100%">注 册</button>
        </form>
    </div>
</div>

<!-- ==================== 聊天页 ==================== -->
<div id="page-chat" class="page">
    <aside class="sidebar">
        <div class="sidebar-header">💬 会话历史</div>
        <button class="new-btn" onclick="newSession()">＋ 新建对话</button>
        <div class="session-list" id="session-list"></div>
    </aside>
    <main class="chat-main">
        <div class="topbar">
            <span class="brand">👗 服装电商智能客服</span>
            <div class="user-info">
                <span id="current-username"></span>
                <a id="admin-link" style="display:none" onclick="showPage('admin')">⚙️ 知识库管理</a>
                <a onclick="openChangePwdModal()">修改密码</a>
                <a onclick="logout()">退出</a>
            </div>
        </div>
        <div class="messages" id="msgs">
            <div class="msg ai">你好！我是服装电商智能客服助手 👋<br><br>
            我可以帮你：<br>
            📏 <b>尺码推荐</b> — 告诉我你的身高体重，帮你选对尺码<br>
            🎨 <b>颜色搭配</b> — 肤色/季节/场合搭配建议<br>
            🧺 <b>洗涤保养</b> — 各类面料的正确洗护方法<br>
            📦 <b>订单物流</b> — 查询你的订单和快递进度<br>
            ℹ️ <b>产品咨询</b> — 面料特性/新品信息/选购指南<br><br>
            直接打字问我吧！</div>
        </div>
        <div class="input-area">
            <input id="inp" placeholder="输入你的问题，例如：我身高175体重70，穿什么尺码的上衣？" autofocus>
            <button id="btn" onclick="send()">发送</button>
        </div>
    </main>
</div>

<!-- ==================== 管理后台 ==================== -->
<div id="page-admin" class="page">
    <aside class="admin-sidebar">
        <div style="padding:12px 20px;font-weight:700;color:#fff;font-size:15px;">⚙️ 管理后台</div>
        <div class="nav-item active" onclick="adminSwitchTab('docs')">📄 文档管理</div>
        <div class="nav-item" onclick="showPage('chat')">💬 返回聊天</div>
    </aside>
    <main class="admin-main">
        <div class="admin-topbar">
            <span style="font-weight:700;">知识库文档管理</span>
            <button class="btn btn-primary btn-sm" onclick="openDocModal()">＋ 添加文档</button>
        </div>
        <div class="admin-content">
            <div class="admin-toolbar">
                <select id="kb-cat-filter" onchange="loadDocs()">
                    <option value="">全部分类</option>
                </select>
                <button class="btn btn-outline btn-sm" onclick="initKB()" id="btn-init-kb">🔄 初始化默认知识库</button>
            </div>
            <table>
                <thead>
                    <tr><th>ID</th><th>标题</th><th>分类</th><th>性别</th><th>更新时间</th><th>操作</th></tr>
                </thead>
                <tbody id="doc-table-body"></tbody>
            </table>
            <div id="doc-pagination" style="margin-top:16px;text-align:center;"></div>
        </div>
    </main>
</div>

<!-- ==================== 文档编辑弹窗 ==================== -->
<div class="modal-overlay" id="doc-modal">
    <div class="modal">
        <h3 id="doc-modal-title">添加文档</h3>
        <div class="form-group"><label>标题</label><input id="doc-title" placeholder="文档标题"></div>
        <div class="form-group">
            <label>分类</label>
            <div style="display:flex;gap:8px;">
                <select id="doc-category" style="flex:1;"></select>
                <button type="button" class="btn btn-outline btn-sm" id="btn-add-cat" onclick="showAddCategory()">＋ 新增</button>
            </div>
            <div id="add-cat-wrap" style="display:none;margin-top:8px;gap:6px;">
                <input id="new-cat-name" placeholder="输入新分类名称" style="flex:1;">
                <button type="button" class="btn btn-primary btn-sm" onclick="confirmAddCategory()">确认</button>
                <button type="button" class="btn btn-outline btn-sm" onclick="cancelAddCategory()">取消</button>
            </div>
        </div>
        <div class="form-group"><label>适用性别</label><select id="doc-gender"><option value="通用">通用</option><option value="男">男</option><option value="女">女</option><option value="儿童">儿童</option></select></div>
        <div class="form-group"><label>内容</label><textarea id="doc-content" placeholder="文档正文内容" rows="6"></textarea></div>
        <input type="hidden" id="doc-edit-id" value="">
        <div class="form-actions">
            <button class="btn btn-outline btn-sm" onclick="closeDocModal()">取消</button>
            <button class="btn btn-primary btn-sm" onclick="saveDoc()">保存</button>
        </div>
    </div>
</div>

<!-- ==================== 修改密码弹窗 ==================== -->
<div class="modal-overlay" id="pwd-modal">
    <div class="modal">
        <h3>修改密码</h3>
        <div class="form-group"><label>旧密码</label><input id="old-pwd" type="password"></div>
        <div class="form-group"><label>新密码（至少6位）</label><input id="new-pwd" type="password"></div>
        <p class="error-msg" id="pwd-error" style="margin-bottom:12px;"></p>
        <div class="form-actions">
            <button class="btn btn-outline btn-sm" onclick="closePwdModal()">取消</button>
            <button class="btn btn-primary btn-sm" onclick="changePwd()">确认修改</button>
        </div>
    </div>
</div>

<script>
// ==================== 全局状态 ====================
let TOKEN = localStorage.getItem('access_token') || '';
let USER = JSON.parse(localStorage.getItem('user_info') || 'null');
let currentSessionId = null;
let currentPage = 1;

// ==================== 页面切换 ====================
function showPage(name) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const target = document.getElementById('page-' + name);
    if (target) target.classList.add('active');
    if (name === 'chat') loadSessions();
    if (name === 'admin') { loadDocs(); loadCategories(); }
}

// ==================== Toast ====================
function toast(msg, type) {
    const el = document.createElement('div');
    el.className = 'toast ' + (type || 'success');
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2500);
}

// ==================== API 封装 ====================
async function api(method, path, body) {
    const headers = { 'Content-Type': 'application/json' };
    if (TOKEN) headers['Authorization'] = 'Bearer ' + TOKEN;
    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || '请求失败 (' + res.status + ')');
    return data;
}

// ==================== 登录/注册 ====================
function switchAuthTab(tab) {
    document.getElementById('tab-login').classList.toggle('active', tab === 'login');
    document.getElementById('tab-register').classList.toggle('active', tab === 'register');
    document.getElementById('form-login').style.display = tab === 'login' ? '' : 'none';
    document.getElementById('form-register').style.display = tab === 'register' ? '' : 'none';
    // 切换时清空所有输入框和错误提示
    document.getElementById('login-username').value = '';
    document.getElementById('login-password').value = '';
    document.getElementById('login-error').style.display = 'none';
    document.getElementById('reg-username').value = '';
    document.getElementById('reg-password').value = '';
    document.getElementById('reg-error').style.display = 'none';
}

async function handleLogin(e) {
    e.preventDefault();
    const uname = document.getElementById('login-username').value.trim();
    const pwd = document.getElementById('login-password').value;
    const errEl = document.getElementById('login-error');
    errEl.style.display = 'none';
    try {
        const data = await api('POST', '/auth/login', { username: uname, password: pwd });
        TOKEN = data.access_token;
        USER = { user_id: data.user_id, username: data.username, is_admin: data.is_admin };
        localStorage.setItem('access_token', TOKEN);
        localStorage.setItem('user_info', JSON.stringify(USER));
        document.getElementById('current-username').textContent = USER.username;
        document.getElementById('admin-link').style.display = USER.is_admin ? '' : 'none';
        showPage('chat');
        newSession();
    } catch (err) {
        errEl.textContent = err.message;
        errEl.style.display = 'block';
    }
}

async function handleRegister(e) {
    e.preventDefault();
    const uname = document.getElementById('reg-username').value.trim();
    const pwd = document.getElementById('reg-password').value;
    const errEl = document.getElementById('reg-error');
    errEl.style.display = 'none';
    try {
        await api('POST', '/auth/register', { username: uname, password: pwd });
        toast('注册成功，请登录', 'success');
        // 清空注册表单并切换到登录
        document.getElementById('reg-username').value = '';
        document.getElementById('reg-password').value = '';
        document.getElementById('reg-error').style.display = 'none';
        switchAuthTab('login');
    } catch (err) {
        errEl.textContent = err.message;
        errEl.style.display = 'block';
    }
}

function logout() {
    TOKEN = ''; USER = null; currentSessionId = null;
    localStorage.removeItem('access_token');
    localStorage.removeItem('user_info');
    showPage('login');
    document.getElementById('msgs').innerHTML = '<div class="msg ai">你好！我是服装电商智能客服助手...</div>';
}

// ==================== 聊天 ====================
function addMsg(text, role, citations) {
    const msgs = document.getElementById('msgs');
    const div = document.createElement('div');
    div.className = 'msg ' + role;

    if (role === 'ai') {
        // 渲染 Markdown 风格的换行
        const contentDiv = document.createElement('div');
        contentDiv.className = 'msg-content';
        contentDiv.innerHTML = text.replace(/\n/g, '<br>').replace(/`([^`]+)`/g, '<code>$1</code>');
        div.appendChild(contentDiv);

        if (citations && citations.length > 0) {
            const citeBlock = document.createElement('div');
            citeBlock.className = 'cite-block';
            const toggle = document.createElement('div');
            toggle.className = 'cite-toggle';
            toggle.innerHTML = '📚 参考来源 (' + citations.length + ') ▾';
            toggle.onclick = function() {
                const list = this.nextElementSibling;
                const visible = list.style.display !== 'none';
                list.style.display = visible ? 'none' : '';
                this.innerHTML = '📚 参考来源 (' + citations.length + ') ' + (visible ? '▾' : '▴');
            };
            const list = document.createElement('div');
            list.className = 'cite-list';
            list.style.display = 'none';
            citations.forEach(c => {
                const item = document.createElement('div');
                item.className = 'cite-item';
                item.innerHTML = '<span class="cite-cat">' + escHtml(c.category) + '</span>' +
                    '<span class="cite-title">' + escHtml(c.title) + '</span>' +
                    '<div class="cite-snippet">' + escHtml(c.snippet) + '</div>';
                list.appendChild(item);
            });
            citeBlock.appendChild(toggle);
            citeBlock.appendChild(list);
            div.appendChild(citeBlock);
        }
    } else {
        div.textContent = text;
    }

    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
}

function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function showLoading() {
    const div = document.createElement('div');
    div.className = 'loading'; div.id = 'loader';
    div.innerHTML = '<span></span><span></span><span></span>';
    document.getElementById('msgs').appendChild(div);
    document.getElementById('msgs').scrollTop = document.getElementById('msgs').scrollHeight;
}
function hideLoading() { const l = document.getElementById('loader'); if (l) l.remove(); }

async function send() {
    const inp = document.getElementById('inp');
    const text = inp.value.trim();
    if (!text) return;
    addMsg(text, 'user');
    inp.value = '';
    document.getElementById('btn').disabled = true;
    showLoading();

    // 自动创建会话
    if (!currentSessionId && USER) {
        try { const s = await api('POST', '/sessions', { title: text.substring(0, 30) }); currentSessionId = s.id; loadSessions(); }
        catch(e) { /* 匿名使用不创建会话 */ }
    }

    try {
        const body = { message: text, enable_rag: true };
        if (currentSessionId) body.session_id = currentSessionId;
        const data = await api('POST', '/chat', body);
        hideLoading();
        addMsg(data.reply || '(未收到回复)', 'ai', data.citations);
    } catch (e) {
        hideLoading();
        addMsg('抱歉，请求出错了：' + e.message, 'ai');
    }
    document.getElementById('btn').disabled = false;
    inp.focus();
}

document.getElementById('inp').addEventListener('keydown', e => { if (e.key === 'Enter') send(); });

// ==================== 会话管理 ====================
async function loadSessions() {
    if (!TOKEN) return [];
    try {
        const sessions = await api('GET', '/sessions');
        const list = document.getElementById('session-list');
        list.innerHTML = sessions.map(s =>
            '<div class="session-item' + (s.id === currentSessionId ? ' active' : '') + '">' +
            '<span onclick="switchSession(' + s.id + ')" style="flex:1;overflow:hidden;text-overflow:ellipsis;">' +
            escHtml(s.session_title) + ' <span style="font-size:11px;color:#999;">(' + s.message_count + '条)</span></span>' +
            '<span class="del-session" title="删除对话" onclick="deleteSession(event, ' + s.id + ')">✕</span></div>'
        ).join('');
        return sessions;
    } catch(e) { return []; }
}

async function deleteSession(e, sid) {
    e.stopPropagation();
    if (!confirm('确定要删除此对话吗？删除后不可恢复。')) return;
    try {
        await api('DELETE', '/sessions/' + sid);
        if (currentSessionId === sid) {
            currentSessionId = null;
            document.getElementById('msgs').innerHTML = '<div class="msg ai">对话已删除。点击「＋ 新建对话」开始新咨询。</div>';
        }
        toast('对话已删除', 'success');
        loadSessions();
    } catch(e) { toast('删除失败: ' + e.message, 'error'); }
}

async function newSession() {
    if (!TOKEN) return;
    try {
        // 检查是否已有空会话（避免重复创建）
        const sessions = await api('GET', '/sessions');
        const emptySession = sessions.find(function(s) { return s.message_count === 0; });
        if (emptySession) {
            // 已有空会话，直接切换过去
            switchSession(emptySession.id);
            return;
        }
        // 没有空会话，创建新的
        const s = await api('POST', '/sessions', { title: '新对话' });
        currentSessionId = s.id;
        document.getElementById('msgs').innerHTML = '<div class="msg ai">新对话已开始！有什么服装相关的问题想问我？</div>';
        loadSessions();
    } catch(e) { toast('创建会话失败: ' + e.message, 'error'); }
}

async function switchSession(sid) {
    currentSessionId = sid;
    document.getElementById('msgs').innerHTML = '';
    document.getElementById('inp').value = '';
    try {
        const msgs = await api('GET', '/sessions/' + sid + '/messages');
        msgs.forEach(m => addMsg(m.content, m.role, m.citations));
    } catch(e) { addMsg('加载历史消息失败', 'ai'); }
    loadSessions();
}

// ==================== 密码修改 ====================
function openChangePwdModal() { document.getElementById('pwd-modal').classList.add('show'); }
function closePwdModal() { document.getElementById('pwd-modal').classList.remove('show'); }
async function changePwd() {
    const oldPwd = document.getElementById('old-pwd').value;
    const newPwd = document.getElementById('new-pwd').value;
    const errEl = document.getElementById('pwd-error');
    errEl.style.display = 'none';
    if (newPwd.length < 6) { errEl.textContent = '新密码至少6位'; errEl.style.display = 'block'; return; }
    try {
        await api('POST', '/auth/change-password', { old_password: oldPwd, new_password: newPwd });
        toast('密码修改成功', 'success');
        closePwdModal();
    } catch(e) { errEl.textContent = e.message; errEl.style.display = 'block'; }
}

// ==================== 知识库管理 ====================
async function loadDocs(page) {
    if (page) currentPage = page;
    const cat = document.getElementById('kb-cat-filter').value;
    try {
        const data = await api('GET', '/admin/kb/documents?category=' + encodeURIComponent(cat) + '&page=' + currentPage + '&page_size=10');
        const tbody = document.getElementById('doc-table-body');
        tbody.innerHTML = data.items.map(d =>
            '<tr><td>' + d.id + '</td><td>' + escHtml(d.title) + '</td><td>' + escHtml(d.category) +
            '</td><td>' + escHtml(d.gender || '通用') + '</td><td>' + d.updated_at + '</td><td class="actions">' +
            '<button class="btn btn-outline btn-sm" onclick="editDoc(' + d.id + ',\'' + escHtml(d.title) + '\',\'' + escHtml(d.category) + '\',\'' + escHtml(d.gender || '通用') + '\',\'' + escHtml(d.content.replace(/'/g, "\\'").replace(/\n/g, '\\n')) + '\')">编辑</button>' +
            '<button class="btn btn-danger btn-sm" onclick="deleteDoc(' + d.id + ')">删除</button></td></tr>'
        ).join('');
        // Pagination (10 per page)
        const totalPages = Math.ceil(data.total / 10);
        let pag = '';
        for (let i = 1; i <= totalPages; i++) {
            pag += '<button class="btn btn-sm ' + (i === currentPage ? 'btn-primary' : 'btn-outline') +
                '" onclick="loadDocs(' + i + ')" style="margin:0 2px;">' + i + '</button>';
        }
        document.getElementById('doc-pagination').innerHTML = pag;
    } catch(e) { toast('加载失败: ' + e.message, 'error'); }
}

async function loadCategories() {
    try {
        const cats = await api('GET', '/admin/kb/categories');
        // 更新筛选下拉框
        const sel = document.getElementById('kb-cat-filter');
        if (sel) {
            sel.innerHTML = '<option value="">全部分类</option>' + cats.map(c => '<option>' + escHtml(c) + '</option>').join('');
        }
        // 更新弹窗分类下拉框
        const docSel = document.getElementById('doc-category');
        if (docSel) {
            docSel.innerHTML = cats.map(c => '<option value="' + escHtml(c) + '">' + escHtml(c) + '</option>').join('');
        }
    } catch(e) {
        console.error('加载分类失败:', e);
        var defaults = ['尺码指南','颜色搭配','洗涤保养','产品信息','售后政策','通用'];
        var sel = document.getElementById('kb-cat-filter');
        if (sel && sel.options.length <= 1) {
            sel.innerHTML = '<option value="">全部分类</option>' + defaults.map(function(c){ return '<option>' + c + '</option>'; }).join('');
        }
        var docSel = document.getElementById('doc-category');
        if (docSel && docSel.options.length === 0) {
            docSel.innerHTML = defaults.map(function(c){ return '<option value="' + c + '">' + c + '</option>'; }).join('');
        }
    }
}

// ==================== 新增分类 ====================
function showAddCategory() {
    document.getElementById('add-cat-wrap').style.display = 'flex';
    document.getElementById('btn-add-cat').style.display = 'none';
    document.getElementById('new-cat-name').value = '';
    document.getElementById('new-cat-name').focus();
}

function cancelAddCategory() {
    document.getElementById('add-cat-wrap').style.display = 'none';
    document.getElementById('btn-add-cat').style.display = '';
    document.getElementById('new-cat-name').value = '';
}

function confirmAddCategory() {
    var name = document.getElementById('new-cat-name').value.trim();
    if (!name) return toast('请输入分类名称', 'error');
    // 检查是否已存在
    var sel = document.getElementById('doc-category');
    for (var i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === name) {
            toast('该分类已存在', 'error');
            return;
        }
    }
    // 添加到下拉框并选中
    var opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
    sel.value = name;
    cancelAddCategory();
    toast('分类「' + name + '」已添加（保存文档后生效）', 'success');
}

function openDocModal(doc) {
    document.getElementById('doc-edit-id').value = doc ? doc.id : '';
    document.getElementById('doc-modal-title').textContent = doc ? '编辑文档' : '添加文档';
    document.getElementById('doc-title').value = doc ? doc.title : '';
    document.getElementById('doc-category').value = doc ? doc.category : '通用';
    document.getElementById('doc-gender').value = doc ? (doc.gender || '通用') : '通用';
    document.getElementById('doc-content').value = doc ? doc.content : '';
    document.getElementById('doc-modal').classList.add('show');
}
function closeDocModal() { document.getElementById('doc-modal').classList.remove('show'); }

function editDoc(id, title, category, gender, content) {
    openDocModal({ id, title, category, gender, content });
}

async function saveDoc() {
    const id = document.getElementById('doc-edit-id').value;
    const title = document.getElementById('doc-title').value.trim();
    const category = document.getElementById('doc-category').value.trim();
    const gender = document.getElementById('doc-gender').value;
    const content = document.getElementById('doc-content').value.trim();
    if (!title || !content) return toast('标题和内容不能为空', 'error');
    if (!category) return toast('分类不能为空', 'error');
    try {
        if (id) {
            await api('PUT', '/admin/kb/documents/' + id, { title, category, content, gender });
        } else {
            await api('POST', '/admin/kb/documents', { title, category, content, gender });
        }
        closeDocModal();
        loadDocs();
        loadCategories();  // 刷新分类列表（可能有新分类）
        toast(id ? '文档更新成功' : '文档添加成功', 'success');
    } catch(e) { toast('保存失败: ' + e.message, 'error'); }
}

async function deleteDoc(id) {
    if (!confirm('确定要删除此文档吗？此操作不可撤销。')) return;
    try {
        await api('DELETE', '/admin/kb/documents/' + id);
        loadDocs();
        toast('文档已删除', 'success');
    } catch(e) { toast('删除失败: ' + e.message, 'error'); }
}

async function initKB() {
    const btn = document.getElementById('btn-init-kb');
    btn.disabled = true; btn.textContent = '初始化中...';
    try {
        const res = await api('POST', '/admin/kb/init');
        toast(res.message, 'success');
        loadDocs(); loadCategories();
    } catch(e) { toast('初始化失败: ' + e.message, 'error'); }
    btn.disabled = false; btn.textContent = '🔄 初始化默认知识库';
}

function adminSwitchTab(tab) { /* 当前只有文档管理，预留扩展 */ }

// ==================== 初始化 ====================
window.onload = function() {
    if (TOKEN && USER) {
        document.getElementById('current-username').textContent = USER.username;
        document.getElementById('admin-link').style.display = USER.is_admin ? '' : 'none';
        showPage('chat');
        loadSessions().then(function(sessions) {
            if (!sessions || sessions.length === 0) {
                newSession();
            }
        });
    } else {
        showPage('login');
    }
};
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
