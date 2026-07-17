"""
FastAPI 入口：启动 HTTP 服务，提供完整的服装电商智能客服系统。
包含：认证 / 聊天(RAG) / 会话管理 / 知识库管理 / 限流 / 全局异常处理。

启动命令：uvicorn app.main:app --reload
"""
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from app.models import (
    ChatRequest, ChatResponse,
    RegisterRequest, LoginRequest, LoginResponse,
    ChangePasswordRequest, UserInfo,
    SessionCreateRequest, SessionCreateResponse,
    SessionListItem, SessionMessage,
    DocumentCreate, DocumentUpdate, DocumentResponse, DocumentListResponse,
    BatchDeleteRequest, Citation,
    AdminUserItem, AdminOrderItem, AdminOrderDetail,
    BenchmarkRequest, BenchmarkResult,
)
from app.agent import chat as agent_chat, chat_stream as agent_chat_stream
from app.benchmark import run_benchmark, run_benchmark_stream
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
    # 自动修复：如果 ChromaDB 向量缺失 gender 元数据，自动重建索引
    try:
        from app.rag import _get_vectorstore, reindex_knowledge_base
        vs = _get_vectorstore()
        existing = vs.get()
        if existing["ids"]:
            # 抽样检查第一条文档是否有 gender 字段
            sample_meta = existing["metadatas"][0] if existing["metadatas"] else {}
            if "gender" not in sample_meta:
                app_log.warning("检测到 ChromaDB 向量缺失 gender 字段，自动重建索引...")
                reindexed = reindex_knowledge_base()
                app_log.info("自动重建完成: %d 条文档已重新索引", reindexed)
    except Exception as e:
        app_log.warning("向量索引检查/重建出错（不影响正常使用）: %s", e)
    app_log.info("服装电商智能客服 v0.2.1 启动")


# ==================== 认证路由 ====================

