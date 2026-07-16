"""
Agent 工具函数：每一个函数 = Agent 可以调用的一项能力。
LangChain 会把函数名和文档字符串自动转成工具的 name + description，
DeepSeek 会根据用户问题自动决定调用哪个工具。

面试知识点：
- ContextVar 是 Python 3.7+ 标准库提供的协程/线程安全变量存储机制，
  每个异步任务有独立的上下文副本，互不干扰。
  在 FastAPI 的 async/await 并发模型下，不能用全局变量或 threading.local
  来存储请求级状态（多个请求共享同一线程），所以 ContextVar 是正确的选择。
"""
import contextvars
from app.database import (
    query_user_by_phone,
    query_user_by_id,
    query_orders_by_user_id,
    query_order_by_no,
    query_logistics_by_order_id,
)

# ContextVar：在每个异步请求上下文中独立存储 RAG 引用
_rag_citations: contextvars.ContextVar[list[dict]] = contextvars.ContextVar(
    "rag_citations", default=[]
)


def get_citations() -> list[dict]:
    """获取当前请求上下文中收集的 RAG 引用（由 agent.py 在请求结束后调用）"""
    return _rag_citations.get()


def clear_citations() -> None:
    """清空当前请求的引用缓存（每次新请求开始时调用）"""
    _rag_citations.set([])


# ==================== 原有工具 ====================

def lookup_user_by_phone(phone: str) -> str:
    """根据手机号查询用户信息。当你需要查找某个手机号对应的用户时调用。"""
    user = query_user_by_phone(phone)
    if not user:
        return f"未找到手机号为 {phone} 的用户。"
    return (
        f"用户信息：姓名={user['name']}，手机={user['phone']}，"
        f"邮箱={user['email']}，注册时间={user['created_at']}"
    )


def lookup_orders_by_user_id(user_id: int) -> str:
    """查询某用户的所有订单。当用户问"我的订单"或需要查某人订单时调用。"""
    orders = query_orders_by_user_id(user_id)
    if not orders:
        return f"用户 ID {user_id} 暂无订单记录。"
    lines = [f"用户 ID {user_id} 的订单（共{len(orders)}条）："]
    for o in orders:
        lines.append(
            f"  · 订单号={o['order_no']}，商品={o['product_name']}，"
            f"金额={o['amount']}元，状态={o['status']}"
        )
    return "\n".join(lines)


def lookup_order_by_no(order_no: str) -> str:
    """根据订单号精确查询订单详情。当用户提供具体订单号时调用。"""
    order = query_order_by_no(order_no)
    if not order:
        return f"未找到订单号为 {order_no} 的订单。"
    return (
        f"订单详情：订单号={order['order_no']}，商品={order['product_name']}，"
        f"金额={order['amount']}元，状态={order['status']}，"
        f"下单时间={order['created_at']}"
    )


def lookup_logistics(order_id: int) -> str:
    """查询订单的物流信息。当用户问"物流到哪了"或"快递进度"时调用。需要先查到订单ID。"""
    logistics = query_logistics_by_order_id(order_id)
    if not logistics:
        return f"订单 ID {order_id} 暂无物流信息。"
    return (
        f"物流信息：快递单号={logistics['tracking_no']}，"
        f"承运商={logistics['carrier']}，状态={logistics['status']}，"
        f"最新进展={logistics['updates']}"
    )


# ==================== RAG 新增工具 ====================

def search_knowledge_base(query: str, category: str = "全部", gender: str = "全部") -> str:
    """
    搜索服装知识库。当用户咨询服装相关问题（尺码、颜色搭配、面料洗涤保养、
    产品信息、售后政策等）时，必须先调用此工具检索知识库，再基于检索结果回答。
    参数 category 可选值：尺码指南、颜色搭配、洗涤保养、产品信息、售后政策。
    参数 gender 可选值：男、女、通用、儿童、全部。当用户明确指定性别（如"推荐女装"）
    时务必传入 gender 参数以缩小检索范围，提高准确率。
    """
    from app.rag import search_similar

    cat = None if category in ("全部", "所有", "") else category
    gen = None if gender in ("全部", "所有", "") else gender
    results = search_similar(query, category=cat, gender=gen)

    if not results:
        return "知识库中暂无与您问题直接相关的信息。请尝试换个方式提问，或联系人工客服。"

    # 格式化检索结果
    lines = [f"知识库检索结果（共{len(results)}条）："]
    for i, r in enumerate(results, 1):
        gender_tag = f"[{r.get('gender', '通用')}]" if r.get('gender') else ""
        lines.append(
            f"{i}. {gender_tag} [{r['category']}] {r['title']}\n"
            f"   {r['content'][:300]}"
        )

    # 将检索结果存入 ContextVar，供 agent.py 构建 citations
    citations = _rag_citations.get()
    citations.extend([
        {
            "title": r["title"],
            "snippet": r["content"][:150] + ("..." if len(r["content"]) > 150 else ""),
            "category": r["category"],
        }
        for r in results
    ])
    _rag_citations.set(citations)

    return "\n".join(lines)


