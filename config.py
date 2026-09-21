"""
TicketPilot 配置管理

所有配置从环境变量读取，支持 .env 文件。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# 项目根目录
BASE_DIR = Path(__file__).parent

# ===========================================
# LLM 配置
# ===========================================
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# ===========================================
# RAG 配置
# ===========================================
KNOWLEDGE_BASE_DIR = BASE_DIR / "ticketpilot" / "knowledge_base"

# ===========================================
# 联网搜索配置（Tavily）
# ===========================================
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ===========================================
# 推送配置（企业微信机器人）
# ===========================================
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "")
