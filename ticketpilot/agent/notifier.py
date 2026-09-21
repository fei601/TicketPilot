"""
消息推送模块

支持企业微信机器人 Webhook 推送。
"""

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
