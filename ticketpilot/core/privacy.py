"""
隐私保护模块

提供 PII（个人身份信息）脱敏功能，用于 UI 显示和日志记录。
"""

import re
import logging

logger = logging.getLogger(__name__)


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
