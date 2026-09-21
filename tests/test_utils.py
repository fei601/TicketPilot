"""
core/utils 测试：extract_json
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.core.utils import extract_json


class TestExtractJson:
    """extract_json 对象/数组提取测试"""

    def test_bare_object(self):
        """裸 JSON 对象"""
        assert extract_json('{"intent": "GENERAL"}') == {"intent": "GENERAL"}

    def test_object_in_code_block(self):
        """Markdown 代码块中的对象（带 json 标记和不带标记）"""
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_nested_object(self):
        """嵌套对象（非贪婪匹配需跨越多层右括号）"""
        result = extract_json('{"order": {"customer": "张三", "qty": 2}}')
        assert result == {"order": {"customer": "张三", "qty": 2}}

    def test_bare_array(self):
        """裸 JSON 数组"""
        assert extract_json('[{"id": 1}, {"id": 2}]') == [{"id": 1}, {"id": 2}]

    def test_array_in_code_block(self):
        """代码块中的数组（含嵌套对象）"""
        text = '```json\n[{"name": "张三", "tags": ["a", "b"]}]\n```'
        assert extract_json(text) == [{"name": "张三", "tags": ["a", "b"]}]

    def test_json_with_surrounding_text(self):
        """JSON 前后有解释性文字"""
        text = '好的，结果如下：\n{"intent": "ORDER_PARSE"}\n hope this helps'
        assert extract_json(text) == {"intent": "ORDER_PARSE"}

    def test_array_before_object(self):
        """数组先出现时取数组（最外层括号取先出现者）"""
        assert extract_json('[{"a": 1}]') == [{"a": 1}]

    def test_failures(self):
        """无法提取时返回 None"""
        assert extract_json("") is None
        assert extract_json("纯文本没有 JSON") is None
        assert extract_json("{不是合法 JSON}") is None
