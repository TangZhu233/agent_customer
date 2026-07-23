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

- 熔断器为什么包裹 tenacity 重试链而不是替代它？
  重试和熔断解决不同层次的问题：tenacity 处理偶发网络抖动（2s/4s/8s 指数退避），
  熔断器处理持续性故障（连续 5 次 RetryError → 快速失败 30 秒）。
  关键是：tenacity 的 RetryError（3 次全失败）才算熔断器的 1 次失败，
  避免单次网络抖动触发熔断。
"""
import asyncio
import contextvars
from langchain_deepseek import ChatDeepSeek
from langgraph.prebuilt import create_react_agent
from langgraph.errors import GraphRecursionError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError,
)
from config.settings import settings
from app.tools import TOOLS, clear_citations, get_citations, set_current_user
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

## 隐私保护规则（重要！）
- 当前登录用户只能直接查看**自己**名下的订单列表和物流信息
- 如果用户提供他人的手机号来查询，你必须先调用 lookup_user_by_phone 锁定目标用户，然后告知用户"需要提供该手机号对应订单的订单编号作为验证"
- 用户提供订单编号后，调用 lookup_order_by_no —— **该工具会自动校验订单是否归属手机号对应的用户**，如果不匹配会返回错误提示
- 必须确保用户提供的手机号和订单编号属于同一个人（工具层自动校验，你只需按顺序调用即可）
- 如果工具返回"不匹配"，请如实告知用户并要求核实信息
- 如果用户直接提供订单编号（没有先给手机号），则直接调用 lookup_order_by_no 即可，订单编号本身即为有效凭证
- 简单说：查自己 → 手机号足够；查别人 → 手机号 + 匹配的订单编号，缺一不可

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
- 产品信息（面料特性/新品系列/选购指南/具体SKU参数/尺码范围/价格区间/颜色选项）
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
    result = await agent.ainvoke(
        {"messages": messages},
        config={"recursion_limit": 10},  # 防止死循环
    )
    elapsed = round((time.perf_counter() - start) * 1000, 2)
    llm_log.info("LLM 调用完成: %.2fms model=%s", elapsed, settings.DEEPSEEK_MODEL)
    return result


# ==================== 公开接口 ====================

async def _build_messages(message: str, user_id: int | None, session_id: int | None) -> list:
    """构建发送给 Agent 的消息列表（供 chat 和 chat_stream 共用）

    两阶段截断策略：
    1. 消息数截断 — 保留最近 N 条（可配置，默认 20）
    2. 字符数安全网 — 若总字符数超过阈值，从最早消息开始丢弃
       （始终保留 system prompt 和当前用户消息）
    """
    messages = [("system", SYSTEM_PROMPT)]

    if user_id is not None and session_id is not None:
        try:
            from app.database import get_session_messages
            max_msgs = settings.MAX_HISTORY_MESSAGES
            history = get_session_messages(session_id, user_id, limit=max_msgs)

            for msg in history:
                role = "assistant" if msg["role"] == "assistant" else "user"
                messages.append((role, msg["content"]))
        except Exception:
            pass

    messages.append(("user", message))

    # 阶段 B：字符数安全网 — 用 len() 估算 token 量
    # 中文字符 ≈ 1~2 token，英文字符 ≈ 0.25 token。len() 是保守估计（偏高），
    # 会提前触发裁剪而非触顶才裁，这是安全方向。
    char_limit = settings.MAX_HISTORY_CHAR_LIMIT
    total_chars = sum(len(text) for _, text in messages)

    if total_chars > char_limit:
        before_count = len(messages) - 2  # 历史消息数（排除 system + 当前用户）
        reserve_chars = len(SYSTEM_PROMPT) + len(message)
        available = char_limit - reserve_chars
        kept_history = []
        for role, text in reversed(messages[1:-1]):
            if available - len(text) >= 0:
                kept_history.insert(0, (role, text))
                available -= len(text)
            else:
                break
        messages = [messages[0]] + kept_history + [messages[-1]]
        after_count = len(kept_history)
        after_chars = sum(len(t) for _, t in messages)
        llm_log.warning(
            "历史消息触发字符数安全裁剪: %d 条 → %d 条, 预估字符 %d → %d (阈值 %d)",
            before_count, after_count, total_chars, after_chars, char_limit,
        )

    return messages


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
    # 1. 清空上次请求的引用缓存（ContextVar 隔离）
    clear_citations()

    # 1.5. 设置当前用户上下文（供工具函数隐私校验使用）
    set_current_user(user_id)

    # 2. 构建消息列表
    messages = await _build_messages(message, user_id, session_id)

    # 3. 熔断器检查（在 LLM 调用前）
    if settings.CB_ENABLED:
        from app.circuit_breaker import get_circuit_breaker
        cb = get_circuit_breaker()
        if not await cb.allow_request():
            llm_log.warning("熔断器已断开，快速失败")
            return (
                "抱歉，AI 服务暂时不可用（已触发熔断保护），请稍后重试或联系人工客服。",
                None,
            )

    # 4. 获取 Agent
    agent = get_agent(enable_rag=enable_rag)

    # 5. LLM 调用（含超时 + 熔断记录）
    try:
        if settings.LLM_REQUEST_TIMEOUT > 0:
            result = await asyncio.wait_for(
                _invoke_agent(agent, messages),
                timeout=settings.LLM_REQUEST_TIMEOUT,
            )
        else:
            result = await _invoke_agent(agent, messages)

        # 成功 → 通知熔断器
        if settings.CB_ENABLED:
            await cb.record_success()

    except asyncio.TimeoutError:
        llm_log.error("LLM 调用超时 (%.1fs)", settings.LLM_REQUEST_TIMEOUT)
        if settings.CB_ENABLED:
            await cb.record_failure()
        return "抱歉，AI 服务响应超时，请简化问题后重试。", None

    except GraphRecursionError:
        llm_log.warning("Agent 递归超限，强制终止（recursion_limit=%d）", 10)
        if settings.CB_ENABLED:
            await cb.record_failure()
        return "抱歉，处理您的请求时步骤过多，请简化问题后重试。", None

    except RetryError as e:
        llm_log.error("LLM 调用全部重试失败: %s", e)
        if settings.CB_ENABLED:
            await cb.record_failure()
        return "抱歉，AI 服务暂时不可用，请稍后重试。如急需帮助请联系人工客服。", None

    except Exception as e:
        llm_log.error("Agent 调用异常: %s", e)
        if settings.CB_ENABLED:
            await cb.record_failure()
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


async def chat_stream(
    message: str,
    user_id: int | None = None,
    session_id: int | None = None,
    enable_rag: bool = True,
):
    """
    流式对话：逐 token 推送 LLM 输出，用户感知延迟从 9 秒降到 1 秒。

    面试知识点：
    - 为什么使用 astream_events 而不是 ainvoke？
      ainvoke 等 LLM 全部生成完才返回（用户干等 9 秒）。
      astream_events 每生成一个 token 就 push 一次（用户 1 秒后开始阅读）。

    - ReAct Agent 至少 2 次 LLM 调用（思考→工具→回答），如何处理？
      只流式推送「最后一次」LLM 调用的 token（最终回答），
      跳过的第一次是工具调用 JSON（用户不需要看到内部推理过程）。

    - 检测"最后一次"的方法：按 run_id 分组，新 run_id 出现时 discard 旧内容，
      始终只推送最新 run_id 的 token。
    """
    import time

    # 1. 清空 ContextVar
    clear_citations()
    set_current_user(user_id)

    # 2. 构建消息
    messages = await _build_messages(message, user_id, session_id)

    # 3. 熔断器检查
    if settings.CB_ENABLED:
        from app.circuit_breaker import get_circuit_breaker
        cb = get_circuit_breaker()
        if not await cb.allow_request():
            llm_log.warning("熔断器已断开（流式），快速失败")
            yield {"type": "error", "message": "AI 服务暂时不可用（已触发熔断保护），请稍后重试。"}
            return
    agent = get_agent(enable_rag=enable_rag)

    # 流式状态追踪
    run_contents = {}   # run_id → 累积文本
    run_order = []      # LLM 调用顺序

    stream_start = time.perf_counter()
    stream_success = False

    try:
        async for event in agent.astream_events(
            {"messages": messages},
            config={"recursion_limit": 10},
            version="v2",
        ):
            if event["event"] != "on_chat_model_stream":
                continue

            chunk = event["data"]["chunk"]
            content = chunk.content
            if not content:
                continue

            run_id = event.get("run_id", "unknown")

            # 新 LLM 调用开始 → 清空前端之前显示的中间推理
            if run_id not in run_contents:
                run_contents[run_id] = ""
                run_order.append(run_id)
                if len(run_order) > 1:
                    yield {"type": "clear"}

            run_contents[run_id] += content

            # 只推送「最新一次」LLM 调用的 token（最终回答）
            if run_id == run_order[-1]:
                yield {"type": "token", "content": content}

        stream_success = True

    except GraphRecursionError:
        llm_log.warning("Agent 递归超限（流式），强制终止")
        if settings.CB_ENABLED:
            await cb.record_failure()
        yield {"type": "error", "message": "处理步骤过多，请简化问题后重试。"}
        return
    except RetryError:
        llm_log.error("流式 LLM 调用全部重试失败")
        if settings.CB_ENABLED:
            await cb.record_failure()
        yield {"type": "error", "message": "AI 服务暂时不可用，请稍后重试。"}
        return
    except Exception as e:
        llm_log.error("流式调用异常: %s", e)
        if settings.CB_ENABLED:
            await cb.record_failure()
        yield {"type": "error", "message": f"处理请求出错: {str(e)[:100]}"}
        return

    # 成功 → 通知熔断器
    if stream_success and settings.CB_ENABLED:
        await cb.record_success()

    # 收集引用
    citations = get_citations()
    citations_out = citations if citations else None

    elapsed = round((time.perf_counter() - stream_start) * 1000, 2)
    llm_log.info("LLM 流式调用完成: %.2fms model=%s", elapsed, settings.DEEPSEEK_MODEL)

    yield {"type": "done", "citations": citations_out}
