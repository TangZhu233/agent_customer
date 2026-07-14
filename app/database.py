"""
SQLite 数据库连接与基础查询封装。
所有 SQL 操作都走这个模块，方便后续切换数据库。
"""
import sqlite3
from config.settings import settings


def get_connection() -> sqlite3.Connection:
    """获取数据库连接（自动创建 data 目录）"""
    import os
    os.makedirs(os.path.dirname(settings.DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(settings.DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # 让查询结果支持字典式访问 row["name"]
    return conn


# ---------- 用户查询 ----------

def query_user_by_phone(phone: str) -> dict | None:
    """根据手机号查询用户"""
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else None


def query_user_by_id(user_id: int) -> dict | None:
    """根据用户ID查询用户"""
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------- 订单查询 ----------

def query_orders_by_user_id(user_id: int) -> list[dict]:
    """根据用户ID查所有订单"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_order_by_no(order_no: str) -> dict | None:
    """根据订单号精确查询"""
    conn = get_connection()
    row = conn.execute("SELECT * FROM orders WHERE order_no = ?", (order_no,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------- 物流查询 ----------

def query_logistics_by_order_id(order_id: int) -> dict | None:
    """根据订单ID查物流信息"""
    conn = get_connection()
    row = conn.execute("SELECT * FROM logistics WHERE order_id = ?", (order_id,)).fetchone()
    conn.close()
    return dict(row) if row else None
