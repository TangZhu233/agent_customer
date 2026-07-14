"""
LangChain Agent：绑定 DeepSeek 模型 + 业务工具。
Agent 启动时初始化一次，后续所有请求共用同一个实例。
"""
from langchain_deepseek import ChatDeepSeek
from langgraph.prebuilt import create_react_agent
from config.settings import settings
from app.tools import TOOLS

# 全局 Agent 实例（启动时创建，复用）
_agent = None


def get_agent():
    """获取或创建 Agent 单例"""
    global _agent
    if _agent is None:
        # 1. 创建 DeepSeek 聊天模型
        llm = ChatDeepSeek(
            model_name=settings.DEEPSEEK_MODEL,
            api_key=settings.DEEPSEEK_API_KEY,
            api_base=settings.DEEPSEEK_BASE_URL,
            temperature=0.3,  # 客服场景，低温度保证稳定输出
        )
        # 2. 创建 ReAct Agent（模型 + 工具）
        _agent = create_react_agent(llm, TOOLS)
    return _agent


async def chat(message: str) -> str:
    """发送消息给 Agent，返回文本回复"""
    agent = get_agent()
    # LangGraph ReAct agent 用 invoke 发消息
    result = await agent.ainvoke({"messages": [("user", message)]})
    # 取最后一条 AI 消息作为回复
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "content") and msg.type == "ai":
            return msg.content
    return "抱歉，我暂时无法处理你的请求。"
