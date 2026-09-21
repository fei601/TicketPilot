"""
意图路由测试
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.core.router import IntentType, classify_intent_simple, classify_order_action


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


class TestClassifyOrderAction:
    """ORDER_MANAGE 子分类测试"""

    def test_delete(self):
        assert classify_order_action("删除订单3") == "delete"
        assert classify_order_action("把张三的订单删了") == "delete"

    def test_delete_by_ba_pattern(self):
        """把…订单/单子 无动作词默认删除"""
        assert classify_order_action("把荣佳颖的订单处理一下") == "delete"
        assert classify_order_action("把这个单子弄走") == "delete"

    def test_edit(self):
        assert classify_order_action("把票价改成1380") == "edit"
        assert classify_order_action("订单加上一个观影人") == "edit"

    def test_query(self):
        assert classify_order_action("我的订单有哪些") == "query"
        assert classify_order_action("查看订单") == "query"

    def test_summary_fallback(self):
        """无删除/编辑/查询关键词时回退到状态汇总"""
        assert classify_order_action("标记中票") == "summary"
        assert classify_order_action("这单中了") == "summary"

    def test_delete_beats_edit(self):
        """删除与编辑词同现时删除优先"""
        assert classify_order_action("修改一下，把张三订单删除") == "delete"
