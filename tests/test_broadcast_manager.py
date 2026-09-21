"""
播报链路测试：process_forwarded_content + scheduler 委托

不打真实网络：
- wechat_parser.parse_broadcast_content 用 monkeypatch（方法是惰性导入，
  patch 模块属性即可生效）
- BroadcastDB/Database 换 :memory:
- Notifier 用假实现
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import ticketpilot.tools.wechat_parser as wechat_parser_mod
from ticketpilot.agent.broadcast_manager import BroadcastManager
from ticketpilot.agent.broadcast_scheduler import BroadcastScheduler
from ticketpilot.data.broadcast_db import BroadcastDB
from ticketpilot.data.database import Database

REPO_ROOT = Path(__file__).parent.parent

SAMPLE_EVENT = {
    "event_name": "周杰伦演唱会",
    "artist": "周杰伦",
    "city": "上海",
    "sale_date": "2026-09-22",
    "sale_time": "10:00",
}


@pytest.fixture
def manager():
    """BroadcastManager + 内存库（构造函数会开真实路径的库，随后替换）"""
    mgr = BroadcastManager()
    mgr.broadcast_db = BroadcastDB(":memory:")
    mgr.order_db = Database(":memory:")
    return mgr


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_markdown(self, content):
        self.sent.append(content)
        return True


# ===========================================
# process_forwarded_content
# ===========================================

class TestProcessForwardedContent:
    def test_success_saves_and_reports(self, manager, monkeypatch):
        monkeypatch.setattr(wechat_parser_mod, "parse_broadcast_content",
                            lambda content: [dict(SAMPLE_EVENT)])
        # generate_evening_report 走 get_accurate_time(NTP)，替换为固定实现
        monkeypatch.setattr(manager, "generate_evening_report",
                            lambda: {"has_orders": False, "order_events": [],
                                     "other_events": [], "summary": "明日播报内容"})

        result = manager.process_forwarded_content("公众号内容……")

        assert result["success"] is True
        assert result["events"] == [SAMPLE_EVENT]
        assert result["report"]["summary"] == "明日播报内容"
        # 真实入库（内存 broadcast_db）
        saved = manager.broadcast_db.get_events_by_date("2026-09-22")
        assert len(saved) == 1
        assert saved[0]["event_name"] == "周杰伦演唱会"

    def test_empty_parse_fails(self, manager, monkeypatch):
        monkeypatch.setattr(wechat_parser_mod, "parse_broadcast_content",
                            lambda content: [])
        result = manager.process_forwarded_content("无关内容")
        assert result["success"] is False
        assert "解析失败" in result["error"]

    def test_error_dict_fails(self, manager, monkeypatch):
        """wechat_parser 失败约定：[{"error": ...}]"""
        monkeypatch.setattr(wechat_parser_mod, "parse_broadcast_content",
                            lambda content: [{"error": "解析失败: LLM 超时"}])
        result = manager.process_forwarded_content("x")
        assert result["success"] is False


# ===========================================
# scheduler 委托（推送只留在 scheduler）
# ===========================================

class TestSchedulerDelegation:
    def _make_scheduler(self, process_result):
        notifier = FakeNotifier()
        scheduler = BroadcastScheduler(notifier)

        class FakeManager:
            def process_forwarded_content(self, content):
                return process_result

        scheduler.manager = FakeManager()
        return scheduler, notifier

    def test_success_pushes_summary(self):
        scheduler, notifier = self._make_scheduler(
            {"success": True, "events": [{}], "report": {"summary": "📅 明日播报"}})
        out = scheduler.process_broadcast_after_forward("内容")
        assert out == "📅 明日播报"
        assert notifier.sent == ["📅 明日播报"]

    def test_failure_returns_error_no_push(self):
        scheduler, notifier = self._make_scheduler(
            {"success": False, "error": "解析失败，请确认转发的是票务类公众号内容"})
        out = scheduler.process_broadcast_after_forward("内容")
        assert out.startswith("❌")
        assert notifier.sent == []


# ===========================================
# 循环导入回归（步骤 2 的持久化验证，子进程隔离）
# ===========================================

def test_broadcast_manager_import_does_not_pull_tools():
    """agent 层独立 import 不得触发 tools 层（模块级循环导入地雷回归）"""
    code = (
        "import sys;"
        "import ticketpilot.agent.broadcast_manager;"
        "leaked=[m for m in sys.modules if m.startswith('ticketpilot.tools')];"
        "assert not leaked, leaked;"
        "print('OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