@app.post("/auth/register")
async def register(req: RegisterRequest):
    """用户注册"""
    from app.database import create_user, get_user_by_username

    if get_user_by_username(req.username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    user_id = create_user(req.username, hash_password(req.password), is_admin=0)
    auth_log.info("新用户注册: id=%d username=%s", user_id, req.username)
    return {"id": user_id, "username": req.username, "message": "注册成功"}


@app.post("/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """用户登录，返回 JWT 令牌"""
    from app.database import get_user_by_username

    user = get_user_by_username(req.username)
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
    from app.database import get_user_by_id, update_password

    db_user = get_user_by_id(user["user_id"])
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


@app.post("/admin/kb/documents/batch-delete")
async def admin_batch_delete_documents(
    req: BatchDeleteRequest,
    user: dict = Depends(require_admin),
):
    """管理员：批量删除知识库文档"""
    from app.database import delete_document
    from app.rag import delete_document as rag_delete

    deleted = 0
    failed = []
    for doc_id in req.ids:
        if delete_document(doc_id):
            rag_delete(doc_id)
            deleted += 1
        else:
            failed.append(doc_id)
    app_log.info("管理员批量删除文档: %d成功 %d失败", deleted, len(failed))
    return {"message": f"成功删除 {deleted} 条文档", "deleted": deleted, "failed": failed}


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


@app.post("/admin/kb/reindex")
async def admin_reindex_kb(user: dict = Depends(require_admin)):
    """管理员：从 SQLite 重建整个 ChromaDB 向量索引"""
    from app.rag import reindex_knowledge_base
    count = reindex_knowledge_base()
    return {"message": f"向量索引重建完成，共 {count} 条文档", "count": count}


@app.get("/admin/kb/categories")
async def admin_get_categories(user: dict = Depends(require_admin)):
    """管理员：获取所有文档分类"""
    from app.database import get_categories
    return get_categories()


# ==================== 管理后台：用户管理 ====================

@app.get("/admin/users", response_model=list[AdminUserItem])
async def admin_list_users(user: dict = Depends(require_admin)):
    """管理员：获取所有用户列表"""
    from app.database import list_all_users
    users = list_all_users()
    return [AdminUserItem(
        id=u["id"],
        username=u["username"],
        phone=u.get("phone"),
        is_admin=bool(u["is_admin"]),
        created_at=u["created_at"],
    ) for u in users]


# ==================== 管理后台：订单管理 ====================

@app.get("/admin/orders", response_model=list[AdminOrderItem])
async def admin_list_orders(user: dict = Depends(require_admin)):
    """管理员：获取所有订单列表（含用户名和手机号）"""
    from app.database import list_all_orders
    orders = list_all_orders()
    return [AdminOrderItem(**o) for o in orders]


@app.get("/admin/orders/{order_id}", response_model=AdminOrderDetail)
async def admin_get_order(order_id: int, user: dict = Depends(require_admin)):
    """管理员：获取单个订单详情"""
    from app.database import get_order_by_id
    order = get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    return AdminOrderDetail(**order)


# ==================== 聊天路由 ====================

@app.post("/chat")
async def chat_api(req: ChatRequest, user: dict | None = Depends(get_optional_user)):
    """
    对话接口（向后兼容：未登录也可使用基础功能）。

    支持两种模式：
    - stream=False（默认）：等待完整回复后返回 JSON
    - stream=True：SSE 流式响应，逐 token 推送

    面试知识点：
    - 为什么流式响应用 NDJSON（一行一个 JSON）？
      NDJSON 比 SSE 的 `data:` 前缀更简洁，前端用 ReadableStream + split('\n')
      即可解析，且与业务数据格式统一（压测也是 NDJSON）。
    """
    user_id = user["user_id"] if user else None

        # --- 流式模式 ---
    if req.stream:
        import json as _json

        async def sse_stream():
            # 先持久化用户消息（异步生成器内无法在外部保存）
            if user_id and req.session_id:
                try:
                    from app.database import save_message, get_session_by_id
                    session = get_session_by_id(req.session_id)
                    if session and session["user_id"] == user_id:
                        save_message(req.session_id, user_id, "user", req.message)
                except Exception:
                    pass

            full_reply = ""
            citations = None

            async for event in agent_chat_stream(
                message=req.message,
                user_id=user_id or req.user_id,
                session_id=req.session_id,
                enable_rag=req.enable_rag,
            ):
                yield _json.dumps(event, ensure_ascii=False) + "\n"
                if event["type"] == "token":
                    full_reply += event["content"]
                elif event["type"] == "done":
                    citations = event.get("citations")

            # 流结束后持久化 AI 回复
            if user_id and req.session_id and full_reply:
                try:
                    from app.database import save_message, get_session_by_id
                    session = get_session_by_id(req.session_id)
                    if session and session["user_id"] == user_id:
                        citations_json = _json.dumps(citations, ensure_ascii=False) if citations else None
                        save_message(req.session_id, user_id, "assistant", full_reply, citations_json)
                except Exception:
                    pass

        return StreamingResponse(
            sse_stream(),
            media_type="application/x-ndjson",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

# --- 批处理模式（原有逻辑）---
    reply, citations = await agent_chat(
        message=req.message,
        user_id=user_id or req.user_id,
        session_id=req.session_id,
        enable_rag=req.enable_rag,
    )

    # 持久化消息（仅登录用户 + 有会话时）
    if user_id and req.session_id:
        import json as _json2
        from app.database import save_message, get_session_by_id

        session = get_session_by_id(req.session_id)
        if session and session["user_id"] == user_id:
            save_message(req.session_id, user_id, "user", req.message)
            citations_json = _json2.dumps(citations, ensure_ascii=False) if citations else None
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


# ==================== 压测仪表盘 ====================

@app.get("/benchmark", response_class=HTMLResponse)
def benchmark_page():
    """压测仪表盘页面（JS 端检查 token，未登录自动跳回主页）"""
    return HTMLResponse(content=BENCHMARK_PAGE)


@app.post("/admin/benchmark/run", response_model=BenchmarkResult)
async def benchmark_run(req: BenchmarkRequest, request: Request, user: dict = Depends(require_admin)):
    """
    执行压测（批处理模式）：等所有请求完成后一次性返回结果。
    如需实时推送，使用 /admin/benchmark/stream
    """
    base_url = str(request.base_url).rstrip("/")
    result = await run_benchmark(req.num_users, req.messages, base_url)
    return BenchmarkResult(**result)


@app.post("/admin/benchmark/stream")
async def benchmark_stream(req: BenchmarkRequest, request: Request, user: dict = Depends(require_admin)):
    """
    流式压测：每完成一个请求就推送一条 SSE 事件，前端实时更新图表。

    事件格式（NDJSON，一行一个 JSON）：
      {"type":"tick","result":{...},"completed":1,"total":10}
      ...
      {"type":"summary","total_requests":10,"qps":...,"latency":{...},...}
    """
    import json as _json

    base_url = str(request.base_url).rstrip("/")

    async def event_stream():
        async for event in run_benchmark_stream(req.num_users, req.messages, base_url):
            yield _json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


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
                <a id="admin-link" style="display:none" onclick="showPage('admin')">⚙️ 后台管理</a>
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
        <div class="nav-item active" onclick="adminSwitchTab('users')">👥 用户管理</div>
        <div class="nav-item" onclick="adminSwitchTab('orders')">📦 订单管理</div>
        <div class="nav-item" onclick="adminSwitchTab('docs')">📄 文档管理</div>
        <div class="nav-item" onclick="window.open('/benchmark')">⚡ 压测仪表盘</div>
        <div class="nav-item" onclick="showPage('chat')">💬 返回聊天</div>
    </aside>
    <main class="admin-main">
        <div class="admin-topbar">
            <span style="font-weight:700;" id="admin-tab-title">用户管理</span>
            <button class="btn btn-primary btn-sm" id="btn-add-doc" onclick="openDocModal()" style="display:none">＋ 添加文档</button>
        </div>
        <div class="admin-content">
            <!-- ===== 用户管理标签 ===== -->
            <div id="admin-tab-users">
                <table>
                    <thead>
                        <tr><th>ID</th><th>用户名</th><th>手机号</th><th>管理员</th><th>注册时间</th></tr>
                    </thead>
                    <tbody id="user-table-body"></tbody>
                </table>
            </div>
            <!-- ===== 订单管理标签 ===== -->
            <div id="admin-tab-orders" style="display:none">
                <table>
                    <thead>
                        <tr><th>ID</th><th>订单号</th><th>用户名</th><th>手机号</th><th>商品</th><th>金额</th><th>状态</th><th>下单时间</th></tr>
                    </thead>
                    <tbody id="order-table-body"></tbody>
                </table>
            </div>
            <!-- ===== 文档管理标签 ===== -->
            <div id="admin-tab-docs" style="display:none">
                <div class="admin-toolbar">
                    <select id="kb-cat-filter" onchange="loadDocs()">
                        <option value="">全部分类</option>
                    </select>
                    <button class="btn btn-outline btn-sm" onclick="initKB()" id="btn-init-kb">🔄 初始化默认知识库</button>
                    <button class="btn btn-outline btn-sm" id="btn-batch-mode" onclick="toggleBatchMode()">🗑 批量删除</button>
                    <button class="btn btn-danger btn-sm" id="btn-batch-confirm" onclick="batchDeleteDocs()" style="display:none" disabled>确认删除</button>
                    <span id="selected-count" style="display:none;font-size:13px;color:var(--danger);margin-left:8px;"></span>
                </div>
                <table>
                    <thead>
                        <tr><th class="batch-col" style="display:none"><input type="checkbox" id="select-all" onchange="toggleSelectAll()" title="全选/取消全选"></th><th>ID</th><th>标题</th><th>分类</th><th>性别</th><th>更新时间</th><th>操作</th></tr>
                    </thead>
                    <tbody id="doc-table-body"></tbody>
                </table>
                <div id="doc-pagination" style="margin-top:16px;text-align:center;"></div>
            </div>
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
    if (name === 'admin') { adminSwitchTab(adminCurrentTab || 'users'); }
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
    // 清空登录表单
    document.getElementById('login-username').value = '';
    document.getElementById('login-password').value = '';
    document.getElementById('login-error').style.display = 'none';
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

// ==================== 流式对话辅助函数 ====================
// 创建一个空 AI 消息气泡，返回 contentDiv 以便逐 token 追加
function createStreamingMsg() {
    const msgs = document.getElementById('msgs');
    const div = document.createElement('div');
    div.className = 'msg ai';
    div.id = 'streaming-msg';
    const contentDiv = document.createElement('div');
    contentDiv.className = 'msg-content';
    div.appendChild(contentDiv);
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    return { container: div, contentDiv: contentDiv };
}

// 移除 streaming 标记并渲染引用
function finalizeStreamingMsg(fullText, citations) {
    const el = document.getElementById('streaming-msg');
    if (!el) return;
    el.removeAttribute('id');

    const contentDiv = el.querySelector('.msg-content');
    if (contentDiv) {
        contentDiv.innerHTML = fullText.replace(/\n/g, '<br>').replace(/`([^`]+)`/g, '<code>$1</code>');
    }

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
        citations.forEach(function(c) {
            const item = document.createElement('div');
            item.className = 'cite-item';
            item.innerHTML = '<span class="cite-cat">' + escHtml(c.category) + '</span>' +
                '<span class="cite-title">' + escHtml(c.title) + '</span>' +
                '<div class="cite-snippet">' + escHtml(c.snippet) + '</div>';
            list.appendChild(item);
        });
        citeBlock.appendChild(toggle);
        citeBlock.appendChild(list);
        el.appendChild(citeBlock);
    }

    document.getElementById("msgs").scrollTop = document.getElementById("msgs").scrollHeight;
}

async function send() {
    const inp = document.getElementById('inp');
    const text = inp.value.trim();
    if (!text) return;
    addMsg(text, 'user');
    inp.value = '';
    document.getElementById('btn').disabled = true;

    // 创建空气泡准备流式填充
    const streaming = createStreamingMsg();
    let fullText = '';
    let citations = null;

    // 自动创建会话
    if (!currentSessionId && USER) {
        try { const s = await api('POST', '/sessions', { title: text.substring(0, 30) }); currentSessionId = s.id; loadSessions(); }
        catch(e) { /* 匿名使用不创建会话 */ }
    }

    try {
        const body = { message: text, enable_rag: true, stream: true };
        if (currentSessionId) body.session_id = currentSessionId;

        const headers = { 'Content-Type': 'application/json' };
        if (TOKEN) headers['Authorization'] = 'Bearer ' + TOKEN;

        const res = await fetch('/chat', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(body),
        });

        if (!res.ok) {
            const errData = await res.json().catch(function() { return {}; });
            throw new Error(errData.detail || '请求失败 (' + res.status + ')');
        }

        // 流式读取 NDJSON（一行一个 JSON）
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });

            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (let i = 0; i < lines.length; i++) {
                const line = lines[i].trim();
                if (!line) continue;
                try {
                    const event = JSON.parse(line);
                    if (event.type === 'token') {
                        fullText += event.content;
                        streaming.contentDiv.textContent = fullText;
                        document.getElementById('msgs').scrollTop = document.getElementById('msgs').scrollHeight;
                    } else if (event.type === 'clear') {
                        fullText = '';
                        streaming.contentDiv.textContent = '';
                    } else if (event.type === 'done') {
                        citations = event.citations || null;
                        finalizeStreamingMsg(fullText, citations);
                    } else if (event.type === 'error') {
                        streaming.contentDiv.textContent = event.message;
                    }
                } catch(e) { console.error('解析流事件失败:', e, line); }
            }
        }
    } catch (e) {
        streaming.contentDiv.textContent = '抱歉，请求出错了：' + e.message;
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
            '<tr><td class="batch-col" style="display:none"><input type="checkbox" class="doc-check" value="' + d.id + '" onchange="onCheckChange()"></td><td>' + d.id + '</td><td>' + escHtml(d.title) + '</td><td>' + escHtml(d.category) +
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

// ==================== 批量删除 ====================
let batchMode = false;

function toggleBatchMode() {
    batchMode = !batchMode;
    // 显示/隐藏所有勾选列
    document.querySelectorAll('.batch-col').forEach(el => el.style.display = batchMode ? '' : 'none');
    const modeBtn = document.getElementById('btn-batch-mode');
    const confirmBtn = document.getElementById('btn-batch-confirm');
    if (batchMode) {
        modeBtn.textContent = '取消';
        confirmBtn.style.display = '';
    } else {
        modeBtn.textContent = '🗑 批量删除';
        confirmBtn.style.display = 'none';
        // 退出时清空勾选
        document.getElementById('select-all').checked = false;
        document.querySelectorAll('.doc-check').forEach(cb => cb.checked = false);
    }
    updateBatchUI();
}

function getCheckedIds() {
    return [...document.querySelectorAll('.doc-check:checked')].map(cb => parseInt(cb.value));
}

function toggleSelectAll() {
    const checked = document.getElementById('select-all').checked;
    document.querySelectorAll('.doc-check').forEach(cb => cb.checked = checked);
    updateBatchUI();
}

function onCheckChange() {
    const all = document.querySelectorAll('.doc-check');
    const checked = document.querySelectorAll('.doc-check:checked');
    document.getElementById('select-all').checked = all.length > 0 && checked.length === all.length;
    updateBatchUI();
}

function updateBatchUI() {
    const ids = getCheckedIds();
    const confirmBtn = document.getElementById('btn-batch-confirm');
    const span = document.getElementById('selected-count');
    if (batchMode && ids.length > 0) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = '确认删除(' + ids.length + ')';
        span.style.display = '';
        span.textContent = '';
    } else if (batchMode) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = '确认删除';
        span.style.display = 'none';
    } else {
        span.style.display = 'none';
    }
}

async function batchDeleteDocs() {
    const ids = getCheckedIds();
    if (ids.length === 0) return toast('请先勾选要删除的文档', 'error');
    if (!confirm('确定要删除选中的 ' + ids.length + ' 条文档吗？此操作不可撤销！')) return;
    try {
        const res = await api('POST', '/admin/kb/documents/batch-delete', { ids: ids });
        // 退出批量模式
        batchMode = false;
        document.querySelectorAll('.batch-col').forEach(el => el.style.display = 'none');
        document.getElementById('btn-batch-mode').textContent = '🗑 批量删除';
        document.getElementById('btn-batch-confirm').style.display = 'none';
        document.getElementById('select-all').checked = false;
        loadDocs();
        updateBatchUI();
        toast(res.message, 'success');
    } catch(e) { toast('批量删除失败: ' + e.message, 'error'); }
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

let adminCurrentTab = 'users';

function adminSwitchTab(tab) {
    adminCurrentTab = tab;
    // 侧边栏高亮
    const tabs = { users: '用户管理', orders: '订单管理', docs: '文档管理' };
    document.querySelectorAll('.admin-sidebar .nav-item').forEach(el => {
        el.classList.remove('active');
        if (el.textContent.includes(tabs[tab] || '')) el.classList.add('active');
    });
    // 切换面板
    ['users', 'orders', 'docs'].forEach(t => {
        const panel = document.getElementById('admin-tab-' + t);
        if (panel) panel.style.display = (t === tab) ? '' : 'none';
    });
    // 顶部标题和按钮
    const titles = { users: '用户管理', orders: '订单管理', docs: '知识库文档管理' };
    document.getElementById('admin-tab-title').textContent = titles[tab] || '';
    document.getElementById('btn-add-doc').style.display = (tab === 'docs') ? '' : 'none';
    // 加载数据
    if (tab === 'users') loadUsers();
    if (tab === 'orders') loadOrders();
    if (tab === 'docs') { loadDocs(); loadCategories(); }
}

async function loadUsers() {
    try {
        const users = await api('GET', '/admin/users');
        const tbody = document.getElementById('user-table-body');
        tbody.innerHTML = users.map(u =>
            '<tr><td>' + u.id + '</td><td>' + escHtml(u.username) + '</td><td>' + (u.phone || '-') +
            '</td><td>' + (u.is_admin ? '是' : '否') + '</td><td>' + u.created_at + '</td></tr>'
        ).join('');
    } catch(e) { toast('加载用户列表失败: ' + e.message, 'error'); }
}

async function loadOrders() {
    try {
        const orders = await api('GET', '/admin/orders');
        const tbody = document.getElementById('order-table-body');
        tbody.innerHTML = orders.map(o =>
            '<tr><td>' + o.id + '</td><td>' + escHtml(o.order_no) + '</td><td>' + escHtml(o.username) +
            '</td><td>' + (o.phone || '-') + '</td><td>' + escHtml(o.product_name) +
            '</td><td>¥' + o.amount.toFixed(2) + '</td><td>' + escHtml(o.status) +
            '</td><td>' + o.created_at + '</td></tr>'
        ).join('');
    } catch(e) { toast('加载订单列表失败: ' + e.message, 'error'); }
}

// ==================== 初始化 ====================
window.onload = function() {
    // 始终先显示登录页，用户需手动登录
    showPage('login');
    if (TOKEN && USER) {
        // 预填用户名方便快速登录
        document.getElementById('login-username').value = USER.username;
    }
};
</script>
</body>
</html>
"""


# ==================== 压测仪表盘 HTML ====================

BENCHMARK_PAGE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>压测仪表盘 — 智能客服</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js">
    </script>
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
            --warning: #f39c12;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", sans-serif;
            background: var(--bg);
            min-height: 100vh;
            color: var(--text);
        }

        /* ======== 顶部导航 ======== */
        .topbar {
            background: var(--white);
            height: 56px;
            display: flex;
            align-items: center;
            padding: 0 24px;
            border-bottom: 1px solid var(--border);
            gap: 16px;
            box-shadow: 0 1px 4px rgba(0,0,0,.04);
        }
        .topbar .logo { font-size: 18px; font-weight: 700; color: var(--primary); }
        .topbar .sep { flex: 1; }
        .topbar a {
            color: var(--primary); text-decoration: none; font-size: 13px;
            padding: 6px 12px; border-radius: 6px; transition: background .2s;
        }
        .topbar a:hover { background: #f0f3ff; }

        /* ======== 主布局 ======== */
        .container { max-width: 1100px; margin: 0 auto; padding: 24px; }

        /* ======== 控制栏 ======== */
        .control-bar {
            background: var(--white);
            border-radius: 12px;
            padding: 20px 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 4px rgba(0,0,0,.04);
            display: flex;
            align-items: flex-end;
            gap: 20px;
            flex-wrap: wrap;
        }
        .control-group { display: flex; flex-direction: column; gap: 6px; }
        .control-group label { font-size: 12px; color: var(--text-light); font-weight: 500; }
        .control-group input, .control-group select {
            padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px;
            font-size: 14px; outline: none; font-family: inherit;
        }
        .control-group input:focus, .control-group select:focus {
            border-color: var(--primary); box-shadow: 0 0 0 3px rgba(79,110,247,.1);
        }
        .slider-row { display: flex; align-items: center; gap: 10px; }
        .slider-row input[type=range] { width: 160px; accent-color: var(--primary); }
        .slider-row .val { font-weight: 700; font-size: 16px; min-width: 28px; text-align: center; }
        .btn-run {
            padding: 10px 28px; border: none; border-radius: 8px;
            font-size: 14px; font-weight: 600; cursor: pointer;
            background: var(--primary); color: #fff; transition: all .2s;
            height: 40px;
        }
        .btn-run:hover:not(:disabled) { background: #3d5ce5; transform: translateY(-1px); }
        .btn-run:disabled { background: #bbb; cursor: not-allowed; }
        .btn-run.running { background: var(--danger); animation: pulse 1.2s infinite; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }

        /* ======== 进度信息 ======== */
        .progress-info {
            background: var(--white);
            border-radius: 12px;
            padding: 12px 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 4px rgba(0,0,0,.04);
            display: none;
            align-items: center;
            gap: 12px;
            font-size: 13px;
            color: var(--text-light);
        }
        .progress-info.show { display: flex; }
        .spinner {
            width: 20px; height: 20px;
            border: 3px solid var(--border);
            border-top-color: var(--primary);
            border-radius: 50%;
            animation: spin .6s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        /* ======== 统计卡片 ======== */
        .stats-row {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: var(--white);
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            box-shadow: 0 1px 4px rgba(0,0,0,.04);
        }
        .stat-card .value {
            font-size: 32px; font-weight: 700; color: var(--primary);
            margin-bottom: 4px;
        }
        .stat-card .value.danger { color: var(--danger); }
        .stat-card .value.success { color: var(--success); }
        .stat-card .label { font-size: 12px; color: var(--text-light); }

        /* ======== 图表区 ======== */
        .charts-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 20px;
        }
        .chart-card {
            background: var(--white);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 1px 4px rgba(0,0,0,.04);
        }
        .chart-card h3 {
            font-size: 15px; margin-bottom: 16px; color: var(--text);
            font-weight: 600;
        }
        .chart-card canvas { max-height: 320px; }

        /* ======== 错误列表 ======== */
        .error-list {
            background: var(--white);
            border-radius: 12px;
            padding: 20px 24px;
            box-shadow: 0 1px 4px rgba(0,0,0,.04);
            display: none;
        }
        .error-list.show { display: block; }
        .error-list h3 { font-size: 15px; margin-bottom: 12px; color: var(--danger); font-weight: 600; }
        .error-list .err-item {
            font-size: 12px; color: var(--danger); padding: 6px 0;
            border-bottom: 1px solid var(--border); font-family: "Consolas", monospace;
        }

        /* ======== 指标说明 ======== */
        .help-section {
            background: var(--white);
            border-radius: 12px;
            padding: 20px 24px;
            margin-top: 20px;
            box-shadow: 0 1px 4px rgba(0,0,0,.04);
        }
        .help-section h3 { font-size: 15px; margin-bottom: 12px; }
        .help-section table { width: 100%; border-collapse: collapse; font-size: 13px; }
        .help-section th, .help-section td {
            text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border);
        }
        .help-section th { color: var(--primary); font-weight: 600; }

        /* ======== Toast ======== */
        .toast {
            position: fixed; top: 20px; right: 20px; z-index: 9999;
            padding: 12px 20px; border-radius: 8px; color: #fff; font-size: 13px;
            opacity: 0; transform: translateY(-10px); transition: all .3s;
            pointer-events: none;
        }
        .toast.show { opacity: 1; transform: translateY(0); }
        .toast-error { background: var(--danger); }
        .toast-success { background: var(--success); }

        @media (max-width: 768px) {
            .stats-row { grid-template-columns: repeat(2, 1fr); }
            .charts-row { grid-template-columns: 1fr; }
            .control-bar { flex-direction: column; align-items: stretch; }
        }
    </style>
</head>
<body>

<div class="topbar">
    <span class="logo">⚡ 压测仪表盘</span>
    <span style="font-size:12px;color:var(--text-light)">模拟多用户并发访问客服系统</span>
    <span class="sep"></span>
    <a href="/">💬 返回客服</a>
    <a href="/admin">⚙️ 后台管理</a>
</div>

<div class="container">
    <!-- 控制栏 -->
    <div class="control-bar">
        <div class="control-group">
            <label>并发用户数</label>
            <div class="slider-row">
                <input type="range" id="num-users" min="1" max="100" value="10" oninput="onSliderChange()">
                <span class="val" id="num-users-val">10</span>
            </div>
        </div>
        <div class="control-group" style="flex:1;min-width:240px;">
            <label>提问内容（一行一个，压测时轮询分配）</label>
            <textarea id="messages" rows="3" style="font-size:13px;">我想买一件适合夏天穿的连衣裙，有什么推荐吗？
我身高170cm体重65kg，应该穿什么尺码？
我的订单号是ORD20240715001，帮我查一下物流
纯棉T恤怎么洗不会缩水？
这件衣服支持退换货吗？</textarea>
        </div>
        <button class="btn-run" id="btn-run" onclick="startBenchmark()">🚀 开始压测</button>
    </div>

    <!-- 进度条 -->
    <div class="progress-info" id="progress-info">
        <div class="spinner"></div>
        <span id="progress-text">压测进行中...</span>
    </div>

    <!-- 统计卡片 -->
    <div class="stats-row" id="stats-row" style="display:none;">
        <div class="stat-card">
            <div class="value" id="stat-qps">--</div>
            <div class="label">QPS（每秒处理请求数）</div>
        </div>
        <div class="stat-card">
            <div class="value" id="stat-avg">--</div>
            <div class="label">平均延迟 (ms)</div>
        </div>
        <div class="stat-card">
            <div class="value success" id="stat-rate">--</div>
            <div class="label">成功率</div>
        </div>
        <div class="stat-card">
            <div class="value" id="stat-total">--</div>
            <div class="label">总耗时 (秒)</div>
        </div>
    </div>

    <!-- 图表区 -->
    <div class="charts-row" id="charts-row" style="display:none;">
        <div class="chart-card">
            <h3>📊 延迟分位数 (ms) — 越低越好</h3>
            <canvas id="chart-percentile"></canvas>
        </div>
        <div class="chart-card">
            <h3>📍 逐请求延迟分布 — 看是否越压越慢</h3>
            <canvas id="chart-scatter"></canvas>
        </div>
    </div>

    <!-- 错误列表 -->
    <div class="error-list" id="error-list">
        <h3>⚠️ 失败请求详情</h3>
        <div id="error-items"></div>
    </div>

    <!-- 指标说明 -->
    <div class="help-section">
        <h3>📖 指标怎么看？</h3>
        <table>
            <tr><th>指标</th><th>含义</th><th>怎么判断好坏</th></tr>
            <tr><td>QPS</td><td>每秒能处理多少请求</td><td>越高越好。客服系统通常 5-50 QPS 即可用</td></tr>
            <tr><td>P50 延迟</td><td>一半用户在此时间内得到回复</td><td>用户体感的"快慢"，2 秒内较好</td></tr>
            <tr><td>P95 延迟</td><td>最慢的 5% 用户等了多久</td><td>长尾指标，超过 10 秒说明有毛刺</td></tr>
            <tr><td>P99 延迟</td><td>最慢的 1% 用户等了多久</td><td>定位极端情况，能否接受取决于业务</td></tr>
            <tr><td>成功率</td><td>返回 200 的请求占比</td><td>必须 100%，低于 95% 需要排查</td></tr>
            <tr><td>散点图趋势</td><td>请求按时间排列的延迟变化</td><td>如果散点越来越高 → 系统在累积压力</td></tr>
        </table>
    </div>
</div>

<div class="toast" id="toast"></div>

<script>
// ==================== 状态 ====================
let charts = { percentile: null, scatter: null };
let running = false;
let liveResults = [];  // 实时累积的请求结果

// ==================== 鉴权 ====================
function getToken() {
    return localStorage.getItem('access_token') || '';
}

// 未登录则跳回主页登录
if (!getToken()) {
    window.location.href = '/';
}

// ==================== 工具函数 ====================
function $(id) { return document.getElementById(id); }

function onSliderChange() {
    $('num-users-val').textContent = $('num-users').value;
}

function showToast(msg, type) {
    const t = $('toast');
    t.textContent = msg;
    t.className = 'toast toast-' + type + ' show';
    setTimeout(() => { t.className = 'toast'; }, 3000);
}

function escHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

// ==================== 百分位计算 ====================
function percentile(sorted, p) {
    if (!sorted.length) return 0;
    var k = (sorted.length - 1) * p / 100;
    var f = Math.floor(k), c = k - f;
    if (f + 1 < sorted.length) return sorted[f] * (1 - c) + sorted[f + 1] * c;
    return sorted[f];
}

// ==================== 实时统计 ====================
function computeLiveStats() {
    var success = liveResults.filter(function(r) { return r.status_code === 200; });
    var lats = success.map(function(r) { return r.elapsed_ms; }).sort(function(a, b) { return a - b; });
    return {
        completed: liveResults.length,
        success: success.length,
        errors: liveResults.length - success.length,
        avg: lats.length ? Math.round(lats.reduce(function(a, b) { return a + b; }, 0) / lats.length) : 0,
        p50: percentile(lats, 50),
        p75: percentile(lats, 75),
        p90: percentile(lats, 90),
        p95: percentile(lats, 95),
        p99: percentile(lats, 99),
        lats: lats,
    };
}

// ==================== 压测执行 ====================
async function startBenchmark() {
    if (running) return;
    running = true;
    liveResults = [];

    var btn = $('btn-run');
    btn.textContent = '⏳ 压测中...';
    btn.classList.add('running');
    btn.disabled = true;

    var numUsers = parseInt($('num-users').value);
    var rawMessages = $('messages').value.split('\n').filter(function(m) { return m.trim(); });
    if (rawMessages.length === 0) { showToast('请至少输入一条提问内容', 'error'); resetButton(); return; }

    // 显示进度
    $('progress-info').classList.add('show');
    $('progress-text').textContent = '0 / ' + numUsers + ' 已完成...';
    $('stats-row').style.display = '';
    $('charts-row').style.display = '';
    $('error-list').classList.remove('show');
    // 初始空图表
    renderPercentileChart({p50_ms:0,p75_ms:0,p90_ms:0,p95_ms:0,p99_ms:0});
    renderScatterChart([]);

    try {
        var res = await fetch('/admin/benchmark/stream', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + getToken(),
            },
            body: JSON.stringify({ num_users: numUsers, messages: rawMessages }),
        });

        if (!res.ok) {
            var err = await res.json().catch(function() { return {}; });
            throw new Error(err.detail || '请求失败 (' + res.status + ')');
        }

        // 流式读取 NDJSON（一行一个 JSON 事件）
        var reader = res.body.getReader();
        var decoder = new TextDecoder();
        var buffer = '';

        while (true) {
            var chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });

            var lines = buffer.split('\n');
            buffer = lines.pop();

            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (!line) continue;
                try {
                    var event = JSON.parse(line);
                    if (event.type === 'tick') {
                        liveResults.push(event.result);
                        updateLiveStats(event.completed, event.total);
                    } else if (event.type === 'summary') {
                        renderFinalResults(event);
                    }
                } catch(e) { console.error('解析事件失败:', e, line); }
            }
        }
    } catch (e) {
        $('progress-info').classList.remove('show');
        showToast('压测失败: ' + e.message, 'error');
    }

    resetButton();
}