def recommend_size(height: float, weight: float, gender: str = "", clothing_type: str = "上衣") -> str:
    """
    根据用户的身高(cm)、体重(kg)、性别、服装类型推荐尺码。
    当用户提供身高体重并询问尺码时，调用此工具获取推荐。
    内部会搜索知识库中的尺码指南进行匹配。
    如果用户未提供性别或性别不明确（空字符串），会分别检索男装、女装和通用尺码建议。
    """
    from app.rag import search_similar

    # 存储引用
    citations = _rag_citations.get()

    # 判断是否指定了明确性别
    known_gender = gender in ("男", "女")

    if known_gender:
        # 明确性别：精准检索
        search_query = f"{clothing_type} 尺码 {gender} 身高{int(height)} 体重{int(weight)}"
        results = search_similar(search_query, category="尺码指南", gender=gender)

        if not results:
            return (
                f"根据您提供的信息（{gender}性，身高{int(height)}cm，体重{int(weight)}kg），"
                f"未能从知识库中匹配到精确的{clothing_type}尺码数据。"
                f"建议您参考以下通用建议：\n"
                f"- 身高{int(height)}cm 体重{int(weight)}kg 的{gender}性通常适合"
                f"{'M-L码' if weight >= 60 else 'S-M码'}范围\n"
                f"- 建议结合胸围/腰围实际测量数据选择\n"
                f"- 可联系人工客服获取一对一尺码指导"
            )

        # 收集引用
        citations.extend([
            {
                "title": r["title"],
                "snippet": r["content"][:150] + ("..." if len(r["content"]) > 150 else ""),
                "category": r["category"],
            }
            for r in results
        ])

        lines = [
            f"根据您提供的信息（{gender}性，身高{int(height)}cm，体重{int(weight)}kg），"
            f"为您检索到以下{clothing_type}尺码参考：\n"
        ]
        for i, r in enumerate(results, 1):
            lines.append(f"参考{i}：[{r['category']}] {r['title']}\n{r['content']}\n")
        lines.append("请结合您的实际体型（如偏瘦/偏胖）和个人喜好（修身/宽松）微调。如有疑问欢迎继续咨询！")
    else:
        # 未知性别：分别检索男装、女装、通用尺码
        search_query = f"{clothing_type} 尺码 身高{int(height)} 体重{int(weight)}"
        male_results = search_similar(search_query, category="尺码指南", gender="男")
        female_results = search_similar(search_query, category="尺码指南", gender="女")
        general_results = search_similar(search_query, category="尺码指南", gender="通用")

        # 收集引用
        all_results = male_results + female_results + general_results
        citations.extend([
            {
                "title": r["title"],
                "snippet": r["content"][:150] + ("..." if len(r["content"]) > 150 else ""),
                "category": r["category"],
            }
            for r in all_results
        ])

        lines = [
            f"根据您提供的信息（身高{int(height)}cm，体重{int(weight)}kg），"
            f"由于未指定性别，为您分别列出{clothing_type}尺码参考：\n"
        ]

        if male_results:
            lines.append("─── 👨 男装推荐 ───")
            for i, r in enumerate(male_results, 1):
                lines.append(f"  {i}. {r['title']}\n     {r['content'][:200]}")
            lines.append("")

        if female_results:
            lines.append("─── 👩 女装推荐 ───")
            for i, r in enumerate(female_results, 1):
                lines.append(f"  {i}. {r['title']}\n     {r['content'][:200]}")
            lines.append("")

        if general_results:
            lines.append("─── 🔄 通用推荐 ───")
            for i, r in enumerate(general_results, 1):
                lines.append(f"  {i}. {r['title']}\n     {r['content'][:200]}")
            lines.append("")

        if not all_results:
            lines.append("未能从知识库中匹配到精确的尺码数据。建议结合胸围/腰围实际测量数据选择，或联系人工客服获取一对一指导。")
        else:
            lines.append("请根据您的实际性别和体型（如偏瘦/偏胖）微调选择。如有疑问欢迎继续咨询！")

    _rag_citations.set(citations)
    return "\n".join(lines)


# ==================== 工具列表 ====================
# Agent 初始化时注册这些工具
TOOLS = [
    lookup_user_by_phone,
    lookup_orders_by_user_id,
    lookup_order_by_no,
    lookup_logistics,
    search_knowledge_base,
    recommend_size,
]
