"""
Function Calling 工具模块

import 本包即完成全量工具注册（12 个工具）到 tools.base 的全局注册表：
每个子模块在 import 时通过 @register_tool 装饰器写入注册表，
入口（api/routes.py、frontend/app.py、main.py）只需 `import ticketpilot.tools`。

分层约束：
- base.py 是注册表本体（_tool_registry / register_tool / execute_tool）
- tools 子模块可以 import agent 层（如 broadcast_tools → broadcast_manager）
- 反向（agent → tools）必须函数内惰性 import，否则集中注册后
  任何先加载 agent 的进程都会撞循环导入
"""

from ticketpilot.tools import base  # noqa: F401 — 注册表本体
from ticketpilot.tools import time_utils  # noqa: F401 — check_time
from ticketpilot.tools import event_search  # noqa: F401 — search_event / get_damai_broadcast / check_approval
from ticketpilot.tools import knowledge_qa  # noqa: F401 — search_knowledge
from ticketpilot.tools import order_parser  # noqa: F401 — parse_order_image / get_my_orders / update_order_status
from ticketpilot.tools import wechat_parser  # noqa: F401 — parse_wechat_broadcast
from ticketpilot.tools import broadcast_tools  # noqa: F401 — get_evening_report / cross_check_broadcast / check_order_for_event
