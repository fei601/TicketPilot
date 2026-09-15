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
