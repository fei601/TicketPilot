"""
privacy 模块测试：PII 输入最小化（redact/restore）+ prompt 模板卫生

核心不变量：
1. 发给 LLM 的文本里不出现明文证件号/手机号（占位符替代）
2. 占位符在本地还原，落库仍是原文（DB 可信边界内）
3. prompt 模板本身不烧录任何完整证件号/手机号（哪怕是虚构的）
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ticketpilot.core.privacy import redact_pii, restore_pii
from ticketpilot.core.prompts import ORDER_PARSE_PROMPT
from ticketpilot.core.router import DOMAIN_CLASSIFY_PROMPT

ID_A = "310101199001011234"
ID_B = "31010119900101123X"
PHONE_A = "13800138000"


class TestRedactPii:
    def test_round_trip(self):
        text = f"张三{ID_A} 上海周杰伦 联系电话{PHONE_A}"
        redacted, mapping = redact_pii(text)
        assert ID_A not in redacted and PHONE_A not in redacted
        assert "[ID_1]" in redacted and "[PHONE_1]" in redacted
        assert restore_pii(redacted, mapping) == text

    def test_id_before_phone_no_crossover(self):
        """18 位证件号内部含形如手机号的 11 位子串时，必须整体作为 ID 置换，
        不能被手机号正则切碎（替换顺序：身份证在前）"""
        id_with_phone_inside = "340803199507162319"  # 内含 "19950716231" 命中 1[3-9]\d{9}
        redacted, mapping = redact_pii(id_with_phone_inside)
        assert redacted == "[ID_1]"
        assert mapping == {"[ID_1]": id_with_phone_inside}

    def test_multiple_pii_numbered_independently(self):
        text = f"张三{ID_A} 李四{ID_B} 电话{PHONE_A}"
        redacted, mapping = redact_pii(text)
        assert "[ID_1]" in redacted and "[ID_2]" in redacted and "[PHONE_1]" in redacted
        assert len(mapping) == 3

    def test_no_pii_untouched(self):
        text = "周杰伦什么时候开票"
        redacted, mapping = redact_pii(text)
        assert redacted == text and mapping == {}

    def test_restore_tolerates_non_str(self):
        """LLM 偶尔对数字字段输出 int，回填层不许崩"""
        assert restore_pii(None, {"[ID_1]": ID_A}) is None
        assert restore_pii(1680, {"[ID_1]": ID_A}) == 1680


class TestPromptHygiene:
    """prompt 模板随每次调用出境，不得烧录完整证件号/手机号"""

    def test_order_parse_prompt_clean(self):
        assert not re.search(r'\d{17}[\dXx]', ORDER_PARSE_PROMPT)
        assert not re.search(r'1[3-9]\d{9}', ORDER_PARSE_PROMPT)

    def test_domain_classify_prompt_clean(self):
        assert not re.search(r'\d{17}[\dXx]', DOMAIN_CLASSIFY_PROMPT)
        assert not re.search(r'1[3-9]\d{9}', DOMAIN_CLASSIFY_PROMPT)


class TestParseMinimization:
    """parse_order_from_text 集成：LLM 只见占位符，返回的 Order 已本地还原"""

    def test_llm_never_sees_raw_pii(self, monkeypatch):
        from ticketpilot.agent import order_manager as om_mod
        from ticketpilot.agent.order_manager import OrderManager
        from ticketpilot.data.database import Database

        captured = {}

        def fake_chat(messages, **kw):
            captured["user"] = messages[1]["content"]
            return {"content": ('{"customer_name": "张三", "event_name": "上海周杰伦",'
                                ' "event_date": "10.1", "ticket_type": "1280", "quantity": 1,'
                                ' "seats": "连座", "budget": "[PHONE_1]",'
                                ' "notes": "张三 [ID_1]"}')}

        monkeypatch.setattr(om_mod.llm, "chat", fake_chat)
        om = OrderManager(db=Database(":memory:"))
        order = om.parse_order_from_text(
            f"上海周杰伦 10.1 1280连座 张三{ID_A} 联系电话{PHONE_A}")

        # 出境检查：LLM 收到的 user 消息里没有明文
        assert ID_A not in captured["user"]
        assert PHONE_A not in captured["user"]
        assert "[ID_1]" in captured["user"] and "[PHONE_1]" in captured["user"]
        # 本地还原检查
        assert order.notes == f"张三 {ID_A}"
        assert order.budget == PHONE_A
