"""
订单管理 Agent

处理订单的创建、查询、状态更新等业务逻辑。
"""

import json

from ticketpilot.core import llm, prompts
from ticketpilot.data.constants import CITIES
from ticketpilot.data.database import Database
from ticketpilot.data.models import Order, OrderStatus


class OrderManager:
    """订单管理器"""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    def parse_order_from_text(self, text: str) -> Order:
        """
        用 LLM 从自然语言中解析订单信息。

        Args:
            text: 用户输入的零散信息

        Returns:
            解析后的 Order 对象
        """
        messages = [
            {"role": "system", "content": prompts.ORDER_PARSE_PROMPT},
            {"role": "user", "content": text},
        ]

        response = llm.chat(messages, temperature=0.1, max_tokens=500)

        # 提取 JSON
        content = response["content"]
        try:
            from ticketpilot.core.utils import extract_json
            data = extract_json(content)
            if isinstance(data, list) and data:
                # LLM 偶尔输出 [{...}] 而非单对象，取第一个元素
                data = data[0]
            if isinstance(data, dict):
                return Order(**data)
            else:
                # 解析失败时返回一个基础订单
                return Order(
                    customer_name="未知",
                    event_name=text[:50],
                    notes=f"解析失败，原始文本：{text}",
                )
        except Exception as e:
            # 解析失败时返回一个基础订单
            return Order(
                customer_name="未知",
                event_name=text[:50],
                notes=f"解析失败，原始文本：{text}",
            )

    def parse_multiple_orders(self, text: str) -> list[Order]:
        """
        从文本中解析多个订单。
        按行或换行符拆分，每段独立解析。

        Args:
            text: 包含多个订单的文本

        Returns:
            订单列表
        """
        import re

        # 城市列表用于检测新订单（统一用 constants.CITIES，与拆单/艺人剥离同源）
        cities = CITIES

        # 按换行符拆分（用户通常每行一个订单）
        lines = text.strip().split('\n')

        # 合并行：只有当行以城市名开头或包含明确的开票关键词时才认为是新订单
        orders_text = []
        current = ""
        for line in lines:
            line = line.strip()
            if not line:
                if current:
                    orders_text.append(current)
                    current = ""
                continue

            # 检测是否是新订单的开始
            is_new_order = False
            # 以城市名开头
            if any(line.startswith(city) for city in cities):
                is_new_order = True
            # 包含"开"字（一开、二开）且有日期
            elif "开" in line and re.search(r'\d+[./]\d+|\d+月', line):
                is_new_order = True
            # 包含"演唱会"、"音乐节"等关键词
            elif any(kw in line for kw in ["演唱会", "音乐节", "巡演"]):
                is_new_order = True
            # 以"姓名："开头，且当前行没有其他订单信息（避免把同一订单的姓名行当成新订单）
            elif (line.startswith("姓名") or line.startswith("姓名：")) and not current:
                is_new_order = True

            if is_new_order and current:
                orders_text.append(current)
                current = line
            else:
                current = current + " " + line if current else line

        if current:
            orders_text.append(current)

        # 解析每个订单
        orders = []
        for order_text in orders_text:
            order_text = order_text.strip()
            if len(order_text) > 10:  # 太短的忽略
                order = self.parse_order_from_text(order_text)
                orders.append(order)

        return orders

    def save_order(self, order: Order) -> int:
        """保存订单到数据库"""
        return self.db.add_order(order)

    def update_order(self, order_id: int, **kwargs) -> bool:
        """更新订单的指定字段"""
        return self.db.update_order(order_id, **kwargs)

    def delete_order(self, order_id: int) -> bool:
        """删除订单"""
        return self.db.delete_order(order_id)

    def get_order(self, order_id: int) -> Order | None:
        """获取订单"""
        return self.db.get_order(order_id)

    def get_all_orders(self, status: OrderStatus | None = None) -> list[Order]:
        """获取所有订单"""
        return self.db.get_all_orders(status)

    def update_status(self, order_id: int, status: OrderStatus) -> bool:
        """更新订单状态"""
        return self.db.update_order_status(order_id, status)

    def confirm_order(self, order_id: int) -> bool:
        """确认草稿（与抢票状态无关，只翻转 confirmed 位）"""
        return self.db.confirm_order(order_id)

    def mark_success(self, order_id: int) -> bool:
        """标记为中票"""
        return self.update_status(order_id, OrderStatus.SUCCESS)

    def mark_failed(self, order_id: int) -> bool:
        """标记为未中票"""
        return self.update_status(order_id, OrderStatus.FAILED)

    def mark_cancelled(self, order_id: int) -> bool:
        """标记为已撤单"""
        return self.update_status(order_id, OrderStatus.CANCELLED)

    def mark_refunded(self, order_id: int) -> bool:
        """标记为已退款"""
        return self.update_status(order_id, OrderStatus.REFUNDED)

    def mark_waiting_second(self, order_id: int) -> bool:
        """标记为等待二开"""
        return self.update_status(order_id, OrderStatus.WAITING_SECOND)

    def get_orders_for_tomorrow(self) -> list[Order]:
        """
        获取明天开票的订单（需要与演出信息关联）。

        这里返回所有 pending 状态的订单作为示例。
        实际实现需要根据演出的 sale_time 筛选。
        """
        return self.db.get_all_orders(OrderStatus.PENDING)

    def get_status_summary(self) -> dict:
        """获取订单状态汇总"""
        all_orders = self.db.get_all_orders()
        summary = {status.value: 0 for status in OrderStatus}
        for order in all_orders:
            summary[order.status.value] += 1
        summary["total"] = len(all_orders)
        return summary
