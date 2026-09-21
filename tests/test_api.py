"""
API 薄适配器冒烟测试（TestClient，不打真实网络）

routes.py 的 /chat 已委托 ChatService，这里验证：
- 适配器接线正确（reply/intent/route 透传）
- use_llm_router 默认值已切为 True
- ORDER_PARSE 回复脱敏（API 不再回明文 PII）
- /tools、/ 等端点不受影响

LLM/工具/大麦全部 monkeypatch，订单库用 tmp_path 临时文件库。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

import api.routes as routes_mod
from ticketpilot.agent.order_manager import OrderManager
from ticketpilot.application import chat_service as cs_mod
from ticketpilot.application.chat_service import ChatService
from ticketpilot.data.database import Database

client = TestClient(routes_mod.app)


@pytest.fixture
def mem_service(monkeypatch, tmp_path):
    """把 routes 模块级 chat_service 换成临时库实例，避免污染真实数据。
    用文件库而非 :memory: ——TestClient 在工作线程里跑端点，
    sqlite 内存库的单连接不允许跨线程使用。"""
    svc = ChatService(OrderManager(db=Database(tmp_path / "test.db")))
    monkeypatch.setattr(routes_mod, "chat_service", svc)
    return svc


def test_root():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_tools_endpoint_full_registry():
    resp = client.get("/tools")
    assert resp.status_code == 200
    names = {t["function"]["name"] for t in resp.json()["tools"]}
    # 集中注册后的全量工具（API 进程曾因散装 import 只见 3-4 个）
    assert {"search_event", "parse_order_image", "get_my_orders",
            "update_order_status", "search_knowledge",
            "get_damai_broadcast", "get_evening_report"} <= names


def test_chat_general_keyword_router(mem_service, monkeypatch):
    monkeypatch.setattr(cs_mod.llm, "chat", lambda *a, **k: {"content": "你好！有什么可以帮你的？"})
    resp = client.post("/chat", json={"message": "你好", "use_llm_router": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "你好！有什么可以帮你的？"
    assert body["intent"] == "GENERAL"
    assert body["route"] == "general"


def test_chat_default_uses_llm_router(mem_service, monkeypatch):
    """use_llm_router 默认值已切为 True：不传字段时走 router.classify_intent"""
    from ticketpilot.core.router import IntentType

    called = []

    def fake_classify(user_input, context=""):
        called.append(user_input)
        return IntentType.GENERAL

    monkeypatch.setattr(cs_mod.router, "classify_intent", fake_classify)
    monkeypatch.setattr(cs_mod.llm, "chat", lambda *a, **k: {"content": "ok"})

    resp = client.post("/chat", json={"message": "你好"})
    assert resp.status_code == 200
    assert called == ["你好"]


def test_chat_order_parse_masked(mem_service, monkeypatch):
    """行为变更：API 的 ORDER_PARSE 回复从明文改脱敏"""
    monkeypatch.setattr(mem_service, "build_sale_alerts", lambda new_orders=None: "")
    order_json = json.dumps({
        "customer_name": "张三",
        "event_name": "上海周杰伦演唱会",
        "event_date": "2026-10-01",
        "ticket_type": "1280",
        "quantity": 2,
        "seats": "连座",
        "budget": "13800138000",
        "notes": "张三310101199001011234",
    })
    monkeypatch.setattr(cs_mod.llm, "chat", lambda *a, **k: {"content": order_json})

    resp = client.post("/chat", json={
        "message": "上海周杰伦演唱会 10.1 1280连座两张 张三13800138000",
        "use_llm_router": False,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "order_parse"
    assert "已整理 1 条订单" in body["reply"]
    assert "13800138000" not in body["reply"]
    assert "138****8000" in body["reply"]
    assert "310101199001011234" not in body["reply"]


def test_chat_order_manage_summary_no_llm(mem_service):
    """summary 子分支纯本地：无 LLM 也能出状态汇总表"""
    resp = client.post("/chat", json={"message": "标记中票", "use_llm_router": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "order_manage"
    assert "| 待抢票 | 0 |" in body["reply"]
    assert "| **总计** | **0** |" in body["reply"]
