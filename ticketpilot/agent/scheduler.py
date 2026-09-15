"""
定时任务调度器

管理开票提醒、漏票监控等定时任务。
"""

from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


class TaskScheduler:
    """任务调度器"""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.jobs: dict[str, dict] = {}

    def start(self):
        """启动调度器"""
        if not self.scheduler.running:
            self.scheduler.start()

    def stop(self):
        """停止调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown()

    def add_daily_reminder(
        self,
        job_id: str,
        hour: int = 9,
        minute: int = 0,
        func=None,
    ):
        """
        添加每日提醒任务（如每天早上 9 点推送明日开票信息）。

        Args:
            job_id: 任务 ID
            hour: 小时
            minute: 分钟
            func: 执行的函数
        """
        trigger = CronTrigger(hour=hour, minute=minute)

        if func is None:
            func = self._default_reminder

        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
        )

        self.jobs[job_id] = {
            "type": "daily_reminder",
            "time": f"{hour:02d}:{minute:02d}",
            "created_at": datetime.now().isoformat(),
        }

    def add_interval_task(
        self,
        job_id: str,
        minutes: int = 30,
        func=None,
    ):
        """
        添加间隔任务（如每 30 分钟检查漏票）。

        Args:
            job_id: 任务 ID
            minutes: 间隔分钟数
            func: 执行的函数
        """
        trigger = IntervalTrigger(minutes=minutes)

        if func is None:
            func = self._default_check

        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
        )

        self.jobs[job_id] = {
            "type": "interval",
            "minutes": minutes,
            "created_at": datetime.now().isoformat(),
        }

    def remove_job(self, job_id: str):
        """移除任务"""
        try:
            self.scheduler.remove_job(job_id)
            self.jobs.pop(job_id, None)
        except Exception:
            pass

    def list_jobs(self) -> list[dict]:
        """列出所有任务"""
        result = []
        for job in self.scheduler.get_jobs():
            result.append({
                "id": job.id,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
                "info": self.jobs.get(job.id, {}),
            })
        return result

    @staticmethod
    def _default_reminder():
        """默认提醒函数（占位）"""
        print(f"[{datetime.now()}] 每日提醒任务执行")

    @staticmethod
    def _default_check():
        """默认检查函数（占位）"""
        print(f"[{datetime.now()}] 定时检查任务执行")


# 全局调度器实例
_scheduler: TaskScheduler | None = None


def get_scheduler() -> TaskScheduler:
    """获取调度器单例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler
