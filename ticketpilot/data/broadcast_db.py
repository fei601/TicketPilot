"""
播报数据存储

存储从公众号解析的演出播报信息，用于：
- 每日播报整理
- 开票提醒
- 与大麦数据对比（二次检索）
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from ticketpilot.data.models import BroadcastEvent


class BroadcastDB:
    """播报数据管理"""

    def __init__(self, db_path: str | Path = "data/ticketpilot.db"):
        self.db_path = Path(db_path)
        self._is_memory = str(db_path) == ":memory:"
        self._conn = None

        if self._is_memory:
            self._conn = sqlite3.connect(":memory:")
            self._conn.row_factory = sqlite3.Row
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._is_memory:
            return self._conn
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _close_conn(self, conn: sqlite3.Connection):
        if not self._is_memory:
            conn.close()

    def _init_db(self):
        """初始化播报表"""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_name TEXT NOT NULL,
                artist TEXT,
                city TEXT,
                venue TEXT,
                show_date TEXT,
                sale_date TEXT,
                sale_time TEXT,
                platform TEXT,
                prices TEXT,
                notes TEXT,
                source TEXT DEFAULT 'wechat',
                parsed_at TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()

    def save_events(self, events: list[dict]) -> list[int]:
        """
        保存解析的演出信息。

        Args:
            events: 解析的演出信息列表

        Returns:
            保存的记录 ID 列表
        """
        now = datetime.now().isoformat()
        conn = self._get_conn()
        ids = []

        try:
            for event in events:
                if "error" in event:
                    continue

                cursor = conn.execute("""
                    INSERT INTO broadcasts (
                        event_name, artist, city, venue,
                        show_date, sale_date, sale_time,
                        platform, prices, notes,
                        source, parsed_at, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event.get("event_name"),
                    event.get("artist"),
                    event.get("city"),
                    event.get("venue"),
                    event.get("show_date"),
                    event.get("sale_date"),
                    event.get("sale_time"),
                    event.get("platform"),
                    event.get("prices"),
                    event.get("notes"),
                    event.get("source", "wechat"),
                    event.get("parsed_at", now),
                    "pending",
                    now,
                ))
                ids.append(cursor.lastrowid)

            conn.commit()
            return ids
        finally:
            self._close_conn(conn)

    def get_events_by_date(self, sale_date: str) -> list[dict]:
        """获取指定日期开票的演出"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM broadcasts WHERE sale_date = ? ORDER BY sale_time",
                (sale_date,)
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close_conn(conn)

    def get_tomorrow_events(self) -> list[dict]:
        """获取明天开票的演出"""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        return self.get_events_by_date(tomorrow)

    def get_today_events(self) -> list[dict]:
        """获取今天开票的演出"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.get_events_by_date(today)

    def get_pending_events(self) -> list[dict]:
        """获取待处理的播报"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM broadcasts WHERE status = 'pending' ORDER BY sale_date, sale_time"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close_conn(conn)

    def update_status(self, event_id: int, status: str) -> bool:
        """更新播报状态"""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "UPDATE broadcasts SET status = ? WHERE id = ?",
                (status, event_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            self._close_conn(conn)

    def cleanup_old_events(self, days: int = 30):
        """清理过期数据"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM broadcasts WHERE created_at < ?",
                (cutoff,)
            )
            conn.commit()
            return cursor.rowcount
        finally:
            self._close_conn(conn)