// ==================== 实时更新 ====================
function updateLiveStats(completed, total) {
    var stats = computeLiveStats();
    $('stat-qps').textContent = '...';
    $('stat-avg').textContent = stats.avg;
    $('stat-rate').textContent = (completed > 0 ? (stats.success / completed * 100).toFixed(1) : '0') + '%';
    $('stat-total').textContent = '...';
    $('progress-text').textContent = completed + ' / ' + total + ' 已完成  |  当前平均 ' + stats.avg + 'ms';

    renderPercentileChart(stats);
    renderScatterChart(liveResults);
}

function renderFinalResults(data) {
    $('progress-text').textContent = '压测完成！' + data.total_requests + ' 请求，耗时 ' + (data.total_duration_ms / 1000).toFixed(1) + ' 秒';
    setTimeout(function() { $('progress-info').classList.remove('show'); }, 4000);

    $('stat-qps').textContent = data.qps;
    $('stat-avg').textContent = data.latency.avg_ms;
    $('stat-rate').textContent = data.total_requests > 0
        ? (data.success_count / data.total_requests * 100).toFixed(1) + '%'
        : '--';
    if (data.error_count > 0) {
        $('stat-rate').classList.add('danger');
    } else {
        $('stat-rate').classList.remove('danger');
    }
    $('stat-total').textContent = (data.total_duration_ms / 1000).toFixed(1);

    renderPercentileChart(data.latency);
    renderScatterChart(data.per_request);

    if (data.errors && data.errors.length > 0) {
        $('error-items').innerHTML = data.errors.map(function(e) { return '<div class="err-item">' + escHtml(e) + '</div>'; }).join('');
        $('error-list').classList.add('show');
    } else {
        $('error-list').classList.remove('show');
    }

    showToast('压测完成！' + data.success_count + ' 成功, QPS=' + data.qps, 'success');
}

