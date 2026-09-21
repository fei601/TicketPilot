"""
订单信息解析工具

功能：
1. 解析零散的客户信息，整理成统一格式
2. 支持文本和图片（截屏）输入
3. 数据本地存储，不外泄
"""

import json
import re
from datetime import datetime

from ticketpilot.core import llm
from ticketpilot.data.database import Database
from ticketpilot.data.models import Order, OrderStatus
from ticketpilot.tools.base import register_tool

# 惰性数据库单例：import 本模块不再触发建库/建表（import 副作用清零）
_db = None


def _get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db

# 订单格式模板
ORDER_TEMPLATE = """演出场次：{event_info}
观影人信息：{viewer_info}
联系电话：{phone}
"""

# 解析 Prompt
PARSE_PROMPT = """你是一个票务订单信息整理助手。请从用户输入中提取以下信息：

1. **演出场次**：城市 + 艺人/演出名称 + 日期 + 票价 + 是否连坐
   - 示例：重庆凤凰传奇 10.16号 1380连坐
2. **观影人信息**：姓名 + 身份证号（可能有多个，每人一行）
   - 示例：张三110101199001011234
3. **联系电话**：手机号

输出 JSON 格式：
```json
{
    "event_info": "演出场次信息",
    "viewer_info": "观影人信息（多人用换行分隔）",
    "phone": "联系电话",
    "raw_event": "原始演出名称",
    "city": "城市",
    "date": "日期",
    "price": "票价",
    "quantity": "数量",
    "viewers": [
        {"name": "姓名", "id_card": "身份证号"}
    ]
}
```

注意：
- 如果某个字段无法确定，设置为空字符串
- 连坐 = 连座，表示要相邻座位
- 如果有多个观影人，viewer_info 中每人一行，格式为"姓名+身份证号"
- 日期尽量保留原始格式
- 身份证号是 18 位数字或 17 位数字+X

用户输入：
{input}
"""


def parse_order_text(text: str) -> dict:
    """
    解析订单文本信息。

    Args:
        text: 用户输入的零散信息

    Returns:
        解析后的订单信息
    """
    messages = [
        {"role": "system", "content": "你是票务订单整理助手，只输出 JSON，不要其他内容。"},
        {"role": "user", "content": PARSE_PROMPT.format(input=text)},
    ]

    try:
        response = llm.chat(messages, temperature=0.1, max_tokens=500)
        raw = response["content"].strip()

        # 提取 JSON
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
        if json_match:
            raw = json_match.group(1).strip()

        data = json.loads(raw)
        return data

    except Exception as e:
        return {"error": f"解析失败: {e}"}


def parse_order_image(image_base64: str) -> dict:
    """
    解析订单图片（聊天记录截屏）。

    Args:
        image_base64: 图片的 base64 编码

    Returns:
        解析后的订单信息
    """
    messages = [
        {
            "role": "system",
            "content": "你是票务订单整理助手，只输出 JSON，不要其他内容。"
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }
                },
                {
                    "type": "text",
                    "text": PARSE_PROMPT.format(input="请从图片中提取订单信息")
                }
            ]
        }
    ]

    try:
        response = llm.chat(messages, temperature=0.1, max_tokens=500)
        raw = response["content"].strip()

        # 提取 JSON
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
        if json_match:
            raw = json_match.group(1).strip()

        data = json.loads(raw)
        return data

    except Exception as e:
        return {"error": f"图片解析失败: {e}"}


def format_order(data: dict) -> str:
    """
    格式化订单信息。

    Args:
        data: 解析后的订单信息

    Returns:
        格式化的订单文本
    """
    return ORDER_TEMPLATE.format(
        event_info=data.get("event_info", ""),
        viewer_info=data.get("viewer_info", ""),
        phone=data.get("phone", ""),
    )


