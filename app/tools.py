"""
Agent 工具函数：每一个函数 = Agent 可以调用的一项能力。
LangChain 会把函数名和文档字符串自动转成工具的 name + description，
DeepSeek 会根据用户问题自动决定调用哪个工具。
"""
from app.database import (
    query_user_by_phone,
    query_user_by_id,
    query_orders_by_user_id,
    query_order_by_no,
    query_logistics_by_order_id,
)


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


# 工具列表：Agent 初始化时注册这些工具
TOOLS = [lookup_user_by_phone, lookup_orders_by_user_id, lookup_order_by_no, lookup_logistics]