function resetButton() {
    running = false;
    var btn = $('btn-run');
    btn.textContent = '🚀 开始压测';
    btn.classList.remove('running');
    btn.disabled = false;
}

// ==================== 图表渲染 ====================
function renderPercentileChart(lat) {
    var ctx = $('chart-percentile').getContext('2d');
    if (charts.percentile) charts.percentile.destroy();

    var labels = ['P50', 'P75', 'P90', 'P95', 'P99'];
    var values = [lat.p50_ms, lat.p75_ms, lat.p90_ms, lat.p95_ms, lat.p99_ms];
    var colors = values.map(function(v) {
        if (!v) return 'rgba(200,200,200,0.7)';
        if (v < 2000) return 'rgba(39,174,96,0.7)';
        if (v < 5000) return 'rgba(243,156,18,0.7)';
        return 'rgba(231,76,60,0.7)';
    });

    charts.percentile = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: '延迟 (ms)',
                data: values,
                backgroundColor: colors,
                borderRadius: 6,
            }],
        },
        options: {
            responsive: true,
            animation: { duration: 200 },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(ctx) { return ctx.raw + ' ms' + (ctx.raw >= 5000 ? ' ⚠️ 偏慢' : ctx.raw >= 2000 ? ' 一般' : ''); },
                    },
                },
            },
            scales: {
                y: { beginAtZero: true, title: { display: true, text: '毫秒 (ms)' } },
            },
        },
    });
}

function renderScatterChart(requests) {
    var ctx = $('chart-scatter').getContext('2d');
    if (charts.scatter) charts.scatter.destroy();

    var points = requests
        .filter(function(r) { return r.status_code === 200; })
        .map(function(r, i) { return { x: i + 1, y: r.elapsed_ms }; });

    charts.scatter = new Chart(ctx, {
        type: 'scatter',
        data: {
            datasets: [{
                label: '成功请求',
                data: points,
                backgroundColor: 'rgba(79,110,247,0.5)',
                borderColor: 'rgba(79,110,247,0.8)',
                pointRadius: 4,
                pointHoverRadius: 7,
            }],
        },
        options: {
            responsive: true,
            animation: { duration: 150 },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: function(ctx) { return '请求 #' + ctx.raw.x + ': ' + ctx.raw.y + ' ms'; },
                    },
                },
            },
            scales: {
                x: { title: { display: true, text: '请求序号（按完成顺序）' } },
                y: { beginAtZero: true, title: { display: true, text: '延迟 (ms)' } },
            },
        },
    });
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
