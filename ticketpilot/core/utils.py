"""
核心工具函数

提供通用的工具函数，避免代码重复。
"""

import json
import re
import logging

logger = logging.getLogger(__name__)


def extract_json(text: str) -> dict | None:
    """
    从文本中提取 JSON 对象。

    支持以下格式：
    1. Markdown 代码块中的 JSON: ```json {...}```
    2. 裸 JSON 对象: {...}

    Args:
        text: 包含 JSON 的文本

    Returns:
        解析后的 dict，或 None（如果提取失败）
    """
    if not text:
        return None

    text = text.strip()

    # 尝试1: 从代码块中提取
    code_block_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1))
        except json.JSONDecodeError as e:
            logger.warning(f"代码块中的JSON解析失败: {e}")

    # 尝试2: 提取裸 JSON（找到最外层的 {}）
    try:
        start = text.index("{")
        # 找到匹配的右括号
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning(f"裸JSON解析失败: {e}")

    return None
