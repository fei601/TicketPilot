"""
order_parser 测试：脱敏格式化（纯逻辑，不打 LLM）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.data.models import Order
from ticketpilot.tools.order_parser import format_order_masked


class TestFormatOrderMasked:
    """format_order_masked 脱敏确认文本"""

    def _make_order(self, **kwargs) -> Order:
        base = dict(
            customer_name="张三、李四",
            event_name="上海周杰伦",
            event_date="1.1",
            ticket_type="1380",
            platform="全平台",
            seats="连坐",
            budget="13812345678",
            notes="张三 310101199001011234\n李四 31010119900101123X",
        )
        base.update(kwargs)
        return Order(**base)

    def test_full_format(self):
        text = format_order_masked(self._make_order(), order_id=7, idx=1)
        assert "订单 1（订单号：7）" in text
        assert "上海周杰伦 1.1 1380 连坐2张" in text
        assert "联系电话：138****5678" in text
        assert "平台：全平台" in text

    def test_id_card_masked(self):
        text = format_order_masked(self._make_order(), order_id=1)
        # 原始身份证号不得出现，必须是脱敏形式
        assert "310101199001011234" not in text
        assert "310***********1234" in text

    def test_pending_phone_not_starred(self):
        """budget='待补充' 不能被 mask_phone 打成 '***'（len!=11 守卫）"""
        text = format_order_masked(self._make_order(budget="待补充"), order_id=1)
        assert "联系电话：待补充" in text
        assert "联系电话：***" not in text

    def test_single_ticket(self):
        text = format_order_masked(
            self._make_order(seats=None, notes="张三 310101199001011234"),
            order_id=2,
        )
        assert "单张" in text
