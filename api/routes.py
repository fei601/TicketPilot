"""
FastAPI 路由定义

提供 RESTful API 接口。
"""

import json
import logging

from fastapi import FastAPI, HTTPException

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
from pydantic import BaseModel

from ticketpilot.core import llm, prompts, router
from ticketpilot.core.router import IntentType
from ticketpilot.rag.retriever import retrieve_from_knowledge
from ticketpilot.tools.base import execute_tool, get_all_tool_schemas
from ticketpilot.tools import event_search, time_utils  # noqa: F401 — 触发工具注册
from ticketpilot.agent.order_manager import OrderManager
from ticketpilot.data.models import OrderStatus

app = FastAPI(
    title="TicketPilot API",
    description="面向票务工作者的 AI 智能助手 API",
    version="0.1.0",
)

order_manager = OrderManager()


# ===========================================
# 请求/响应模型
# ===========================================

class ChatRequest(BaseModel):
    """对话请求"""
    message: str
    use_llm_router: bool = False  # 是否用 LLM 做意图分类（默认用关键词）


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
    主对话接口。

    根据用户意图自动路由到不同处理逻辑：
    - ORDER_PARSE: 订单信息整理
    - EVENT_QUERY: 演出信息查询
    - KNOWLEDGE_QA: 知识库问答
    - ORDER_MANAGE: 订单管理
    - GENERAL: 通用对话
    """
    user_input = request.message

    # 1. 意图分类
    if request.use_llm_router:
        intent = router.classify_intent(user_input)
    else:
        intent = router.classify_intent_simple(user_input)

    # 2. 根据意图路由处理
    if intent == IntentType.ORDER_PARSE:
        order = order_manager.parse_order_from_text(user_input)
        order_id = order_manager.save_order(order)
        reply = (
            f"已整理订单并保存（订单号：{order_id}）：\n"
            f"- 客户：{order.customer_name}\n"
            f"- 演出：{order.event_name}\n"
            f"- 票种：{order.ticket_type or '未指定'}\n"
            f"- 数量：{order.quantity}\n"
            f"- 平台：{order.platform or '未指定'}"
        )
        return ChatResponse(reply=reply, intent=intent.value, route="order_parse")

    elif intent == IntentType.EVENT_QUERY:
        # 演出信息查询：优先使用 LLM 工具调用，失败则直接提取关键词查询
        logger.info(f"[EVENT_QUERY] 用户输入: {user_input}")

        # 方案1: 尝试让 LLM 调用工具
        event_system_prompt = prompts.SYSTEM_PROMPT + """

## 重要指令
用户正在查询演出信息。你必须调用 search_event 工具，不要直接回复。
调用 search_event 工具时，使用用户输入中的关键词作为 search_query 参数。
"""
        messages = [
            {"role": "system", "content": event_system_prompt},
            {"role": "user", "content": user_input},
        ]

        # 提取关键词用于工具调用（备用方案）
        import re
        keywords = re.sub(r'什么时候|开票|余票|票价|查询|搜索|票|演出|演唱会|音乐节', '', user_input).strip()
        if not keywords:
            keywords = user_input

        response = llm.chat(messages, tools=get_all_tool_schemas())

        # 如果 LLM 调用了工具
        if response.get("tool_calls"):
            logger.info(f"[EVENT_QUERY] LLM 成功调用工具")
            tool_results = []
            tool_calls_for_msg = []
            for tc in response["tool_calls"]:
                logger.info(f"[EVENT_QUERY] 调用工具: {tc['function']}, 参数: {tc['arguments']}")
                result = execute_tool(tc["function"], json.loads(tc["arguments"]))
                logger.info(f"[EVENT_QUERY] 工具返回: {result[:500]}...")
                tool_results.append(result)
                tool_calls_for_msg.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["function"], "arguments": tc["arguments"]}
                })

            # 把工具结果返回给 LLM 生成最终回复
            messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls_for_msg})
            for i, tc in enumerate(response["tool_calls"]):
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_results[i]})

            final_response = llm.chat(messages)
            reply = final_response.get("content", "查询完成")
        else:
            # 方案2: LLM 未调用工具，直接执行工具查询
            logger.info(f"[EVENT_QUERY] LLM 未调用工具，直接执行查询，关键词: {keywords}")
            try:
                tool_result = execute_tool("search_event", {"keyword": keywords, "city": None})
                logger.info(f"[EVENT_QUERY] 工具返回: {tool_result[:200]}...")

                # 解析工具结果，检查数据来源
                import json as json_lib
                try:
                    result_data = json_lib.loads(tool_result)
                    if isinstance(result_data, list) and result_data:
                        data_source = result_data[0].get("data_source", "unknown")
                        source_label = result_data[0].get("data_source_label", "未知来源")
                    else:
                        data_source = "unknown"
                        source_label = "未知来源"
                except:
                    data_source = "unknown"
                    source_label = "未知来源"

                # 根据数据来源构建提示
                source_hint = ""
                if data_source == "damai":
                    source_hint = "\n\n✅ 数据来源：大麦播报站（官方数据），可直接信任。"
                elif data_source == "web":
                    source_hint = "\n\n⚠️ 数据来源：网络搜索（仅供参考）。请在回复开头明确标注「以下信息来自网络搜索，仅供参考，请以官方售票平台为准」。购票平台只列出大麦、猫眼、票星球等主流平台。"

                # 让 LLM 根据工具结果生成回复
                final_messages = [
                    {"role": "system", "content": prompts.SYSTEM_PROMPT + f"\n\n查询结果：\n{tool_result}{source_hint}"},
                    {"role": "user", "content": user_input},
                ]
                final_response = llm.chat(final_messages)
                reply = final_response.get("content", "查询完成，但无法生成回复")
            except Exception as e:
                logger.error(f"[EVENT_QUERY] 工具调用失败: {e}")
                reply = f"抱歉，查询演出信息时遇到问题：{str(e)}"

        return ChatResponse(reply=reply, intent=intent.value, route="event_query")

    elif intent == IntentType.KNOWLEDGE_QA:
        # 从知识库检索相关信息（无结果时返回空串，给出明确降级指令防止编造）
        context = retrieve_from_knowledge(user_input)
        if context:
            kb_hint = f"\n\n参考知识库内容：\n{context}"
        else:
            kb_hint = "\n\n知识库未检索到相关内容。请直接告知用户知识库暂无相关信息，不要编造。"
        messages = [
            {"role": "system", "content": prompts.SYSTEM_PROMPT + kb_hint},
            {"role": "user", "content": user_input},
        ]
        response = llm.chat(messages)
        return ChatResponse(reply=response["content"], intent=intent.value, route="knowledge_qa")

    elif intent == IntentType.ORDER_MANAGE:
        # 简单的订单管理（后续扩展）
        summary = order_manager.get_status_summary()
        reply = (
            f"当前订单状态：\n"
            f"- 总计：{summary['total']} 单\n"
            f"- 待抢票：{summary['pending']} 单\n"
            f"- 已中票：{summary['success']} 单\n"
            f"- 未中票：{summary['failed']} 单\n"
            f"- 已撤单：{summary['cancelled']} 单\n"
            f"- 已退款：{summary['refunded']} 单\n"
            f"- 等待二开：{summary['waiting_second']} 单"
        )
        return ChatResponse(reply=reply, intent=intent.value, route="order_manage")

    else:
        # 通用对话
        messages = [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            {"role": "user", "content": user_input},
        ]
        response = llm.chat(messages)
        return ChatResponse(reply=response["content"], intent=intent.value, route="general")


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
