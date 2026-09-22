"""
ChatService 编排层测试

策略：不打真实网络。
- LLM：monkeypatch chat_service.llm.chat
- 工具：monkeypatch chat_service.execute_tool
- 知识库：monkeypatch chat_service.retrieve_from_knowledge
- 时间：monkeypatch chat_service.get_accurate_time（避免 NTP）
- 数据库：OrderManager(db=Database(":memory:"))
- 开抢提醒：ORDER_PARSE 用例直接替换 build_sale_alerts，避免拉大麦数据
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.agent.order_manager import OrderManager
from ticketpilot.application import chat_service as cs_mod
from ticketpilot.application.chat_service import ChatResult, ChatService
from ticketpilot.data.database import Database
from ticketpilot.data.models import Order

FIXED_NOW = datetime(2026, 9, 21, 10, 0, 0)


@pytest.fixture
def om():
    return OrderManager(db=Database(":memory:"))


@pytest.fixture
def service(om):
    return ChatService(order_manager=om)


# ===========================================
# 分发
# ===========================================

class TestDispatch:
    def test_three_domains(self, service, monkeypatch):
        """关键词路径（use_llm_router=False）按三域分发"""
        for method, tag in [
            ("_handle_order", "order"),
            ("_handle_agent", "agent"),
            ("_handle_knowledge_qa", "kb"),
        ]:
            monkeypatch.setattr(
                service, method,
                lambda t, tag=tag: ChatResult(reply=tag, intent="X", route=tag),
            )

        cases = [
            ("帮我整理一下这个客户的订单", "order"),
            ("查看订单", "order"),
            ("周杰伦什么时候开票", "agent"),
            ("你好", "agent"),
            ("什么是一开二开", "kb"),
        ]
        for text, expected in cases:
            result = service.chat(text, use_llm_router=False)
            assert result.reply == expected, f"输入 {text!r} 应走 {expected} 域"

    def test_order_domain_internal_dispatch(self, service, monkeypatch):
        """订单域内部：内容硬特征 → 解析；否则 → 管理子分类"""
        monkeypatch.setattr(
            service, "_handle_order_parse",
            lambda t: ChatResult(reply="parse", intent="X", route="parse"),
        )
        monkeypatch.setattr(
            service, "_handle_order_manage",
            lambda t: ChatResult(reply="manage", intent="X", route="manage"),
        )

        # 身份证硬特征 → 解析
        r = service._handle_order("张三310101199001011234 上海周杰伦 10.1 1280连座")
        assert r.reply == "parse"
        # 管理指令（无订单内容特征）→ 管理
        r = service._handle_order("查看订单")
        assert r.reply == "manage"

    def test_llm_router_receives_context(self, service, monkeypatch):
        """use_llm_router=True 时走 router.classify_domain 并传入 context"""
        from ticketpilot.core.router import Domain

        captured = {}

        def fake_classify(user_input, context="", use_llm=True):
            captured["input"] = user_input
            captured["context"] = context
            return Domain.AGENT

        monkeypatch.setattr(cs_mod.router, "classify_domain", fake_classify)
        monkeypatch.setattr(
            service, "_handle_agent",
            lambda t: ChatResult(reply="ok", intent="GENERAL", route="agent"),
        )
        service.chat("你好", context="最近创建的订单：X", use_llm_router=True)
        assert captured["input"] == "你好"
        assert captured["context"] == "最近创建的订单：X"


# ===========================================
# ORDER_PARSE
# ===========================================

class TestOrderParse:
    ORDER_JSON = json.dumps({
        "customer_name": "张三",
        "event_name": "上海周杰伦演唱会",
        "event_date": "2026-10-01",
        "ticket_type": "1280",
        "quantity": 2,
        "seats": "连座",
        "budget": "13800138000",
        "notes": "张三310101199001011234",
    })

    def test_save_backfills_id_and_masks_pii(self, service, om, monkeypatch):
        """保存后回填 order.id；回复走脱敏格式，明文电话/身份证不出现"""
        monkeypatch.setattr(service, "build_sale_alerts", lambda new_orders=None: "")
        monkeypatch.setattr(cs_mod.llm, "chat",
                            lambda *a, **k: {"content": self.ORDER_JSON})

        result = service._handle_order_parse(
            "上海周杰伦演唱会 10.1 1280连座两张 张三13800138000"
        )

        # id 回填（修复开抢提醒 {o.id} 永不匹配的死代码）
        assert len(result.orders) == 1
        assert result.orders[0].id == 1
        # 已落库
        assert len(om.get_all_orders()) == 1
        # 回复格式与脱敏
        assert "已整理 1 条订单" in result.reply
        assert "订单 1（订单号：1）" in result.reply
        assert "13800138000" not in result.reply
        assert "138****8000" in result.reply
        assert "310101199001011234" not in result.reply
        assert "310***********1234" in result.reply

    def test_empty_orders_message(self, service, monkeypatch):
        """解析不出订单时给出引导文案（而非"已整理 0 条订单"）"""
        monkeypatch.setattr(service.order_manager, "parse_multiple_orders",
                            lambda text: [])
        result = service._handle_order_parse("随便说点什么")
        assert "未能从输入中解析出订单信息" in result.reply
        assert result.orders == []


# ===========================================
# 工具循环
# ===========================================

class TestToolLoop:
    def _patch_llm(self, monkeypatch, responses):
        """按调用序号返回：第 1 次带 tool_calls，之后返回最终回复。
        不用 kwargs["tools"] 判断——测试进程里工具注册表可能为空（tools=[] 亦为假值）。"""
        seen = []

        def fake_chat(messages, **kwargs):
            seen.append([dict(m) for m in messages])
            if len(seen) == 1:
                return responses[0]
            return responses[-1]

        monkeypatch.setattr(cs_mod.llm, "chat", fake_chat)
        return seen

    def test_general_with_tool_call(self, service, monkeypatch):
        executed = []
        monkeypatch.setattr(
            cs_mod, "execute_tool",
            lambda name, args: executed.append((name, args)) or '{"ok": true}',
        )
        seen = self._patch_llm(monkeypatch, [
            {"content": "", "tool_calls": [
                {"id": "c1", "function": "check_time", "arguments": "{}"},
            ]},
            {"content": "现在是下午3点"},
        ])

        result = service._handle_agent("现在几点")

        assert result.reply == "现在是下午3点"
        assert executed == [("check_time", {})]
        # 工具结果按 OpenAI 协议回填：assistant(tool_calls) + tool 消息
        final_msgs = seen[-1]
        assert final_msgs[-2]["role"] == "assistant"
        assert final_msgs[-2]["tool_calls"][0]["id"] == "c1"
        assert final_msgs[-1]["role"] == "tool"
        assert final_msgs[-1]["tool_call_id"] == "c1"
        assert final_msgs[-1]["content"] == '{"ok": true}'

    def test_bad_arguments_no_crash(self, service, monkeypatch):
        """坏 arguments 降级为空参数，不再 500"""
        executed = []
        monkeypatch.setattr(
            cs_mod, "execute_tool",
            lambda name, args: executed.append((name, args)) or "ok",
        )
        self._patch_llm(monkeypatch, [
            {"content": "", "tool_calls": [
                {"id": "c1", "function": "check_time", "arguments": "{不是合法JSON"},
            ]},
            {"content": "done"},
        ])

        result = service._handle_agent("现在几点")
        assert result.reply == "done"
        assert executed == [("check_time", {})]

    def test_empty_content_fallback(self, service, monkeypatch):
        """LLM 最终回复为空时用兜底文案"""
        self._patch_llm(monkeypatch, [
            {"content": "", "tool_calls": [
                {"id": "c1", "function": "check_time", "arguments": "{}"},
            ]},
            {"content": None},
        ])
        monkeypatch.setattr(cs_mod, "execute_tool", lambda name, args: "ok")
        result = service._handle_agent("现在几点")
        assert result.reply == "处理完成"


# ===========================================
# KNOWLEDGE_QA
# ===========================================

class TestKnowledgeQA:
    def test_hard_gate_refuses_without_llm(self, service, monkeypatch):
        """硬门控：检索未过阈值 → 罐头拒答，LLM 不允许被调用
        （防编造从 prompt 恳求升级为控制流物理隔离）"""
        monkeypatch.setattr(cs_mod, "retrieve_from_knowledge", lambda q: "")

        def boom(*a, **k):
            raise AssertionError("拒答路径不应调用 LLM")

        monkeypatch.setattr(cs_mod.llm, "chat", boom)
        result = service._handle_knowledge_qa("什么是配票")

        assert "知识库暂无相关信息" in result.reply
        assert result.route == "knowledge_qa_refused"

    def test_hit_context_injected_with_citation_rule(self, service, monkeypatch):
        monkeypatch.setattr(cs_mod, "retrieve_from_knowledge",
                            lambda q: "配票是指主办方释放余票…")
        captured = {}

        def fake_chat(messages, **kw):
            captured["system"] = messages[0]["content"]
            return {"content": "回答"}

        monkeypatch.setattr(cs_mod.llm, "chat", fake_chat)
        result = service._handle_knowledge_qa("什么是配票")
        assert "参考知识库内容" in captured["system"]
        assert "配票是指主办方释放余票" in captured["system"]
        # 引用要求：回答末尾列来源
        assert "来源" in captured["system"]
        assert result.route == "knowledge_qa"


# ===========================================
# EVENT_QUERY
# ===========================================

class TestEventQuery:
    def test_fallback_executes_search_and_hints_source(self, service, monkeypatch):
        """方案2：LLM 未调用工具 → 剥离关键词直接查询 + 来源提示"""
        executed = []
        monkeypatch.setattr(
            cs_mod, "execute_tool",
            lambda name, args: executed.append((name, args))
            or json.dumps([{"name": "周杰伦演唱会", "data_source": "web"}]),
        )
        captured = []
        call_count = []

        def fake_chat(messages, **kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                return {"content": None, "tool_calls": None}  # LLM 不调工具
            captured.append(messages[0]["content"])
            return {"content": "以下是查询结果"}

        monkeypatch.setattr(cs_mod.llm, "chat", fake_chat)
        result = service._handle_agent("周杰伦什么时候开票")
        assert result.route == "agent_fallback_search"

        # 疑问词已剥离，只剩艺人名
        assert executed == [("search_event", {"keyword": "周杰伦", "city": None})]
        # web 来源触发降级提示（API 版全量文案）
        assert "仅供参考" in captured[0]
        assert "主流平台" in captured[0]
        assert result.reply == "以下是查询结果"

    def test_source_hint_damai_and_bad_json(self):
        assert "大麦播报站" in ChatService._build_source_hint(
            json.dumps([{"data_source": "damai"}]))
        assert ChatService._build_source_hint("不是JSON") == ""
        assert ChatService._build_source_hint(json.dumps([])) == ""


# ===========================================
# ORDER_MANAGE
# ===========================================

class TestOrderManage:
    def test_delete_by_index(self, service, om):
        om.save_order(Order(customer_name="A", event_name="上海周杰伦演唱会"))
        om.save_order(Order(customer_name="B", event_name="北京薛之谦演唱会"))

        result = service._handle_order_manage("删除订单2")

        assert "已删除订单" in result.reply
        remaining = om.get_all_orders()
        assert len(remaining) == 1
        assert remaining[0].event_name == "上海周杰伦演唱会"

    def test_delete_all(self, service, om):
        om.save_order(Order(customer_name="A", event_name="演唱会1"))
        om.save_order(Order(customer_name="B", event_name="演唱会2"))
        result = service._handle_order_manage("删除所有订单")
        assert "已删除全部 2 条订单" in result.reply
        assert om.get_all_orders() == []

    def test_delete_out_of_range(self, service, om):
        om.save_order(Order(customer_name="A", event_name="演唱会1"))
        reply = service._manage_delete("删除订单9")  # _manage_* 返回纯文本
        assert "超出范围" in reply
        assert len(om.get_all_orders()) == 1

    def test_summary_table(self, service, om):
        om.save_order(Order(customer_name="A", event_name="演唱会1"))
        result = service._handle_order_manage("标记中票")
        assert "| 待抢票 | 1 |" in result.reply
        assert "| **总计** | **1** |" in result.reply

    def test_query_all_orders(self, service, om, monkeypatch):
        om.save_order(Order(customer_name="A", event_name="上海周杰伦演唱会"))
        monkeypatch.setattr(
            cs_mod.llm, "chat",
            lambda *a, **k: {"content": '{"keywords": [], "query_type": "all"}'},
        )
        result = service._handle_order_manage("查看所有订单")
        assert "当前共有 1 条订单" in result.reply
        assert "上海周杰伦演唱会" in result.reply

    def test_query_masks_viewer_pii(self, service, om, monkeypatch):
        """行为变更：查询分支的观影人行不再输出明文身份证"""
        om.save_order(Order(
            customer_name="张三", event_name="上海周杰伦演唱会",
            notes="张三310101199001011234",
        ))
        monkeypatch.setattr(
            cs_mod.llm, "chat",
            lambda *a, **k: {"content": '{"keywords": ["周杰伦"], "query_type": "specific"}'},
        )
        result = service._handle_order_manage("我有周杰伦的单子吗")
        assert "找到 1 条相关订单" in result.reply
        assert "310101199001011234" not in result.reply
        assert "310***********1234" in result.reply

    def test_edit_updates_field(self, service, om, monkeypatch):
        om.save_order(Order(customer_name="A", event_name="黑马演唱会", ticket_type="980"))
        monkeypatch.setattr(
            cs_mod.llm, "chat",
            lambda *a, **k: {"content": '{"target_index": 1, "updates": {"ticket_type": "1680"}}'},
        )
        result = service._handle_order_manage("把票价改成1680")
        assert "已更新订单" in result.reply
        assert om.get_all_orders()[0].ticket_type == "1680"

    def test_edit_city_prefix(self, service, om, monkeypatch):
        """event_name_prefix：城市拼在演出名最前面，已含城市则不重复"""
        om.save_order(Order(customer_name="A", event_name="黑马演唱会"))
        monkeypatch.setattr(
            cs_mod.llm, "chat",
            lambda *a, **k: {"content": '{"target_index": 1, "updates": {"event_name_prefix": "上海"}}'},
        )
        service._handle_order_manage("订单1的黑马前面加一个上海站")
        assert om.get_all_orders()[0].event_name == "上海黑马演唱会"

    def test_edit_bad_llm_output(self, service, om, monkeypatch):
        """LLM 输出非 JSON 时不崩、给出引导（"改成"命中 edit 子分类）"""
        om.save_order(Order(customer_name="A", event_name="演唱会1"))
        monkeypatch.setattr(cs_mod.llm, "chat", lambda *a, **k: {"content": "我不知道"})
        result = service._handle_order_manage("把票价改成随便什么")
        assert "没理解" in result.reply


# ===========================================
# 草稿确认（D3：LLM 解析先落 draft，人工确认后生效）
# ===========================================

class TestDraftConfirm:
    def test_parse_saves_draft_with_confirm_hint(self, service, om, monkeypatch):
        """解析结果默认草稿落库，回复带确认引导"""
        monkeypatch.setattr(service, "build_sale_alerts", lambda new_orders=None: "")
        monkeypatch.setattr(cs_mod.llm, "chat",
                            lambda *a, **k: {"content": TestOrderParse.ORDER_JSON})

        result = service._handle_order_parse(
            "上海周杰伦演唱会 10.1 1280连座两张 张三13800138000")

        assert om.get_all_orders()[0].confirmed is False
        assert "草稿" in result.reply
        assert "确认全部" in result.reply

    def test_confirm_all(self, service, om):
        om.save_order(Order(customer_name="A", event_name="演唱会1"))
        om.save_order(Order(customer_name="B", event_name="演唱会2"))

        result = service._handle_order_manage("确认全部")

        assert "已确认全部 2 条草稿订单" in result.reply
        assert all(o.confirmed for o in om.get_all_orders())
        assert result.route == "order_manage"

    def test_confirm_by_index(self, service, om):
        om.save_order(Order(customer_name="A", event_name="演唱会1"))
        om.save_order(Order(customer_name="B", event_name="演唱会2"))

        reply = service._manage_confirm("确认第2条")

        assert "已确认订单：演唱会2" in reply
        orders = om.get_all_orders()
        assert orders[0].confirmed is False  # 只确认目标那条
        assert orders[1].confirmed is True

    def test_confirm_idempotent(self, service, om):
        order_id = om.save_order(Order(customer_name="A", event_name="演唱会1"))
        om.confirm_order(order_id)

        reply = service._manage_confirm("确认第1条")
        assert "无需重复确认" in reply

    def test_confirm_no_drafts(self, service, om):
        assert "没有待确认" in service._manage_confirm("确认全部")

    def test_bare_confirm_lists_drafts_without_guessing(self, service, om):
        """无序号的裸「确认」：列出草稿让用户选，不批量生效"""
        om.save_order(Order(customer_name="A", event_name="演唱会1"))

        reply = service._manage_confirm("确认")

        assert "待确认的草稿订单" in reply
        assert "演唱会1" in reply
        assert om.get_all_orders()[0].confirmed is False

    def test_summary_shows_draft_count(self, service, om):
        om.save_order(Order(customer_name="A", event_name="演唱会1"))
        assert "| 待确认草稿 | 1 |" in service._manage_summary()

    def test_query_all_marks_drafts(self, service, om, monkeypatch):
        om.save_order(Order(customer_name="A", event_name="上海周杰伦演唱会"))
        monkeypatch.setattr(
            cs_mod.llm, "chat",
            lambda *a, **k: {"content": '{"keywords": [], "query_type": "all"}'},
        )
        result = service._handle_order_manage("查看所有订单")
        assert "待确认" in result.reply


# ===========================================
# PII 输入最小化（D4：LLM 全程看不到明文证件号/手机号）
# ===========================================

class TestPiiMinimization:
    def test_edit_redacts_phone_and_restores(self, service, om, monkeypatch):
        """改电话指令：LLM 只见 [PHONE_1]，落库还原为真实号码"""
        om.save_order(Order(customer_name="张三", event_name="上海周杰伦演唱会"))
        captured = {}

        def fake_chat(messages, **kw):
            captured["prompt"] = messages[0]["content"]
            return {"content": '{"target_index": 1, "updates": {"budget": "[PHONE_1]"}}'}

        monkeypatch.setattr(cs_mod.llm, "chat", fake_chat)
        service._handle_order_manage("把张三的电话改成13800138000")

        assert "13800138000" not in captured["prompt"]
        assert "[PHONE_1]" in captured["prompt"]
        assert om.get_all_orders()[0].budget == "13800138000"

    def test_delete_by_id_keyword_restored(self, service, om, monkeypatch):
        """按证件号删单：提取 prompt 无明文；LLM 回显的占位符关键词
        本地还原后仍能匹配库里的原文 notes"""
        om.save_order(Order(customer_name="张三", event_name="演唱会1",
                            notes="张三 310101199001011234"))
        captured = {}

        def fake_chat(messages, **kw):
            captured["prompt"] = messages[0]["content"]
            return {"content": '{"keywords": ["[ID_1]"]}'}

        monkeypatch.setattr(cs_mod.llm, "chat", fake_chat)
        result = service._handle_order_manage("删除身份证310101199001011234的订单")

        assert "310101199001011234" not in captured["prompt"]
        assert "已删除订单" in result.reply
        assert om.get_all_orders() == []


# ===========================================
# 可观测性（D4-2：结构化日志 + 日志脱敏）
# ===========================================

class TestObservability:
    def test_chat_logs_structured_and_masked(self, service, monkeypatch, caplog):
        """每次 chat 落一行 JSON（评测脚本按字段统计域分布/拒答率）；
        入口日志的 input 字段必须先脱敏——日志文件也是输出通道"""
        import logging

        monkeypatch.setattr(
            service, "_handle_order",
            lambda t: ChatResult(reply="ok", intent="ORDER_PARSE", route="order_parse"),
        )
        with caplog.at_level(logging.INFO, logger="ticketpilot.application.chat_service"):
            service.chat("张三310101199001011234 上海周杰伦", use_llm_router=False)

        assert "310101199001011234" not in caplog.text       # 明文不落日志
        assert "310***********1234" in caplog.text            # 脱敏形态在
        assert '"event": "chat"' in caplog.text               # 结构化行存在
        assert '"domain": "ORDER"' in caplog.text
        assert '"route": "order_parse"' in caplog.text

    def test_chat_log_carries_cost_fields(self, service, monkeypatch, caplog):
        """结构化行必须携带 llm_calls/latency_ms——
        D5 评测脚本按日志统计单次请求成本与延迟，字段缺失=指标口径断了"""
        import logging

        monkeypatch.setattr(
            service, "_handle_order",
            lambda t: ChatResult(reply="ok", intent="ORDER_PARSE", route="order_parse"),
        )

        def boom(*a, **k):
            raise AssertionError("本测试路径不应发生真实 LLM 调用")
        monkeypatch.setattr(cs_mod.llm, "chat", boom)

        with caplog.at_level(logging.INFO, logger="ticketpilot.application.chat_service"):
            # 必须用 18 位证件号输入：硬路径确定性进 ORDER，monkeypatch 才生效。
            # 若误入 AGENT 路径会真调 LLM——首跑就烧了 2 次真实 API（llm_calls=2 抓包为证）
            service.chat("张三310101199001011234 上海周杰伦", use_llm_router=False)

        line = next(r.getMessage() for r in caplog.records
                    if '"event": "chat"' in r.getMessage())
        payload = json.loads(line)  # 整行必须是合法 JSON（机器可读，不是给人看的文案）
        assert payload["llm_calls"] == 0   # monkeypatch 路径未走真实 llm.chat
        assert isinstance(payload["latency_ms"], int)
        assert payload["llm_calls"] >= 0 and payload["latency_ms"] >= 0


# ===========================================
# 大麦缓存与开抢匹配
# ===========================================

class TestDamai:
    @pytest.fixture(autouse=True)
    def _fixed_time(self, monkeypatch):
        monkeypatch.setattr(cs_mod, "get_accurate_time", lambda: FIXED_NOW)

    def test_cache_hit_and_stale_fallback(self, service, monkeypatch):
        calls = []

        def fake_execute(name, args):
            calls.append(name)
            return json.dumps({"即将开抢": []})

        monkeypatch.setattr(cs_mod, "execute_tool", fake_execute)
        assert service.get_damai_data_cached() == {"即将开抢": []}
        # 1 小时内二次调用走缓存
        assert service.get_damai_data_cached() == {"即将开抢": []}
        assert calls == ["get_damai_broadcast"]

        # 缓存过期 + 拉取失败 → 返回旧缓存
        def boom(name, args):
            raise RuntimeError("net down")

        monkeypatch.setattr(cs_mod, "execute_tool", boom)
        service.damai_cache["last_update"] = FIXED_NOW - timedelta(hours=2)
        assert service.get_damai_data_cached() == {"即将开抢": []}

    def test_find_matching_today(self, service, om):
        om.save_order(Order(customer_name="A", event_name="上海周杰伦演唱会"))
        damai = {"即将开抢": [
            {"name": "周杰伦嘉年华世界巡回演唱会-上海站", "city": "上海", "sale_time": "今天 12:18"},
            {"name": "薛之谦天外来物巡演-北京站", "city": "北京", "sale_time": "今天 10:00"},
        ]}
        matched = service.find_matching_orders(damai, FIXED_NOW)
        assert len(matched) == 1
        assert matched[0]["order"].event_name == "上海周杰伦演唱会"
        assert matched[0]["sale"]["sale_time"] == "今天 12:18"

    def test_find_matching_tomorrow_and_explicit_date(self, service, om):
        om.save_order(Order(customer_name="A", event_name="上海周杰伦演唱会"))
        damai = {"即将开抢": [
            {"name": "周杰伦嘉年华-上海站", "city": "上海", "sale_time": "明天 12:18"},
        ]}
        assert len(service.find_matching_orders(damai, FIXED_NOW + timedelta(days=1))) == 1
        # 今天不匹配"明天"的场次
        assert service.find_matching_orders(damai, FIXED_NOW) == []
        # X月X日格式
        damai2 = {"即将开抢": [
            {"name": "周杰伦嘉年华-上海站", "city": "上海", "sale_time": "9月22日 12:18"},
        ]}
        assert len(service.find_matching_orders(damai2, FIXED_NOW + timedelta(days=1))) == 1

    def test_build_sale_alerts_filters_new_orders(self, service, om, monkeypatch):
        monkeypatch.setattr(
            service, "get_sale_matches",
            lambda: ([{"order": Order(id=7, customer_name="A", event_name="演唱会X"),
                       "sale": {"sale_time": "今天 20:00"}}], []),
        )
        # 只提醒新增订单：id 不在 new_orders 中则无提醒
        assert service.build_sale_alerts(new_orders=[Order(id=99, customer_name="B", event_name="Y")]) == ""
        alert = service.build_sale_alerts(new_orders=[Order(id=7, customer_name="A", event_name="演唱会X")])
        assert "今天开抢" in alert and "演唱会X" in alert
