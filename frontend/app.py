"""
Streamlit 前端应用

提供 Web 聊天界面。
"""

import json
import sys
import logging
from pathlib import Path

# 将项目根目录加入 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

from ticketpilot.application.chat_service import ChatService
import ticketpilot.tools  # noqa: F401 — 触发全量工具注册（ChatService 工具循环需要全量注册表）
from ticketpilot.agent.broadcast_manager import BroadcastManager
from ticketpilot.agent.order_manager import OrderManager
from ticketpilot.data.models import OrderStatus
from ticketpilot.core.privacy import mask_phone, mask_pii_in_text

# 配置日志
logger = logging.getLogger(__name__)

# ===========================================
# 页面配置
# ===========================================

st.set_page_config(
    page_title="TicketPilot",
    page_icon="🎫",
    layout="wide",
)

st.title("🎫 TicketPilot")
st.caption("面向票务工作者的 AI 智能助手")

# ===========================================
# 用户配置持久化
# ===========================================

CONFIG_FILE = Path(__file__).parent.parent / "data" / "user_config.json"

def load_user_config():
    """加载用户配置"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载用户配置失败: {e}")
    return {"first_visit": True, "order_format": "default"}

def save_user_config(config):
    """保存用户配置"""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

# 加载配置
user_config = load_user_config()

# ===========================================
# 初始化
# ===========================================

if "messages" not in st.session_state:
    st.session_state.messages = []
    # 只有首次访问才显示欢迎消息和格式询问
    if user_config.get("first_visit", True):
        welcome_msg = """你好！我是 TicketPilot，你的票务智能助手。

请问你喜欢哪种订单整理格式？

1. 默认格式（推荐）：
```
上海周杰伦 1.1号 1380单张
全平台
张三123456789876543212
联系电话：12345678909
```

2. 如果是连坐，第一行会写成：1380连坐 2张

