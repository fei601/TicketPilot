"""
意图路由测试
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.core.router import IntentType, classify_intent_simple


class TestClassifyIntentSimple:
    """关键词意图分类测试"""

    def test_order_parse_intent(self):
        """测试订单整理意图识别"""
        assert classify_intent_simple("帮我整理一下这个客户的订单") == IntentType.ORDER_PARSE
        assert classify_intent_simple("客户要两张周杰伦580连座") == IntentType.ORDER_PARSE
        assert classify_intent_simple("帮我录入这个订单信息") == IntentType.ORDER_PARSE

    def test_event_query_intent(self):
        """测试演出查询意图识别"""
        assert classify_intent_simple("周杰伦什么时候开票") == IntentType.EVENT_QUERY
        assert classify_intent_simple("这个演出在哪个平台买") == IntentType.EVENT_QUERY
        assert classify_intent_simple("票价多少") == IntentType.EVENT_QUERY

    def test_order_manage_intent(self):
        """测试订单管理意图识别"""
        assert classify_intent_simple("这个单中了") == IntentType.ORDER_MANAGE
        assert classify_intent_simple("没中的撤单吧") == IntentType.ORDER_MANAGE
        assert classify_intent_simple("等待二开") == IntentType.ORDER_MANAGE

    def test_knowledge_qa_intent(self):
        """测试知识问答意图识别"""
        assert classify_intent_simple("什么是一开二开") == IntentType.KNOWLEDGE_QA
        assert classify_intent_simple("大麦怎么退票") == IntentType.KNOWLEDGE_QA

    def test_general_intent(self):
        """测试通用对话意图识别"""
        assert classify_intent_simple("你好") == IntentType.GENERAL
        assert classify_intent_simple("今天天气怎么样") == IntentType.GENERAL
