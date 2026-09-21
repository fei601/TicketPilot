"""
FastAPI 路由定义

提供 RESTful API 接口。
"""

import logging

from fastapi import FastAPI, HTTPException

# 配置日志（保证编排层/工具层的 INFO 日志在 API 进程可见）
logging.basicConfig(level=logging.INFO)
from pydantic import BaseModel

from ticketpilot.application.chat_service import ChatService
import ticketpilot.tools  # noqa: F401 — 触发全量工具注册
from ticketpilot.tools.base import get_all_tool_schemas
from ticketpilot.agent.order_manager import OrderManager
from ticketpilot.data.models import OrderStatus

app = FastAPI(
    title="TicketPilot API",
    description="面向票务工作者的 AI 智能助手 API",
    version="0.1.0",
)

order_manager = OrderManager()
# 五个意图分支的编排逻辑收敛在 ChatService（与 Streamlit 聊天页共享同一实现）
chat_service = ChatService(order_manager)


# ===========================================
# 请求/响应模型
# ===========================================

class ChatRequest(BaseModel):
    """对话请求"""
    message: str
    use_llm_router: bool = True  # 默认走 LLM 分类（失败自动回退关键词），与 Streamlit 一致


class ChatResponse(BaseModel):
    """对话响应"""
    reply: str
    intent: str
    route: str


class OrderParseRequest(BaseModel):
    """订单解析请求"""
    text: str


class OrderStatusRequest(BaseModel):
    """订单状态更新请求"""
    order_id: int
    status: str


# ===========================================
# API 路由
# ===========================================

@app.get("/")
def root():
    """健康检查"""
    return {"status": "ok", "service": "TicketPilot"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """
    主对话接口（薄适配器）。

    意图分类与五个分支的编排全部委托 ChatService：
    - ORDER_PARSE: 订单信息整理（脱敏回复 + 开抢提醒）
    - EVENT_QUERY: 演出信息查询（LLM 工具调用，失败降级直查）
    - KNOWLEDGE_QA: 知识库问答
    - ORDER_MANAGE: 订单管理（删除/查询/修改/汇总）
    - GENERAL: 通用对话（带工具循环）
    """
    result = chat_service.chat(request.message, use_llm_router=request.use_llm_router)
    return ChatResponse(reply=result.reply, intent=result.intent, route=result.route)


@app.post("/orders/parse")
def parse_order(request: OrderParseRequest):
    """解析订单信息"""
    order = order_manager.parse_order_from_text(request.text)
    order_id = order_manager.save_order(order)
    return {"order_id": order_id, "order": order.model_dump()}


@app.get("/orders")
def list_orders(status: str | None = None):
    """获取订单列表"""
    order_status = OrderStatus(status) if status else None
    orders = order_manager.get_all_orders(order_status)
    return {"orders": [o.model_dump() for o in orders]}


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    """获取单个订单"""
    order = order_manager.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    return {"order": order.model_dump()}


@app.put("/orders/{order_id}/status")
def update_order_status(order_id: int, request: OrderStatusRequest):
    """更新订单状态"""
    try:
        status = OrderStatus(request.status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效状态：{request.status}")

    success = order_manager.update_status(order_id, status)
    if not success:
        raise HTTPException(status_code=404, detail="订单不存在")

    return {"success": True, "order_id": order_id, "new_status": request.status}


@app.get("/tools")
def list_tools():
    """列出所有可用工具"""
    return {"tools": get_all_tool_schemas()}
