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

    def test_confirm(self):
        assert classify_order_action("确认全部") == "confirm"
        assert classify_order_action("确认第2条") == "confirm"
        assert classify_order_action("没问题") == "confirm"
        assert classify_order_action("就这样") == "confirm"

    def test_delete_beats_confirm(self):
        """破坏性动作判定先于确认：「确认删除」按删除处理"""
        assert classify_order_action("确认删除订单3") == "delete"


# ===========================================
# v0.2 三域路由（置信度级联）
# ===========================================

class TestClassifyDomain:
    """三域路由：硬特征快路径 → LLM 三域分类 → 关键词容错映射"""

    def test_hard_path_id_card_bypasses_llm(self, monkeypatch):
        """身份证硬特征直接 ORDER，LLM 级不允许被调用"""
        from ticketpilot.core import llm as llm_mod
        from ticketpilot.core.router import Domain, classify_domain

        def boom(*a, **k):
            raise AssertionError("硬特征快路径不应调用 LLM")

        monkeypatch.setattr(llm_mod, "chat", boom)
        assert classify_domain(
            "张三310101199001011234 上海周杰伦 10.1 1280连座") == Domain.ORDER

    def test_hard_path_manage_command_bypasses_llm(self, monkeypatch):
        """「指令词+对象词」组合句式直接 ORDER"""
        from ticketpilot.core import llm as llm_mod
        from ticketpilot.core.router import Domain, classify_domain

        def boom(*a, **k):
            raise AssertionError("硬特征快路径不应调用 LLM")

        monkeypatch.setattr(llm_mod, "chat", boom)
        assert classify_domain("把张三的订单删了") == Domain.ORDER
        assert classify_domain("修改一下李四的单子") == Domain.ORDER
        assert classify_domain("确认订单1") == Domain.ORDER

    def test_keyword_fallback_mapping(self):
        """use_llm=False：关键词五分类映射到三域"""
        from ticketpilot.core.router import Domain, classify_domain

        assert classify_domain("帮我整理一下这个客户的订单", use_llm=False) == Domain.ORDER
        assert classify_domain("查看订单", use_llm=False) == Domain.ORDER
        assert classify_domain("周杰伦什么时候开票", use_llm=False) == Domain.AGENT
        assert classify_domain("你好", use_llm=False) == Domain.AGENT
        assert classify_domain("什么是一开二开", use_llm=False) == Domain.QA

    def test_llm_domain_classification(self, monkeypatch):
        """LLM 级：JSON 输出映射到域"""
        from ticketpilot.core import llm as llm_mod
        from ticketpilot.core.router import Domain, classify_domain

        monkeypatch.setattr(llm_mod, "chat",
                            lambda *a, **k: {"content": '{"domain": "QA"}'})
        assert classify_domain("随便一句话") == Domain.QA

    def test_llm_garbage_falls_back_to_keywords(self, monkeypatch):
        """LLM 输出无法解析时回退关键词容错层"""
        from ticketpilot.core import llm as llm_mod
        from ticketpilot.core.router import Domain, classify_domain

        monkeypatch.setattr(llm_mod, "chat",
                            lambda *a, **k: {"content": "不是JSON"})
        assert classify_domain("大麦怎么退票") == Domain.QA
