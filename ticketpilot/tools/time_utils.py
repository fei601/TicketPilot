"""
时间相关工具

联网校时（NTP）+ 时间查询 + 窗口判断。

代拍场景下时间精度至关重要：
- 开票时间精确到秒，慢 1 秒可能就抢不到
- 退票窗口有明确截止时间
- 锁票超时需要精确计时

使用 NTP 服务器获取标准时间，避免系统时间不准导致误判。
"""

import socket
import struct
import time
from datetime import datetime, timedelta, timezone

import ntplib

from ticketpilot.tools.base import register_tool

# ===========================================
# NTP 服务器列表（国内优先）
# ===========================================

NTP_SERVERS = [
    "ntp.aliyun.com",       # 阿里云（推荐，国内最快）
    "ntp.tencent.com",      # 腾讯云
    "cn.ntp.org.cn",        # 国家授时中心
    "ntp1.aliyun.com",      # 阿里云备选
    "time.windows.com",     # 微软（备选）
]

# NTP 时间缓存（避免每次都请求 NTP 服务器）
_ntp_cache = {
    "offset": None,         # 与系统时间的偏移量（秒）
    "last_sync": None,      # 上次同步时间
    "server": None,         # 使用的 NTP 服务器
}

# 缓存有效期（秒），超过后重新同步
CACHE_TTL = 300  # 5 分钟


# ===========================================
# NTP 联网校时
# ===========================================

def sync_ntp() -> dict:
    """
    从 NTP 服务器同步时间。

    Returns:
        {
            "success": bool,
            "ntp_time": str,        # NTP 标准时间
            "system_time": str,     # 系统时间
            "offset_seconds": float, # 偏移量（正=系统慢了，负=系统快了）
            "server": str,          # 使用的 NTP 服务器
        }
    """
    global _ntp_cache

    client = ntplib.NTPClient()

    for server in NTP_SERVERS:
        try:
            response = client.request(server, timeout=3)

            # 计算偏移量
            offset = response.offset

            # 更新缓存
            _ntp_cache["offset"] = offset
            _ntp_cache["last_sync"] = time.time()
            _ntp_cache["server"] = server

            ntp_time = datetime.now(timezone.utc) + timedelta(seconds=offset)
            system_time = datetime.now(timezone.utc)

            return {
                "success": True,
                "ntp_time": ntp_time.strftime("%Y-%m-%d %H:%M:%S"),
                "system_time": system_time.strftime("%Y-%m-%d %H:%M:%S"),
                "offset_seconds": round(offset, 3),
                "server": server,
            }

        except Exception:
            continue

    # 所有 NTP 服务器都失败
    return {
        "success": False,
        "error": "所有 NTP 服务器均不可用，使用系统时间",
        "ntp_time": None,
        "system_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "offset_seconds": 0,
        "server": None,
    }


def get_accurate_time() -> datetime:
    """
    获取准确的当前时间（优先使用 NTP 校时）。

    如果 NTP 缓存过期，会自动重新同步。

    Returns:
        校准后的 datetime 对象（本地时间）
    """
    global _ntp_cache

    # 检查缓存是否过期
    need_sync = (
        _ntp_cache["offset"] is None
        or _ntp_cache["last_sync"] is None
        or (time.time() - _ntp_cache["last_sync"]) > CACHE_TTL
    )

    if need_sync:
        sync_ntp()

    # 应用偏移量
    if _ntp_cache["offset"] is not None:
        return datetime.now() + timedelta(seconds=_ntp_cache["offset"])
    else:
        # NTP 不可用，降级使用系统时间
        return datetime.now()


def get_time_source() -> str:
    """获取当前时间来源标识"""
    if _ntp_cache["server"]:
        return f"NTP ({_ntp_cache['server']})"
    return "系统时间（NTP 不可用）"


# ===========================================
# 注册工具
# ===========================================

@register_tool(
    name="check_time",
    description="查询当前精确时间（NTP 联网校时）、计算时间差、判断是否在某个时间窗口内。用于开票倒计时、退票窗口判断等场景。",
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["now", "diff", "check_window", "sync"],
                "description": "操作类型：now=当前时间, diff=计算时间差, check_window=检查是否在窗口内, sync=强制同步NTP",
            },
            "target_time": {
                "type": "string",
                "description": "目标时间，格式 YYYY-MM-DD HH:MM",
            },
            "window_hours": {
                "type": "integer",
                "description": "时间窗口（小时），用于 check_window",
            },
        },
        "required": ["action"],
    },
)
def check_time(
    action: str,
    target_time: str | None = None,
    window_hours: int | None = None,
) -> str:
    """
    时间相关工具（NTP 联网校时）。

    Args:
        action: 操作类型
        target_time: 目标时间字符串
        window_hours: 时间窗口小时数

    Returns:
        时间信息字符串
    """
    # 强制同步 NTP
    if action == "sync":
        result = sync_ntp()
        if result["success"]:
            return (
                f"NTP 同步成功\n"
                f"NTP 时间：{result['ntp_time']}\n"
                f"系统时间：{result['system_time']}\n"
                f"偏移量：{result['offset_seconds']} 秒\n"
                f"NTP 服务器：{result['server']}"
            )
        else:
            return f"NTP 同步失败：{result['error']}"

    # 获取校准后的当前时间
    now = get_accurate_time()
    source = get_time_source()

    if action == "now":
        return f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（来源：{source}）"

    if action == "diff":
        if not target_time:
            return "错误：diff 操作需要 target_time 参数"
        try:
            target = datetime.strptime(target_time, "%Y-%m-%d %H:%M")
            delta = target - now
            total_seconds = int(delta.total_seconds())

            if total_seconds > 0:
                days = total_seconds // 86400
                hours = (total_seconds % 86400) // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60

                parts = []
                if days > 0:
                    parts.append(f"{days} 天")
                if hours > 0:
                    parts.append(f"{hours} 小时")
                if minutes > 0:
                    parts.append(f"{minutes} 分钟")
                if seconds > 0 and days == 0:
                    parts.append(f"{seconds} 秒")

                time_str = " ".join(parts)
                return f"距离 {target_time} 还有 {time_str}（时间来源：{source}）"
            else:
                abs_seconds = abs(total_seconds)
                days = abs_seconds // 86400
                hours = (abs_seconds % 86400) // 3600
                return f"{target_time} 已过去 {days} 天 {hours} 小时"
        except ValueError:
            return "错误：时间格式应为 YYYY-MM-DD HH:MM"

    if action == "check_window":
        if not target_time:
            return "错误：check_window 操作需要 target_time 参数"
        if window_hours is None:
            window_hours = 48
        try:
            target = datetime.strptime(target_time, "%Y-%m-%d %H:%M")
            delta = target - now
            hours_remaining = delta.total_seconds() / 3600

            if 0 < hours_remaining <= window_hours:
                return (
                    f"是：在窗口内\n"
                    f"距离 {target_time} 还有 {hours_remaining:.1f} 小时\n"
                    f"窗口范围：{window_hours} 小时\n"
                    f"时间来源：{source}"
                )
            elif hours_remaining > window_hours:
                return f"否：距离 {target_time} 还有 {hours_remaining:.1f} 小时，不在 {window_hours} 小时窗口内"
            else:
                return f"否：{target_time} 已过去 {abs(hours_remaining):.1f} 小时"
        except ValueError:
            return "错误：时间格式应为 YYYY-MM-DD HH:MM"

    return f"错误：未知操作类型 '{action}'，支持 now/diff/check_window/sync"
