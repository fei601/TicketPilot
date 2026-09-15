"""
公众号内容解析工具

用于解析转发到企业微信的公众号内容，提取演出信息。
支持票小二线报等票务公众号格式。
"""

import json
import re
from datetime import datetime, timedelta

from ticketpilot.core import llm
from ticketpilot.tools.base import register_tool


# ===========================================
# 解析 Prompt
# ===========================================

PARSE_PROMPT = """你是一个专业的票务信息解析助手。请从以下公众号文章内容中提取演出信息。

要求：
1. 提取所有演出/演唱会/音乐节信息
2. 识别开票时间（精确到日期和时间）
3. 识别城市、场馆
4. 识别票价区间
5. 识别购票平台（大麦、猫眼、票星球等）

输出 JSON 数组格式：
```json
[
    {
        "event_name": "演出名称",
        "artist": "艺人/歌手",
        "city": "城市",
        "venue": "场馆",
        "show_date": "演出日期 YYYY-MM-DD",
        "sale_date": "开票日期 YYYY-MM-DD",
        "sale_time": "开票时间 HH:MM",
        "platform": "购票平台",
        "prices": "票价区间",
        "notes": "备注信息"
    }
]
```

注意：
- 如果某个字段无法确定，设置为 null
- 开票时间优先级：明确时间 > "今天/明天"推算 > null
- 如果内容不是演出信息，返回空数组 []

公众号内容：
{content}
"""


# ===========================================
# 文本预处理
# ===========================================

def preprocess_text(content: str) -> str:
    """
    预处理公众号文本，去除无关内容。
    """
    # 去除多余空白
    content = re.sub(r'\n\s*\n', '\n\n', content)

    # 去除常见广告词
    ad_patterns = [
        r'关注.*?公众号',
        r'点击.*?链接',
        r'长按.*?二维码',
        r'广告',
        r'推广',
    ]
    for pattern in ad_patterns:
        content = re.sub(pattern, '', content, flags=re.IGNORECASE)

    # 限制长度，避免 token 超限
    max_len = 4000
    if len(content) > max_len:
        content = content[:max_len] + '\n\n[内容已截断...]'

    return content.strip()


# ===========================================
# 解析函数
# ===========================================

def parse_broadcast_content(content: str) -> list[dict]:
    """
    解析公众号播报内容，提取演出信息。

    Args:
        content: 公众号文章内容（手动转发的文本）

    Returns:
        演出信息列表
    """
    # 预处理
    cleaned = preprocess_text(content)

    if not cleaned:
        return []

    # 调用 LLM 解析
    messages = [
        {"role": "system", "content": "你是票务信息解析助手，只输出 JSON，不要其他内容。"},
        {"role": "user", "content": PARSE_PROMPT.format(content=cleaned)},
    ]

    try:
        response = llm.chat(messages, temperature=0.1, max_tokens=2000)
        raw = response["content"].strip()

        # 提取 JSON（兼容 ```json ... ``` 格式）
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
        if json_match:
            raw = json_match.group(1).strip()

        events = json.loads(raw)

        # 验证格式
        if not isinstance(events, list):
            return []

        # 添加元数据
        now = datetime.now()
        for event in events:
            event["source"] = "wechat"
            event["parsed_at"] = now.isoformat()

            # 如果没有开票日期，尝试推算
            if not event.get("sale_date"):
                if "明天" in str(event.get("notes", "")):
                    event["sale_date"] = (now + timedelta(days=1)).strftime("%Y-%m-%d")
                elif "今天" in str(event.get("notes", "")):
                    event["sale_date"] = now.strftime("%Y-%m-%d")

        return events

    except Exception as e:
        return [{"error": f"解析失败: {e}", "raw_content": content[:200]}]


# ===========================================
# 注册工具
# ===========================================

@register_tool(
    name="parse_wechat_broadcast",
    description="解析转发的公众号票务播报内容，提取演出信息。支持票小二线报等格式。",
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "公众号文章内容（手动转发的文本）",
            },
        },
        "required": ["content"],
    },
)
def parse_wechat_broadcast(content: str) -> str:
    """
    解析公众号播报内容。
    """
    events = parse_broadcast_content(content)

    if not events:
        return json.dumps({
            "message": "未识别到演出信息",
            "suggestion": "请确认转发的是票务类公众号内容"
        }, ensure_ascii=False)

    return json.dumps({
        "count": len(events),
        "events": events,
        "tip": "已解析完成，可使用 save_broadcast 保存到数据库"
    }, ensure_ascii=False, indent=2)
