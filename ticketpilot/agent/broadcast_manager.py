"""
播报管理器

整合公众号播报、客户工单、大麦 API，实现：
1. 晚间播报：解析公众号内容，对比客户工单
2. 早间核对：二次核对，异常更新
3. 开票提醒：开票前 10 分钟提醒有工单的项目
"""

import json
from datetime import timedelta

from ticketpilot.core import llm
from ticketpilot.data.broadcast_db import BroadcastDB
from ticketpilot.data.database import Database
from ticketpilot.tools.event_search import DamaiDataSource
from ticketpilot.tools.time_utils import get_accurate_time


class BroadcastManager:
    """播报管理器"""

    def __init__(self):
        self.broadcast_db = BroadcastDB()
        self.order_db = Database()
        self.damai = DamaiDataSource()

    def get_orders_by_event(self, event_name: str, city: str = None) -> list[dict]:
        """
        查询某场演出的客户工单。
        模糊匹配：演出名称包含关键词即可。
        """
        all_orders = self.order_db.get_all_orders()
        matched = []

        for order in all_orders:
            # 简单模糊匹配
            if event_name and event_name in order.event_name:
                matched.append({
                    "id": order.id,
                    "customer": order.customer_name,
                    "event": order.event_name,
                    "ticket_type": order.ticket_type,
                    "quantity": order.quantity,
                    "status": order.status.value,
                })
            elif order.event_name and order.event_name in event_name:
                matched.append({
                    "id": order.id,
                    "customer": order.customer_name,
                    "event": order.event_name,
                    "ticket_type": order.ticket_type,
                    "quantity": order.quantity,
                    "status": order.status.value,
                })

        return matched

    def generate_evening_report(self) -> dict:
        """
        生成晚间播报报告。

        流程：
        1. 获取今日解析的播报数据
        2. 对比客户工单
        3. 生成播报内容

        Returns:
            {
                "has_orders": True/False,
                "order_events": [...],  # 有工单的演出
                "other_events": [...],  # 其他演出
                "summary": "播报内容"
            }
        """
        now = get_accurate_time()
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        # 获取明天开票的播报
        tomorrow_events = self.broadcast_db.get_events_by_date(tomorrow)

        if not tomorrow_events:
            return {
                "has_orders": False,
                "order_events": [],
                "other_events": [],
                "summary": "明天暂无开票播报",
            }

        # 分类：有工单 / 无工单
        order_events = []
        other_events = []

        for event in tomorrow_events:
            orders = self.get_orders_by_event(event["event_name"], event.get("city"))
            if orders:
                event["orders"] = orders
                event["order_count"] = len(orders)
                order_events.append(event)
            else:
                other_events.append(event)

        # 生成播报文本
        summary = self._format_evening_report(order_events, other_events, tomorrow)

        return {
            "has_orders": len(order_events) > 0,
            "order_events": order_events,
            "other_events": other_events,
            "summary": summary,
        }

    def _format_evening_report(self, order_events: list, other_events: list, date: str) -> str:
        """格式化晚间播报"""
        lines = [f"📅 **明日播报** ({date})\n"]

        # 有工单的重点标注
        if order_events:
            lines.append("🔴 **有客户工单（重点）**")
            for event in order_events:
                orders_info = ", ".join([f"{o['customer']}(×{o['quantity']})" for o in event["orders"]])
                lines.append(
                    f"  • {event['event_name']} - {event.get('city', '未知')}\n"
                    f"    开票时间：{event.get('sale_time', '待定')}\n"
                    f"    客户工单：{orders_info}"
                )
            lines.append("")

        # 其他演出
        if other_events:
            lines.append("🟢 **其他演出**")
            for event in other_events:
                lines.append(
                    f"  • {event['event_name']} - {event.get('city', '未知')} - {event.get('sale_time', '待定')}"
                )

        return "\n".join(lines)

    def cross_check_with_damai(self) -> dict:
        """
        二次核对：播报数据 vs 大麦 API。

        检查内容：
        1. 开票时间是否变更
        2. 是否有新增演出
        3. 是否有取消的演出

        Returns:
            {
                "has_changes": True/False,
                "changes": [...],
                "summary": "核对结果"
            }
        """
        now = get_accurate_time()
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        # 获取播报数据
        broadcast_events = self.broadcast_db.get_events_by_date(tomorrow)

        if not broadcast_events:
            return {
                "has_changes": False,
                "changes": [],
                "summary": "无播报数据，跳过核对",
            }

        # 从大麦 API 获取明天的演出
        damai_events = self.damai.search("", None)

        # 对比逻辑
        changes = []
        for broadcast in broadcast_events:
            event_name = broadcast["event_name"]

            # 在大麦数据中查找
            found = False
            for damai_event in damai_events:
                if event_name and event_name in damai_event.get("name", ""):
                    found = True

                    # 检查开票时间是否变更
                    broadcast_sale_time = broadcast.get("sale_time")
                    damai_sale_time = damai_event.get("sale_time")

                    if broadcast_sale_time and damai_sale_time:
                        if broadcast_sale_time != damai_sale_time:
                            changes.append({
                                "type": "time_change",
                                "event": event_name,
                                "old_time": broadcast_sale_time,
                                "new_time": damai_sale_time,
                            })
                    break

            # 如果大麦没有，可能已取消或未上线
            if not found:
                changes.append({
                    "type": "not_found",
                    "event": event_name,
                    "note": "大麦未找到，可能未上线或已取消",
                })

        has_changes = len(changes) > 0
        summary = self._format_cross_check_result(changes, tomorrow)

        return {
            "has_changes": has_changes,
            "changes": changes,
            "summary": summary,
        }

    def _format_cross_check_result(self, changes: list, date: str) -> str:
        """格式化核对结果"""
        if not changes:
            return f"✅ {date} 播报核对完成，无异常"

        lines = [f"⚠️ {date} 播报核对发现异常：\n"]
        for change in changes:
            if change["type"] == "time_change":
                lines.append(f"  • {change['event']}：开票时间变更 {change['old_time']} → {change['new_time']}")
            elif change["type"] == "not_found":
                lines.append(f"  • {change['event']}：{change['note']}")

        return "\n".join(lines)
