"""
工具模块测试
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.tools.base import execute_tool, get_all_tool_schemas, list_tools
from ticketpilot.tools import event_search, time_utils  # noqa: F401


class TestToolRegistry:
    """工具注册机制测试"""

    def test_tools_registered(self):
        """测试工具是否正确注册"""
        tools = list_tools()
        assert "search_event" in tools
        assert "check_approval" in tools
        assert "check_time" in tools

    def test_get_schemas(self):
        """测试获取工具 schema"""
        schemas = get_all_tool_schemas()
        assert len(schemas) >= 3
        for schema in schemas:
            assert "type" in schema
            assert schema["type"] == "function"
            assert "function" in schema
            assert "name" in schema["function"]


class TestEventSearch:
    """演出搜索工具测试（Mock 数据源）"""

    def test_search_by_artist(self):
        """测试按艺人搜索"""
        result = execute_tool("search_event", {"keyword": "周杰伦"})
        data = json.loads(result)
        # Mock 数据源会返回结果，WebSearch 可能因无 API Key 返回错误
        assert isinstance(data, (list, dict))

    def test_search_mock_source(self):
        """测试 Mock 数据源"""
        from ticketpilot.tools.event_search import MockDataSource
        source = MockDataSource()
        results = source.search("周杰伦")
        assert len(results) > 0
        assert "周杰伦" in results[0]["artist"]

    def test_search_mock_with_city(self):
        """测试 Mock 数据源城市筛选"""
        from ticketpilot.tools.event_search import MockDataSource
        source = MockDataSource()
        results = source.search("周杰伦", city="上海")
        assert len(results) > 0
        assert results[0]["city"] == "上海"

    def test_search_no_result(self):
        """测试无结果搜索"""
        from ticketpilot.tools.event_search import MockDataSource
        source = MockDataSource()
        results = source.search("不存在的艺人")
        assert len(results) == 0


class TestWebSearchDataSource:
    """联网搜索数据源测试"""

    def test_no_api_key(self):
        """测试未配置 API Key 时的降级"""
        from ticketpilot.tools.event_search import WebSearchDataSource
        source = WebSearchDataSource(api_key="")
        results = source.search("周杰伦")
        assert len(results) > 0
        assert "error" in results[0]


class TestCheckApproval:
    """演出审批查询测试"""

    def test_check_approval(self):
        """测试审批查询（当前为占位实现）"""
        result = execute_tool("check_approval", {"event_name": "周杰伦演唱会"})
        data = json.loads(result)
        assert data["event_name"] == "周杰伦演唱会"

    def test_check_approval_with_city(self):
        """测试带城市的审批查询"""
        result = execute_tool("check_approval", {"event_name": "周杰伦演唱会", "city": "上海"})
        data = json.loads(result)
        assert data["city"] == "上海"


class TestTimeUtils:
    """时间工具测试"""

    def test_check_time_now(self):
        """测试获取当前时间"""
        result = execute_tool("check_time", {"action": "now"})
        assert "当前时间" in result

    def test_check_time_diff(self):
        """测试计算时间差"""
        result = execute_tool("check_time", {
            "action": "diff",
            "target_time": "2026-12-31 23:59",
        })
        assert "还有" in result or "已过去" in result

    def test_check_time_window(self):
        """测试时间窗口检查"""
        result = execute_tool("check_time", {
            "action": "check_window",
            "target_time": "2026-12-31 23:59",
            "window_hours": 48,
        })
        assert "是" in result or "否" in result

    def test_invalid_action(self):
        """测试无效操作"""
        result = execute_tool("check_time", {"action": "invalid"})
        assert "错误" in result
