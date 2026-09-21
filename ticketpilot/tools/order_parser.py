"""
订单信息解析工具

功能：
1. 从图片（聊天记录截屏）解析订单，统一走 Order 模型并自动落库
2. 订单查询 / 状态更新工具
3. 数据本地存储，回复一律脱敏，不外泄

说明：文本订单解析统一走 agent/order_manager.parse_multiple_orders
（prompts.ORDER_PARSE_PROMPT + Order 模型）。本模块原有的私有 schema
文本管线（event_info/viewer_info/phone）已删除，避免双 schema 漂移。
"""

import json

from ticketpilot.core import llm, prompts
from ticketpilot.core.utils import extract_json
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


def _parse(messages: list) -> dict:
    """调用 LLM 并从回复中提取 JSON 对象（统一走 utils.extract_json）"""
    try:
        response = llm.chat(messages, temperature=0.1, max_tokens=500)
        data = extract_json(response["content"].strip())
        if isinstance(data, list) and data:
            # LLM 偶尔输出 [{...}] 而非单对象，取第一个元素
            data = data[0]
        if isinstance(data, dict):
            return data
        return {"error": "LLM 输出中未提取到有效 JSON"}
    except Exception as e:
        return {"error": f"解析失败: {e}"}


def format_order_masked(order: Order, order_id: int | None = None, idx: int | None = None) -> str:
    """
    生成脱敏的订单确认文本（聊天回复统一格式）。

    Args:
        order: 订单对象
        order_id: 数据库订单号（可选）
        idx: 多订单时的序号（可选，与 order_id 同时给出才显示"订单 N"头）
    """
    from ticketpilot.core.privacy import mask_phone, mask_pii_in_text

    if idx is not None and order_id is not None:
        header = f"订单 {idx}（订单号：{order_id}）\n\n"
    elif order_id is not None:
        header = f"订单号：{order_id}\n\n"
    else:
        header = ""

    # 观影人行（notes 每行"姓名 身份证号"）
    viewers_text = order.notes or order.customer_name or ""
    viewer_lines = [line.strip() for line in viewers_text.split("\n") if line.strip()]
    viewer_count = len(viewer_lines) or 1

    # 第一行：城市+演出 日期 价位 连坐/张数
    event_line = order.event_name or "未知演出"
    if order.event_date:
        event_line += f" {order.event_date}"
    if order.ticket_type:
        event_line += f" {order.ticket_type}"
    if order.seats and "连" in order.seats:
        event_line += f" 连坐{viewer_count}张"
    else:
        event_line += " 单张" if viewer_count == 1 else f" {viewer_count}张"

    lines = [header + event_line, "", "身份信息："]
    for line in viewer_lines:
        lines.append(mask_pii_in_text(line))

    # 联系电话（budget 字段按约定存电话；仅 11 位才脱敏，
    # 避免 mask_phone 把"待补充"这类占位符打成"***"）
    phone_raw = (order.budget or "").strip()
    phone = mask_phone(phone_raw) if len(phone_raw) == 11 else (phone_raw or "待补充")

    lines += ["", f"联系电话：{phone}", f"平台：{order.platform or '全平台'}"]
    return "\n".join(lines)


def parse_order_image(image_base64: str) -> dict:
    """
    解析订单图片（聊天记录截屏），成功后自动保存到数据库。

    与文本管线共用 prompts.ORDER_PARSE_PROMPT 和 Order 模型。

    Args:
        image_base64: 图片的 base64 编码

    Returns:
        {"success": True, "order_id": int, "formatted": 脱敏文本}
        或 {"error": ...}
    """
    messages = [
        {"role": "system", "content": prompts.ORDER_PARSE_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                },
                {"type": "text", "text": "请从图片中提取订单信息，按系统提示词要求输出 JSON。"},
            ],
        },
    ]

    data = _parse(messages)
    if "error" in data:
        return data

    try:
        order = Order(**data)
    except Exception as e:
        return {"error": f"解析结果不是合法订单: {e}"}

    order_id = _get_db().add_order(order)
    order.id = order_id

    return {
        "success": True,
        "order_id": order_id,
        "formatted": format_order_masked(order, order_id),
    }


@register_tool(
    name="parse_order_image",
    description="从聊天记录截屏中提取订单信息，自动保存到数据库并返回脱敏确认文本。",
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
    从图片中解析订单并保存。

    注意：返回内容一律脱敏，不包含原始身份证号/手机号。
    """
    result = parse_order_image(image_base64)

    if "error" in result:
        return json.dumps(result, ensure_ascii=False)

    return json.dumps({
        "success": True,
        "order_id": result["order_id"],
        "formatted": result["formatted"],
        "message": f"订单已从图片解析并保存（ID: {result['order_id']}）",
    }, ensure_ascii=False, indent=2)


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