@register_tool(
    name="parse_order",
    description="解析客户订单信息，整理成统一格式。支持文本和聊天记录输入。",
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "客户信息文本（可以是零散信息或聊天记录）",
            },
        },
        "required": ["text"],
    },
)
def parse_order(text: str) -> str:
    """
    解析订单信息并整理格式。
    """
    # 解析信息
    data = parse_order_text(text)

    if "error" in data:
        return json.dumps(data, ensure_ascii=False)

    # 格式化
    formatted = format_order(data)

    return json.dumps({
        "success": True,
        "formatted": formatted,
        "parsed": data,
        "message": "订单信息已整理，是否保存到数据库？",
    }, ensure_ascii=False, indent=2)


@register_tool(
    name="parse_order_image",
    description="从聊天记录截屏中提取订单信息，整理成统一格式。",
    parameters={
        "type": "object",
        "properties": {
            "image_base64": {
                "type": "string",
                "description": "图片的 base64 编码",
            },
        },
        "required": ["image_base64"],
    },
)
def parse_order_image_tool(image_base64: str) -> str:
    """
    从图片中解析订单信息。
    """
    # 解析图片
    data = parse_order_image(image_base64)

    if "error" in data:
        return json.dumps(data, ensure_ascii=False)

    # 格式化
    formatted = format_order(data)

    return json.dumps({
        "success": True,
        "formatted": formatted,
        "parsed": data,
        "message": "订单信息已从图片提取，是否保存到数据库？",
    }, ensure_ascii=False, indent=2)


@register_tool(
    name="save_order",
    description="保存订单到数据库。",
    parameters={
        "type": "object",
        "properties": {
            "event_info": {
                "type": "string",
                "description": "演出场次信息",
            },
            "viewer_info": {
                "type": "string",
                "description": "观影人信息",
            },
            "phone": {
                "type": "string",
                "description": "联系电话",
            },
            "customer_name": {
                "type": "string",
                "description": "客户姓名（用于标识）",
            },
        },
        "required": ["event_info", "viewer_info", "phone", "customer_name"],
    },
)
def save_order(event_info: str, viewer_info: str, phone: str, customer_name: str) -> str:
    """
    保存订单到数据库。
    """
    try:
        order = Order(
            customer_name=customer_name,
            event_name=event_info,
            notes=f"观影人：{viewer_info}\n电话：{phone}",
        )
        order_id = _get_db().add_order(order)

        return json.dumps({
            "success": True,
            "order_id": order_id,
            "message": f"订单已保存，ID: {order_id}",
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"保存失败: {e}",
        }, ensure_ascii=False)


@register_tool(
    name="get_my_orders",
    description="查看所有订单列表。",
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "筛选状态（可选）：pending/success/failed/cancelled",
            },
        },
        "required": [],
    },
)
def get_my_orders(status: str = None) -> str:
    """
    获取订单列表。
    """
    try:
        order_status = OrderStatus(status) if status else None
        orders = _get_db().get_all_orders(order_status)

        order_list = []
        for o in orders:
            order_list.append({
                "id": o.id,
                "customer": o.customer_name,
                "event": o.event_name,
                "status": o.status.value,
                "created": o.created_at.strftime("%Y-%m-%d %H:%M"),
            })

        return json.dumps({
            "count": len(order_list),
            "orders": order_list,
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "error": f"查询失败: {e}",
        }, ensure_ascii=False)


@register_tool(
    name="update_order_status",
    description="更新订单状态（中票/未中票/撤单等）。",
    parameters={
        "type": "object",
        "properties": {
            "order_id": {
                "type": "integer",
                "description": "订单 ID",
            },
            "status": {
                "type": "string",
                "description": "新状态：pending/success/failed/cancelled/refunded/waiting_second",
            },
        },
        "required": ["order_id", "status"],
    },
)
def update_order_status(order_id: int, status: str) -> str:
    """
    更新订单状态。
    """
    try:
        new_status = OrderStatus(status)
        success = _get_db().update_order_status(order_id, new_status)

        if success:
            return json.dumps({
                "success": True,
                "message": f"订单 {order_id} 状态已更新为 {status}",
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "message": f"订单 {order_id} 不存在",
            }, ensure_ascii=False)

    except ValueError:
        return json.dumps({
            "success": False,
            "error": f"无效的状态: {status}",
        }, ensure_ascii=False)
