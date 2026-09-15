"""
消息推送模块

支持企业微信机器人 Webhook 推送。
"""

import json

import requests

import config


class Notifier:
    """消息推送器"""

    def __init__(self, webhook_url: str | None = None):
        self.webhook_url = webhook_url or config.WECHAT_WEBHOOK_URL

    def send_text(self, content: str) -> bool:
        """
        发送文本消息到企业微信群。

        Args:
            content: 消息内容

        Returns:
            是否发送成功
        """
        if not self.webhook_url:
            print(f"[Notifier] 未配置 webhook，消息仅打印：{content}")
            return False

        payload = {
            "msgtype": "text",
            "text": {
                "content": content,
            },
        }

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            result = response.json()
            return result.get("errcode") == 0
        except Exception as e:
            print(f"[Notifier] 发送失败：{e}")
            return False

    def send_markdown(self, content: str) -> bool:
        """
        发送 Markdown 消息到企业微信群。

        Args:
            content: Markdown 格式内容

        Returns:
            是否发送成功
        """
        if not self.webhook_url:
            print(f"[Notifier] 未配置 webhook，Markdown 消息仅打印：{content}")
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": content,
            },
        }

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            result = response.json()
            return result.get("errcode") == 0
        except Exception as e:
            print(f"[Notifier] 发送失败：{e}")
            return False

    def send_ticket_reminder(self, events: list[dict]):
        """
        发送开票提醒。

        Args:
            events: 即将开票的演出列表
        """
        if not events:
            return

        lines = ["## 📅 明日开票提醒\n"]
        for event in events:
            lines.append(f"**{event.get('name', '未知演出')}**")
            lines.append(f"- 平台：{event.get('platform', '未知')}")
            lines.append(f"- 开票时间：{event.get('sale_time', '未知')}")
            lines.append(f"- 票价：{', '.join(str(p['price']) for p in event.get('prices', []))}")
            lines.append("")

        self.send_markdown("\n".join(lines))

    def send_order_status_update(self, order_id: int, status: str, event_name: str):
        """
        发送订单状态更新通知。

        Args:
            order_id: 订单 ID
            status: 新状态
            event_name: 演出名称
        """
        status_text = {
            "success": "✅ 中票",
            "failed": "❌ 未中票",
            "cancelled": "🚫 已撤单",
            "refunded": "💰 已退款",
            "waiting_second": "⏳ 等待二开",
        }

        text = (
            f"订单状态更新\n"
            f"订单号：{order_id}\n"
            f"演出：{event_name}\n"
            f"状态：{status_text.get(status, status)}"
        )

        self.send_text(text)


# 全局通知器实例
_notifier: Notifier | None = None


def get_notifier() -> Notifier:
    """获取通知器单例"""
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier
