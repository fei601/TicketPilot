"""
SQLite 数据库操作

提供订单、客户等数据的 CRUD 操作。
"""

import sqlite3
from datetime import datetime
from pathlib import Path

import config
from ticketpilot.data.models import Order, OrderStatus


class Database:
    """SQLite 数据库管理"""

    def __init__(self, db_path: str | Path | None = None):
        # 默认路径锚定项目根目录（config.BASE_DIR），与启动时 CWD 无关
        db_path = db_path if db_path is not None else config.DATABASE_PATH
        self.db_path = Path(db_path)
        self._is_memory = str(db_path) == ":memory:"
        self._conn = None

        if self._is_memory:
            # 内存数据库使用单一连接
            self._conn = sqlite3.connect(":memory:")
            self._conn.row_factory = sqlite3.Row
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        if self._is_memory:
            return self._conn
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _close_conn(self, conn: sqlite3.Connection):
        """关闭连接（内存数据库不关闭）"""
        if not self._is_memory:
            conn.close()

    def _init_db(self):
        """初始化数据库表"""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL,
                event_name TEXT NOT NULL,
                event_date TEXT,
                platform TEXT,
                ticket_type TEXT,
                quantity INTEGER DEFAULT 1,
                seats TEXT,
                budget TEXT,
                notes TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()

    def add_order(self, order: Order) -> int:
        """添加订单，返回订单 ID"""
        now = datetime.now().isoformat()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                INSERT INTO orders (
                    customer_name, event_name, event_date, platform,
                    ticket_type, quantity, seats, budget, notes,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.customer_name,
                    order.event_name,
                    order.event_date,
                    order.platform,
                    order.ticket_type,
                    order.quantity,
                    order.seats,
                    order.budget,
                    order.notes,
                    order.status.value,
                    now,
                    now,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            self._close_conn(conn)

    def get_order(self, order_id: int) -> Order | None:
        """根据 ID 获取订单"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            if row:
                return Order(
                    id=row["id"],
                    customer_name=row["customer_name"],
                    event_name=row["event_name"],
                    event_date=row["event_date"],
                    platform=row["platform"],
                    ticket_type=row["ticket_type"],
                    quantity=row["quantity"],
                    seats=row["seats"],
                    budget=row["budget"],
                    notes=row["notes"],
                    status=OrderStatus(row["status"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
            return None
        finally:
            self._close_conn(conn)

    def get_all_orders(self, status: OrderStatus | None = None) -> list[Order]:
        """获取所有订单，可按状态筛选"""
        conn = self._get_conn()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM orders WHERE status = ? ORDER BY created_at ASC",
                    (status.value,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM orders ORDER BY created_at ASC"
                ).fetchall()

            return [
                Order(
                    id=row["id"],
                    customer_name=row["customer_name"],
                    event_name=row["event_name"],
                    event_date=row["event_date"],
                    platform=row["platform"],
                    ticket_type=row["ticket_type"],
                    quantity=row["quantity"],
                    seats=row["seats"],
                    budget=row["budget"],
                    notes=row["notes"],
                    status=OrderStatus(row["status"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
                for row in rows
            ]
        finally:
            self._close_conn(conn)

    def update_order_status(self, order_id: int, status: OrderStatus) -> bool:
        """更新订单状态"""
        now = datetime.now().isoformat()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, now, order_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            self._close_conn(conn)

    def update_order(self, order_id: int, **kwargs) -> bool:
        """
        更新订单的指定字段。

        Args:
            order_id: 订单 ID
            **kwargs: 要更新的字段，如 event_name="新名称", ticket_type="1680"

        Returns:
            更新成功返回 True，订单不存在返回 False
        """
        allowed_fields = {
            "customer_name", "event_name", "event_date", "platform",
            "ticket_type", "quantity", "seats", "budget", "notes"
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields and v is not None}
        if not updates:
            return False

        now = datetime.now().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [now, order_id]

        conn = self._get_conn()
        try:
            cursor = conn.execute(
                f"UPDATE orders SET {set_clause}, updated_at = ? WHERE id = ?",
                values,
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            self._close_conn(conn)

    def delete_order(self, order_id: int) -> bool:
        """删除订单"""
        conn = self._get_conn()
        try:
            cursor = conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            self._close_conn(conn)
