"""
TicketPilot 入口文件

启动 FastAPI 后端服务。
"""

import sys
from pathlib import Path

# 将项目根目录加入 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn

from api.routes import app
from ticketpilot.tools import event_search, time_utils, wechat_parser, broadcast_tools, order_parser, knowledge_qa  # noqa: F401 — 触发工具注册
from ticketpilot.agent.broadcast_scheduler import BroadcastScheduler
from ticketpilot.agent.notifier import Notifier

if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    print("=" * 60)
    print("TicketPilot - 票务 AI 智能助手")
    print("=" * 60)
    print("免责声明：本项目仅供学习研究，严禁商业使用")
    print("详细条款: DISCLAIMER.md")
    print("=" * 60)

    # 启动播报调度器
    notifier = Notifier()
    broadcast_scheduler = BroadcastScheduler(notifier)
    broadcast_scheduler.start()

    print("API 文档: http://localhost:8000/docs")
    print("前端界面: streamlit run frontend/app.py")

    uvicorn.run(
        "api.routes:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