请回复"默认"或告诉我你喜欢的格式"""
        st.session_state.messages.append({
            "role": "assistant",
            "content": welcome_msg,
            "intent": "INIT"
        })
    else:
        # 非首次访问，显示简洁欢迎
        st.session_state.messages.append({
            "role": "assistant",
            "content": "你好！有什么可以帮你的？",
            "intent": "WELCOME"
        })

if "order_manager" not in st.session_state:
    st.session_state.order_manager = OrderManager()

# 编排服务：实例放 session_state（Streamlit 每次 rerun 换线程，不能用模块级单例），
# 与订单管理页/看板共享同一个 order_manager；大麦缓存随实例存活于会话内
if "chat_service" not in st.session_state:
    st.session_state.chat_service = ChatService(st.session_state.order_manager)

# 播报管理器（播报解析页用；与调度器同一套解析→入库→报告逻辑）
if "broadcast_manager" not in st.session_state:
    st.session_state.broadcast_manager = BroadcastManager()

if "order_format" not in st.session_state:
    st.session_state.order_format = user_config.get("order_format", "default")

# 会话记忆：最近创建的订单
if "last_order" not in st.session_state:
    st.session_state.last_order = None


# ===========================================
# 侧边栏
# ===========================================

with st.sidebar:
    st.header("功能菜单")

    page = st.radio(
        "选择功能",
        ["💬 智能对话", "📋 订单管理", "📊 数据看板", "📢 播报解析"],
    )

    st.divider()

    st.subheader("快捷操作")
    if st.button("查看订单状态"):
        summary = st.session_state.order_manager.get_status_summary()
        st.json(summary)

    if st.button("清空对话"):
        st.session_state.messages = []
        st.rerun()

    st.divider()

    # 今日/明日开票提醒（与聊天页共享 ChatService 的大麦缓存和匹配逻辑）
    st.subheader("⏰ 开票提醒")
    _svc = st.session_state.chat_service
    today_matched, tomorrow_matched = _svc.get_sale_matches()
    if _svc.damai_cache.get("data"):
        if today_matched:
            st.warning(f"🔴 今天有 {len(today_matched)} 个项目开抢！")
            for m in today_matched:
                st.write(f"- {m['order'].event_name} {m['sale'].get('sale_time', '')}")

        if tomorrow_matched:
            st.info(f"🟡 明天有 {len(tomorrow_matched)} 个项目开抢")
            for m in tomorrow_matched:
                st.write(f"- {m['order'].event_name} {m['sale'].get('sale_time', '')}")

        if not today_matched and not tomorrow_matched:
            st.success("✅ 近期没有需要抢票的订单")
    else:
        st.write("正在加载大麦数据...")

    st.divider()
    st.caption("v0.1.0 | 基于 LLM 的票务助手")


# ===========================================
# 智能对话页面
# ===========================================

if page == "💬 智能对话":
    # 显示历史消息
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if "intent" in msg:
                st.caption(f"意图：{msg['intent']}")

    # 用户输入
    if user_input := st.chat_input("输入消息，例如：帮我整理一下这个客户的订单..."):
        # 输入长度校验
        if len(user_input) > 5000:
            st.warning("输入过长，请缩短后重试（最大5000字符）")
            st.stop()

        # 显示用户消息
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # 处理请求
        with st.chat_message("assistant"):
            with st.spinner("思考中..."):
                try:
                    # 格式选择特判：欢迎语格式提问的固定应答，不进编排层
                    # （原实现挂在 ORDER_PARSE 分支下，LLM 分类器对"默认"也归 ORDER_PARSE）
                    if user_input in ["默认", "默认格式", "1", "default"]:
                        st.session_state.order_format = "default"
                        # 保存配置
                        user_config["order_format"] = "default"
                        user_config["first_visit"] = False
                        save_user_config(user_config)
                        reply = "好的，已设置为默认格式！\n\n以后整理订单会按这个格式显示：\n\n上海周杰伦 1.1号 1380单张\n全平台\n张三123456789876543212\n联系电话：12345678909\n\n请发送客户信息，我来帮你整理"
                        intent_value = "ORDER_PARSE"
                    else:
                        # 构建上下文（最近创建的订单）
                        context = ""
                        if st.session_state.last_order:
                            lo = st.session_state.last_order
                            context = f"最近创建的订单：{lo['event_name']} {lo['event_date'] or ''} {lo['ticket_type'] or ''}（订单号：{lo['id']}）"

                        # 意图分类 + 五个分支编排全部委托 ChatService（与 API /chat 共享实现）
                        result = st.session_state.chat_service.chat(user_input, context)
                        reply = result.reply
                        intent_value = result.intent

                        if result.intent == "ORDER_PARSE":
                            # 首次发送订单，标记已访问
                            if user_config.get("first_visit", True):
                                user_config["first_visit"] = False
                                save_user_config(user_config)
                            # 会话记忆：最近创建的订单（id 已由编排层回填）
                            if result.orders:
                                lo = result.orders[-1]
                                st.session_state.last_order = {
                                    "id": lo.id,
                                    "event_name": lo.event_name,
                                    "event_date": lo.event_date,
                                    "ticket_type": lo.ticket_type,
                                    "platform": lo.platform,
                                    "notes": lo.notes,
                                    "budget": lo.budget,
                                }

                    st.markdown(reply)
                    st.caption(f"意图：{intent_value}")

                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": reply,
                        "intent": intent_value,
                    })

                except Exception as e:
                    error_msg = f"处理出错：{e}"
                    st.error(error_msg)
                    st.session_state.messages.append({"role": "assistant", "content": error_msg})


# ===========================================
# 订单管理页面
# ===========================================

elif page == "📋 订单管理":
    st.header("订单管理")

    # 筛选
    col1, col2 = st.columns(2)
    with col1:
        status_filter = st.selectbox(
            "按状态筛选",
            ["全部"] + [s.value for s in OrderStatus],
        )

    # 获取订单
    status = OrderStatus(status_filter) if status_filter != "全部" else None
    orders = st.session_state.order_manager.get_all_orders(status)

    if orders:
        for idx, order in enumerate(orders, 1):
            # 解析城市（从 notes 中提取，如果是旧格式）
            city = ""
            clean_notes = order.notes or ""
            if "；" in clean_notes:
                parts = clean_notes.split("；")
                city = parts[0].strip()
                clean_notes = "；".join(parts[1:])

            # 构建第一行：城市+演出 日期 价位+连坐/单张+数量
            event_line = ""
            if city:
                event_line = city
            event_line += order.event_name or "未知演出"
            if order.event_date:
                event_line += f" {order.event_date}"
            if order.ticket_type:
                event_line += f" {order.ticket_type}"
            # 判断连坐还是单张
            if order.seats and "连" in (order.seats or ""):
                event_line += f" 连坐{order.quantity}张"
            else:
                event_line += " 单张" if order.quantity == 1 else f" {order.quantity}张"

            # 状态标签
            status_emoji = {
                "pending": "⏳",
                "success": "✅",
                "failed": "❌",
                "cancelled": "🚫",
                "refunded": "💰",
                "waiting_second": "🔄"
            }.get(order.status.value, "❓")

            with st.expander(f"{status_emoji} 订单 {idx} - {event_line} ({order.status.value})"):
                # 第一行：基本信息
                st.write(event_line)

                # 平台
                platform = order.platform or "全平台"
                st.write(f"平台：{platform}")

                # 身份信息（脱敏显示）
                st.write("身份信息：")
                if clean_notes:
                    # 清理格式并脱敏
                    for line in clean_notes.split('\n'):
                        line = line.strip()
                        if line:
                            # 去掉"身份证："前缀
                            if line.startswith("身份证："):
                                line = line[4:]
                            # 脱敏显示
                            st.write(mask_pii_in_text(line))
                else:
                    st.write("待补充")

                # 联系电话（脱敏显示）
                phone = order.budget
                if phone == order.ticket_type:
                    phone = "待补充"
                st.write(f"联系电话：{mask_phone(phone) if phone and len(phone or '') == 11 else (phone or '待补充')}")

                # 显示完整信息按钮
                if st.button("👁️ 显示完整信息", key=f"show_full_{order.id}"):
                    st.session_state[f"show_full_{order.id}"] = True

                if st.session_state.get(f"show_full_{order.id}"):
                    st.warning("⚠️ 以下为完整敏感信息")
                    st.code(f"联系电话：{order.budget or '未提供'}\n身份信息：{order.notes or '未提供'}")

                # 创建时间
                st.caption(f"创建时间：{order.created_at.strftime('%Y-%m-%d %H:%M')}")

                st.divider()

                # 编辑按钮
                if st.button("编辑订单", key=f"edit_btn_{order.id}"):
                    st.session_state[f"editing_{order.id}"] = not st.session_state.get(f"editing_{order.id}", False)

                # 编辑表单
                if st.session_state.get(f"editing_{order.id}"):
                    with st.form(key=f"edit_form_{order.id}"):
                        st.subheader("编辑订单信息")

                        new_event_name = st.text_input("演出名称", value=order.event_name or "")
                        new_event_date = st.text_input("日期", value=order.event_date or "")
                        new_ticket_type = st.text_input("票价", value=order.ticket_type or "")
                        new_platform = st.text_input("平台", value=order.platform or "全平台")
                        new_budget = st.text_input("联系电话", value=order.budget or "")
                        new_notes = st.text_area("身份信息（每行一个：姓名 身份证号）", value=order.notes or "")

                        submitted = st.form_submit_button("保存修改")
                        if submitted:
                            update_data = {}
                            if new_event_name != (order.event_name or ""):
                                update_data["event_name"] = new_event_name
                            if new_event_date != (order.event_date or ""):
                                update_data["event_date"] = new_event_date
                            if new_ticket_type != (order.ticket_type or ""):
                                update_data["ticket_type"] = new_ticket_type
                            if new_platform != (order.platform or ""):
                                update_data["platform"] = new_platform
                            if new_budget != (order.budget or ""):
                                update_data["budget"] = new_budget
                            if new_notes != (order.notes or ""):
                                update_data["notes"] = new_notes
                                # 更新数量
                                update_data["quantity"] = len([l for l in new_notes.split('\n') if l.strip()])

                            if update_data:
                                st.session_state.order_manager.update_order(order.id, **update_data)
                                st.success("订单已更新")
                                st.session_state[f"editing_{order.id}"] = False
                                st.rerun()
                            else:
                                st.info("未修改任何信息")

                st.divider()

                # 状态更新和删除按钮
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    new_status = st.selectbox(
                        "更新状态",
                        [s.value for s in OrderStatus],
                        index=[s.value for s in OrderStatus].index(order.status.value),
                        key=f"status_{order.id}",
                    )
                with col2:
                    if st.button("更新状态", key=f"update_{order.id}"):
                        st.session_state.order_manager.update_status(order.id, OrderStatus(new_status))
                        st.success(f"状态已更新为 {new_status}")
                        st.rerun()
                with col3:
                    if st.button("删除订单", key=f"delete_{order.id}", type="primary"):
                        st.session_state[f"confirm_delete_{order.id}"] = True

                # 确认删除
                if st.session_state.get(f"confirm_delete_{order.id}"):
                    st.warning(f"确定要删除「{order.event_name}」吗？")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("确认删除", key=f"confirm_yes_{order.id}", type="primary"):
                            st.session_state.order_manager.delete_order(order.id)
                            st.success("订单已删除")
                            st.session_state[f"confirm_delete_{order.id}"] = False
                            st.rerun()
                    with c2:
                        if st.button("取消", key=f"confirm_no_{order.id}"):
                            st.session_state[f"confirm_delete_{order.id}"] = False
                            st.rerun()
    else:
        st.info("暂无订单")


# ===========================================
# 数据看板页面
# ===========================================

elif page == "📊 数据看板":
    st.header("数据看板")

    summary = st.session_state.order_manager.get_status_summary()

    # 指标卡片
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总订单", summary["total"])
    col2.metric("待抢票", summary["pending"])
    col3.metric("已中票", summary["success"])
    col4.metric("未中票", summary["failed"])

    st.divider()

    col1, col2, col3 = st.columns(3)
    col1.metric("已撤单", summary["cancelled"])
    col2.metric("已退款", summary["refunded"])
    col3.metric("等待二开", summary["waiting_second"])


# ===========================================
# 播报解析页面
# ===========================================

elif page == "📢 播报解析":
    st.header("播报解析")
    st.caption("粘贴转发的票小二公众号内容：解析入库并生成明日播报报告。"
               "仅展示、不推送企业微信群（推送由调度器负责）。")

    content = st.text_area(
        "公众号内容",
        height=300,
        placeholder="在此粘贴公众号播报全文…",
    )

    if st.button("🔍 解析播报", type="primary", disabled=not content.strip()):
        with st.spinner("解析中..."):
            result = st.session_state.broadcast_manager.process_forwarded_content(content)

        if result["success"]:
            st.success(f"已解析并入库 {len(result['events'])} 条播报")
            st.markdown(result["report"]["summary"])
        else:
            st.error(result["error"])
