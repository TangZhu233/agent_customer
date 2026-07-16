"""
LangChain Agent：绑定 DeepSeek 模型 + 业务工具 + RAG 知识检索。
Agent 启动时初始化一次，后续所有请求共用同一个实例。

面试知识点：
- 为什么用 tenacity 做重试而不是手动 while 循环？
  tenacity 是 Python 生态最成熟的 retry 库，提供开箱即用的指数退避 (exponential backoff)、
  最大重试次数、可重试异常类型过滤等功能。手动 while 循环容易遗漏边界条件
  （如连接超时 vs 业务错误需区别对待），且退避计算容易写错。

- 为什么 RetryError 要单独 catch？
  tenacity 在所有重试耗尽后抛出 RetryError，此时给用户返回"服务暂时不可用"
  比让 FastAPI 抛出 500 Internal Server Error 体验好得多。这是企业级 API 的
  基本素养——永远不要让用户看到堆栈跟踪。

- 为什么用 ContextVar 而不是全局变量传递引用？
  FastAPI 的 async/await 是协程并发，多个请求在同一个线程的事件循环中交替执行。
  如果使用全局变量，请求 A 可能读到请求 B 存入的引用数据（数据串扰）。
  ContextVar 是 Python 标准库为 async/await 设计的协程安全存储，每个
  协程任务有独立副本。类比：全局变量=公共白板，ContextVar=每人一张便签纸。
"""
import contextvars
from langchain_deepseek import ChatDeepSeek
from langgraph.prebuilt import create_react_agent
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError,
)
from config.settings import settings
from app.tools import TOOLS, clear_citations, get_citations
from app.logger import get_logger

llm_log = get_logger("llm")

# ==================== 系统提示词 ====================
# 注入 Agent 角色认知、工具使用规则、知识库检索规范

SYSTEM_PROMPT = """你是一个专业的服装电商智能客服助手，服务于一家服装品牌官方商城。你的职责包括：

## 核心能力
1. **订单/物流查询** — 使用工具查询用户订单状态、物流进度等信息
2. **服装知识问答** — 使用 search_knowledge_base 工具搜索知识库，回答尺码、颜色搭配、面料特性、洗涤保养等问题
3. **尺码推荐** — 当用户提供身高/体重信息时，使用 recommend_size 工具给出精准尺码建议

## 重要规则
- 当用户询问服装知识（尺码、颜色、面料、洗涤、售后政策等）时，**必须先调用 search_knowledge_base 搜索知识库**，再基于检索结果回答
- 回答时请引用知识库中的具体信息，注明来源
- 如果知识库中没有相关信息，诚实告知用户并提供通用建议，不要编造信息
- 用户提供身高体重数据时，优先使用 recommend_size 工具
- 用户询问订单/物流时，使用对应的查询工具
- 回复风格：专业、亲切、简洁，使用中文

## 性别参数使用规则（重要！）
- search_knowledge_base 和 recommend_size 工具都支持 `gender` 参数（可选值：男/女/通用/儿童）
- 当用户明确提到性别时（如"推荐女装""我是男生""给老婆买"），务必传入对应的 gender 参数以缩小检索范围
- 当用户未明确性别时：
  - recommend_size 的 gender 留空（默认空字符串），工具会自动分别检索男/女/通用三类推荐
  - search_knowledge_base 的 gender 传"全部"，返回所有性别适用结果
- 搭配/洗涤/面料/售后类问题一般不区分性别，gender 传"通用"或"全部"

## 知识库覆盖范围
- 尺码指南（男装/女装/通用/童装/鞋码）
- 颜色搭配（肤色匹配/季节穿搭/职场配色）
- 洗涤保养（棉/羊毛/牛仔/真丝/污渍处理）
- 产品信息（面料特性/新品系列/选购指南）
- 售后政策（退换货/物流/FAQ）"""

# ==================== 全局 Agent 实例 ====================

_agent = None
_agent_with_rag = None  # 带 RAG 工具的 Agent
_agent_no_rag = None    # 不带 RAG 工具的 Agent（enable_rag=False 时使用）


