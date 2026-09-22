"""
隐私保护模块

两层防线：
1. 输出层脱敏（mask_*）：展示/日志里把 PII 打星，DB 存原文
2. 输入层最小化（redact_pii/restore_pii）：发给 LLM 前把证件号/手机号
   换成本地占位符，LLM 全程看不到明文，解析结果回填时再还原
"""

import re
import logging

logger = logging.getLogger(__name__)

# 18 位身份证（末位可为 X）；与 router._has_id_card 的判定保持同一模式
_ID_CARD_RE = re.compile(r'\d{17}[\dXx]')
# 11 位手机号
_PHONE_RE = re.compile(r'1[3-9]\d{9}')


def redact_pii(text: str) -> tuple[str, dict[str, str]]:
    """
    输入最小化：把文本中的身份证/手机号替换为占位符。

    Returns:
        (脱敏文本, 占位符→原文映射)。映射只留在本进程内存，绝不发给 LLM。

    替换顺序不可交换：必须身份证在前。18 位数字串内部可能包含
    形如手机号的 11 位子串（如 "…19950716231…" 命中 1[3-9]\\d{9}），
    先置换手机号会把身份证切碎、产生错位映射。
    """
    if not text:
        return text, {}

    mapping: dict[str, str] = {}
    counters = {"ID": 0, "PHONE": 0}

    def _repl(kind: str):
        def inner(m):
            counters[kind] += 1
            token = f"[{kind}_{counters[kind]}]"
            mapping[token] = m.group(0)
            return token
        return inner

    text = _ID_CARD_RE.sub(_repl("ID"), text)
    text = _PHONE_RE.sub(_repl("PHONE"), text)
    return text, mapping


def restore_pii(text, mapping: dict[str, str]):
    """把 LLM 输出中原样带回的占位符还原为真实值（本地完成，不经网络）。
    非字符串（LLM 偶尔对数字字段输出 int）原样返回，不在回填层崩。"""
    if not isinstance(text, str) or not text:
        return text
    for token, raw in mapping.items():
        text = text.replace(token, raw)
    return text


def mask_phone(phone: str | None) -> str:
    """
    手机号脱敏

    Args:
        phone: 原始手机号（11位）

    Returns:
        脱敏后的字符串，如 "138****5678"
    """
    if not phone:
        return "未提供"

    phone = phone.strip()

    # 验证长度
    if len(phone) != 11:
        return "***"

    return phone[:3] + "****" + phone[-4:]


def mask_pii_in_text(text: str) -> str:
    """
    自动检测并脱敏文本中的 PII

    Args:
        text: 包含 PII 的文本

    Returns:
        脱敏后的文本
    """
    if not text:
        return text

    # 脱敏身份证号（18位）
    text = re.sub(
        r'(\d{3})\d{11}(\d{3}[\dXx])',
        lambda m: m.group(1) + "*" * 11 + m.group(2),
        text
    )

    # 脱敏手机号（11位）
    text = re.sub(
        r'(1[3-9]\d)\d{4}(\d{4})',
        lambda m: m.group(1) + "****" + m.group(2),
        text
    )

    return text
