"""
播报定时任务调度器

定时任务：
1. 每天 20:00 - 发送播报提醒
2. 每天 08:00 - 二次核对，动态创建开票提醒

开票提醒：
- 早上核对完后，根据开票时间动态创建一次性提醒任务
- 开票前 10 分钟提醒有工单的项目
- 提醒后自动删除任务
"""

from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from ticketpilot.agent.broadcast_manager import BroadcastManager
from ticketpilot.agent.notifier import Notifier
from ticketpilot.tools.time_utils import get_accurate_time


class BroadcastScheduler:
    """播报定时任务调度器"""

    def __init__(self, notifier: Notifier = None):
        self.scheduler = BackgroundScheduler()
        self.manager = BroadcastManager()
        self.notifier = notifier or Notifier()
        self._setup_jobs()

    def _setup_jobs(self):
        """设置定时任务"""

        # 任务 1：每天 20:00 发送晚间播报提醒
        self.scheduler.add_job(
            self._evening_broadcast,
            CronTrigger(hour=20, minute=0),
            id="evening_broadcast",
            name="晚间播报提醒",
        )

        # 任务 2：每天 08:00 二次核对 + 动态创建开票提醒
        self.scheduler.add_job(
            self._morning_check,
            CronTrigger(hour=8, minute=0),
            id="morning_check",
            name="早间核对",
        )

    def start(self):
        """启动调度器"""
        self.scheduler.start()
        print("📅 播报调度器已启动")
        print("  • 每天 20:00 - 晚间播报提醒")
        print("  • 每天 08:00 - 早间核对 + 创建开票提醒")

        # 重启补偿：立即重建当天的开票提醒
        # （job id + replace_existing 幂等，开票时间已过的自动跳过）
        self._create_ticket_reminders()

    def stop(self):
        """停止调度器"""
        self.scheduler.shutdown()
        print("📅 播报调度器已停止")

    def _evening_broadcast(self):
        """
        晚间播报任务（20:00）。

        提醒用户转发票小二线报。
        """
        now = datetime.now()
        print(f"[{now}] 执行晚间播报任务...")

        # 发送提醒
        self.notifier.send_markdown(
            "📢 **晚间播报提醒**\n\n"
            "请转发今日票小二线报公众号内容！\n"
            "转发后系统会自动解析并生成明日播报。"
        )

    def process_broadcast_after_forward(self, content: str) -> str:
        """
        用户转发公众号内容后，处理播报。

        解析/入库/报告逻辑委托 BroadcastManager.process_forwarded_content
        （与 Streamlit 播报解析页共享同一实现）；推送只留在 scheduler，
        避免 UI 页每次解析都推企业微信群。

        Args:
            content: 公众号文章内容

        Returns:
            播报报告
        """
        result = self.manager.process_forwarded_content(content)

        if not result["success"]:
            return f"❌ {result['error']}"

        # 推送播报
        self.notifier.send_markdown(result["report"]["summary"])

        return result["report"]["summary"]

    def _morning_check(self):
        """
        早间核对任务（08:00）。

        流程：
        1. 二次核对播报数据和大麦 API
        2. 有异常 → 推送更新
        3. 无异常 → 提醒核对完成
        4. 动态创建开票提醒任务
        """
        now = datetime.now()
        print(f"[{now}] 执行早间核对任务...")

        # 1. 二次核对
        result = self.manager.cross_check_with_damai()
        self.notifier.send_markdown(result["summary"])

        # 2. 动态创建开票提醒
        self._create_ticket_reminders()

    def _create_ticket_reminders(self):
        """
        动态创建开票提醒任务。

        遍历今天开票的播报，如果有客户工单，创建开票前 10 分钟的提醒。
        """
        now = get_accurate_time()
        today = now.strftime("%Y-%m-%d")

        # 获取今天开票的播报
        today_events = self.manager.broadcast_db.get_events_by_date(today)

        created_count = 0
        for event in today_events:
            sale_time_str = event.get("sale_time")
            if not sale_time_str:
                continue

            # 检查是否有工单
            orders = self.manager.get_orders_by_event(event["event_name"], event.get("city"))
            if not orders:
                continue

            try:
                # 解析开票时间
                sale_datetime = datetime.strptime(f"{today} {sale_time_str}", "%Y-%m-%d %H:%M")

                # 计算提醒时间（开票前 10 分钟）
                reminder_time = sale_datetime - timedelta(minutes=10)

                # 如果提醒时间已过，跳过
                if reminder_time <= now:
                    print(f"  跳过 {event['event_name']}（开票时间已过）")
                    continue

                # 创建一次性提醒任务
                job_id = f"ticket_reminder_{event['id']}"
                self.scheduler.add_job(
                    self._send_ticket_reminder,
                    DateTrigger(run_date=reminder_time),
                    id=job_id,
                    name=f"开票提醒：{event['event_name']}",
                    args=[event, orders],
                    replace_existing=True,
                )
                created_count += 1
                print(f"  ✅ 创建提醒：{event['event_name']} @ {reminder_time.strftime('%H:%M')}")

            except ValueError as e:
                print(f"  ❌ 解析时间失败：{event['event_name']} - {e}")
                continue

        if created_count > 0:
            self.notifier.send_markdown(f"📅 已创建 {created_count} 个开票提醒任务")

    def _send_ticket_reminder(self, event: dict, orders: list):
        """
        发送开票提醒。

        Args:
            event: 播报事件
            orders: 客户工单列表
        """
        now = datetime.now()
        print(f"[{now}] 发送开票提醒：{event['event_name']}")

        # 格式化提醒消息
        orders_info = ", ".join([
            f"{o['customer']}(×{o['quantity']})"
            for o in orders
        ])

        message = (
            f"⏰ **开票提醒**\n\n"
            f"🎤 {event['event_name']}\n"
            f"📍 {event.get('city', '未知')}\n"
            f"🕐 即将开票（还有 10 分钟）\n"
            f"👥 客户工单：{orders_info}\n\n"
            f"请准备抢票！"
        )

        self.notifier.send_markdown(message)

    def get_status(self) -> dict:
        """获取调度器状态"""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else "未调度",
            })

        return {
            "running": self.scheduler.running,
            "jobs": jobs,
        }
