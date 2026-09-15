"""
播报解析测试
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.data.broadcast_db import BroadcastDB


class TestBroadcastDB:
    """播报数据库测试"""

    def setup_method(self):
        """每个测试前使用内存数据库"""
        self.db = BroadcastDB(":memory:")

    def test_save_and_get_events(self):
        """测试保存和获取播报"""
        events = [
            {
                "event_name": "周杰伦演唱会",
                "artist": "周杰伦",
                "city": "上海",
                "venue": "上海体育场",
                "sale_date": "2026-09-20",
                "sale_time": "10:00",
                "platform": "大麦",
                "source": "wechat",
                "parsed_at": "2026-09-15T20:00:00",
            },
            {
                "event_name": "林俊杰演唱会",
                "artist": "林俊杰",
                "city": "北京",
                "sale_date": "2026-09-20",
                "sale_time": "11:00",
                "platform": "猫眼",
                "source": "wechat",
                "parsed_at": "2026-09-15T20:00:00",
            },
        ]

        ids = self.db.save_events(events)
        assert len(ids) == 2
        assert all(id > 0 for id in ids)

    def test_get_events_by_date(self):
        """测试按日期获取播报"""
        events = [
            {
                "event_name": "演出A",
                "sale_date": "2026-09-20",
                "source": "wechat",
                "parsed_at": "2026-09-15T20:00:00",
            },
            {
                "event_name": "演出B",
                "sale_date": "2026-09-21",
                "source": "wechat",
                "parsed_at": "2026-09-15T20:00:00",
            },
        ]
        self.db.save_events(events)

        # 查询 9月20日
        results = self.db.get_events_by_date("2026-09-20")
        assert len(results) == 1
        assert results[0]["event_name"] == "演出A"

    def test_update_status(self):
        """测试更新状态"""
        events = [
            {
                "event_name": "测试演出",
                "sale_date": "2026-09-20",
                "source": "wechat",
                "parsed_at": "2026-09-15T20:00:00",
            },
        ]
        ids = self.db.save_events(events)

        success = self.db.update_status(ids[0], "confirmed")
        assert success is True

        # 验证状态已更新
        events = self.db.get_events_by_date("2026-09-20")
        assert events[0]["status"] == "confirmed"

    def test_skip_error_events(self):
        """测试跳过错误事件"""
        events = [
            {"error": "解析失败"},
            {
                "event_name": "正常演出",
                "sale_date": "2026-09-20",
                "source": "wechat",
                "parsed_at": "2026-09-15T20:00:00",
            },
        ]

        ids = self.db.save_events(events)
        assert len(ids) == 1  # 只保存了正常的事件
