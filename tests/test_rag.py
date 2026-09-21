"""
RAG 检索契约测试：无结果返回空串（而非特定文案）
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.rag.retriever import retrieve_from_knowledge
from ticketpilot.tools.knowledge_qa import search_knowledge


class TestRetrieverContract:
    """retrieve_from_knowledge 返回值契约"""

    def test_no_result_returns_empty_string(self):
        """完全无关的查询返回空串，不是提示文案"""
        result = retrieve_from_knowledge("zzzqqqxxx 完全不存在的主题 98765")
        assert result == ""

    def test_hit_returns_content(self):
        """命中知识库时返回带来源的拼接内容"""
        # 注意：SimpleRetriever 按空白分词，中文整句会作为单一 token
        # 做子串匹配，因此用术语本身作为查询
        result = retrieve_from_knowledge("强实名")
        assert result  # 非空
        assert "【来源：" in result


class TestSearchKnowledgeTool:
    """search_knowledge 工具基于空串判断"""

    def test_not_found(self):
        result = json.loads(search_knowledge("zzzqqqxxx 完全不存在的主题 98765"))
        assert result["found"] is False

    def test_found(self):
        result = json.loads(search_knowledge("强实名"))
        assert result["found"] is True
