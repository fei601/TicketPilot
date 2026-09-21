"""
数据库测试
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.data.database import Database
from ticketpilot.data.models import Order, OrderStatus


class TestDatabase:
    """数据库 CRUD 测试"""

    def setup_method(self):
        """每个测试前使用内存数据库"""
        self.db = Database(":memory:")

    def test_add_and_get_order(self):
        """测试添加和获取订单"""
        order = Order(
            customer_name="张三",
            event_name="周杰伦演唱会",
            ticket_type="看台A",
            quantity=2,
        )
        order_id = self.db.add_order(order)
        assert order_id > 0

        retrieved = self.db.get_order(order_id)
        assert retrieved is not None
        assert retrieved.customer_name == "张三"
        assert retrieved.event_name == "周杰伦演唱会"
        assert retrieved.quantity == 2

    def test_get_all_orders(self):
        """测试获取所有订单"""
        for i in range(3):
            self.db.add_order(Order(
                customer_name=f"客户{i}",
                event_name=f"演出{i}",
            ))

        orders = self.db.get_all_orders()
        assert len(orders) == 3

    def test_update_order_status(self):
        """测试更新订单状态"""
        order = Order(customer_name="测试", event_name="测试演出")
        order_id = self.db.add_order(order)

        success = self.db.update_order_status(order_id, OrderStatus.SUCCESS)
        assert success is True

        updated = self.db.get_order(order_id)
        assert updated.status == OrderStatus.SUCCESS

    def test_filter_by_status(self):
        """测试按状态筛选"""
        # 添加不同状态的订单
        o1 = Order(customer_name="A", event_name="演出A", status=OrderStatus.PENDING)
        o2 = Order(customer_name="B", event_name="演出B", status=OrderStatus.SUCCESS)
        self.db.add_order(o1)
        self.db.add_order(o2)

        pending = self.db.get_all_orders(OrderStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].customer_name == "A"

    def test_delete_order(self):
        """测试删除订单"""
        order = Order(customer_name="删除测试", event_name="测试")
        order_id = self.db.add_order(order)

        success = self.db.delete_order(order_id)
        assert success is True

        assert self.db.get_order(order_id) is None

    def test_confirmed_default_false_and_confirm(self):
        """新订单默认草稿；confirm_order 翻转且不触碰 status"""
        order_id = self.db.add_order(Order(customer_name="A", event_name="演出A"))
        assert self.db.get_order(order_id).confirmed is False

        assert self.db.confirm_order(order_id) is True
        retrieved = self.db.get_order(order_id)
        assert retrieved.confirmed is True
        assert retrieved.status == OrderStatus.PENDING  # 与抢票生命周期正交

    def test_confirm_missing_order_returns_false(self):
        assert self.db.confirm_order(999) is False


class TestConfirmedMigration:
    """v0.1 老库（无 confirmed 列）→ v0.2 的启动迁移"""

    def _make_legacy_db(self, path):
        """手工建一个 v0.1 schema 的库并塞一条存量订单"""
        import sqlite3
        from datetime import datetime

        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL, event_name TEXT NOT NULL,
                event_date TEXT, platform TEXT, ticket_type TEXT,
                quantity INTEGER DEFAULT 1, seats TEXT, budget TEXT, notes TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
        """)
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO orders (customer_name, event_name, created_at, updated_at)"
            " VALUES ('老客户', '老演出', ?, ?)", (now, now))
        conn.commit()
        conn.close()

    def test_legacy_rows_grandfathered_as_confirmed(self, tmp_path):
        """存量订单视为已确认：升级不该逼用户手工确认全部历史数据"""
        db_file = tmp_path / "legacy.db"
        self._make_legacy_db(db_file)

        db = Database(db_file)
        orders = db.get_all_orders()
        assert len(orders) == 1
        assert orders[0].confirmed is True

    def test_migration_idempotent_and_new_drafts_survive_reopen(self, tmp_path):
        """迁移只在缺列时执行一次；重开库不会把新草稿洗成已确认"""
        db_file = tmp_path / "legacy.db"
        self._make_legacy_db(db_file)

        Database(db_file)                      # 第一次打开：ALTER + grandfather
        db = Database(db_file)                 # 第二次打开：列已存在，不再迁移
        new_id = db.add_order(Order(customer_name="新客", event_name="新演出"))

        db2 = Database(db_file)                # 第三次打开
        assert db2.get_order(new_id).confirmed is False  # 新草稿未被洗白
        assert db2.get_all_orders()[0].confirmed is True  # 存量仍是已确认
