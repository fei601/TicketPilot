"""
播报相关工具

提供播报管理、核对等功能的工具接口。
"""

import json

from ticketpilot.agent.broadcast_manager import BroadcastManager
from ticketpilot.tools.base import register_tool

# 全局实例
_manager = BroadcastManager()


@register_tool(
    name="get_evening_report",
    description="获取晚间播报报告，包括明日开票演出和客户工单情况。",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def get_evening_report() -> str:
    """获取晚间播报报告"""
    report = _manager.generate_evening_report()
    return json.dumps(report, ensure_ascii=False, indent=2)


@register_tool(
    name="cross_check_broadcast",
    description="二次核对播报数据和大麦 API，检查是否有变更或异常。",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def cross_check_broadcast() -> str:
    """二次核对播报数据"""
    result = _manager.cross_check_with_damai()
    return json.dumps(result, ensure_ascii=False, indent=2)


@register_tool(
    name="check_order_for_event",
    description="查询某场演出是否有客户工单。",
    parameters={
        "type": "object",
        "properties": {
            "event_name": {
                "type": "string",
                "description": "演出名称",
            },
            "city": {
                "type": "string",
                "description": "城市（可选）",
            },
        },
        "required": ["event_name"],
    },
)
def check_order_for_event(event_name: str, city: str = None) -> str:
    """查询演出的客户工单"""
    orders = _manager.get_orders_by_event(event_name, city)

    if not orders:
        return json.dumps({
            "has_orders": False,
            "message": f"「{event_name}」暂无客户工单",
        }, ensure_ascii=False)

    return json.dumps({
        "has_orders": True,
        "count": len(orders),
        "orders": orders,
    }, ensure_ascii=False, indent=2)