def _build_llm():
    """创建 DeepSeek 聊天模型"""
    return ChatDeepSeek(
        model_name=settings.DEEPSEEK_MODEL,
        api_key=settings.DEEPSEEK_API_KEY,
        api_base=settings.DEEPSEEK_BASE_URL,
        temperature=0.3,
    )


def get_agent(enable_rag: bool = True):
    """获取或创建 Agent 单例。enable_rag=False 时仅使用原始 4 个工具。"""
    global _agent, _agent_with_rag, _agent_no_rag

    if enable_rag:
        if _agent_with_rag is None:
            _agent_with_rag = create_react_agent(_build_llm(), TOOLS)
        return _agent_with_rag
    else:
        if _agent_no_rag is None:
            # 仅原有 4 个工具（不含 RAG）
            tools_no_rag = TOOLS[:4]
            _agent_no_rag = create_react_agent(_build_llm(), tools_no_rag)
        return _agent_no_rag


# ==================== LLM 调用（含重试）====================

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((TimeoutError, ConnectionError, OSError)),
    before_sleep=lambda retry_state: llm_log.warning(
        "LLM 调用重试 %d/3: %s", retry_state.attempt_number, retry_state.outcome.exception()
    ),
)
async def _invoke_agent(agent, messages: list) -> dict:
    """调用 Agent，支持自动重试（指数退避：2s → 4s → 8s）"""
    import time
    start = time.perf_counter()
    result = await agent.ainvoke({"messages": messages})
    elapsed = round((time.perf_counter() - start) * 1000, 2)
    llm_log.info("LLM 调用完成: %.2fms model=%s", elapsed, settings.DEEPSEEK_MODEL)
    return result


# ==================== 公开接口 ====================

async def chat(
    message: str,
    user_id: int | None = None,
    session_id: int | None = None,
    enable_rag: bool = True,
) -> tuple[str, list[dict] | None]:
    """
    发送消息给 Agent，返回 (回复文本, 引用列表)。

    参数:
        message: 用户消息文本
        user_id: 已登录用户ID（可选）
        session_id: 会话ID（可选，加载历史上下文）
        enable_rag: 是否启用知识库检索

    面试知识点：
    - 为什么返回 tuple 而不是直接返回 ChatResponse？
      关注点分离：agent.py 是业务逻辑层，不应依赖 models.py（Web 层）。
      返回原始 tuple 让 main.py 负责封装成 ChatResponse，符合分层架构原则。
    """
    import time

    # 1. 清空上次请求的引用缓存（ContextVar 隔离）
    clear_citations()

    # 2. 构建消息列表
    messages = [("system", SYSTEM_PROMPT)]

    # 3. 加载会话历史（最多最近 10 轮）
    if user_id is not None and session_id is not None:
        try:
            from app.database import get_session_messages
            history = get_session_messages(session_id, user_id)
            for msg in history[-20:]:  # 最近 20 条 = 10 轮对话
                role = "assistant" if msg["role"] == "assistant" else "user"
                messages.append((role, msg["content"]))
        except Exception:
            pass  # 历史加载失败不影响本次对话

    # 4. 当前用户消息
    messages.append(("user", message))

    # 5. 获取 Agent 并调用
    agent = get_agent(enable_rag=enable_rag)

    try:
        result = await _invoke_agent(agent, messages)
    except RetryError as e:
        llm_log.error("LLM 调用全部重试失败: %s", e)
        return "抱歉，AI 服务暂时不可用，请稍后重试。如急需帮助请联系人工客服。", None
    except Exception as e:
        llm_log.error("Agent 调用异常: %s", e)
        return "抱歉，处理您的请求时出现了问题，请稍后重试。", None

    # 6. 提取 AI 回复
    reply = "抱歉，我暂时无法处理你的请求。"
    for msg in reversed(result.get("messages", [])):
        if hasattr(msg, "content") and getattr(msg, "type", None) == "ai":
            reply = msg.content
            break

    # 7. 收集引用
    citations = get_citations()
    citations_out = citations if citations else None

    return reply, citations_out
