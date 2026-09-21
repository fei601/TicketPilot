"""
Streamlit 前端应用

提供 Web 聊天界面。
"""

import json
import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta

# 将项目根目录加入 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

from ticketpilot.core import llm, prompts, router
from ticketpilot.core.router import IntentType
from ticketpilot.rag.retriever import retrieve_from_knowledge
from ticketpilot.tools.base import execute_tool, get_all_tool_schemas
from ticketpilot.tools import event_search, time_utils  # noqa: F401
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

if "order_format" not in st.session_state:
    st.session_state.order_format = user_config.get("order_format", "default")

# 会话记忆：最近创建的订单
if "last_order" not in st.session_state:
    st.session_state.last_order = None

# 大麦数据缓存
if "damai_cache" not in st.session_state:
    st.session_state.damai_cache = {"data": None, "last_update": None}


# ===========================================
# 获取大麦数据（带缓存）
# ===========================================

def get_damai_data_cached():
    """获取大麦数据，带缓存（每小时刷新一次）"""
    import json
    from datetime import datetime, timedelta

    cache = st.session_state.damai_cache
    now = datetime.now()

    # 检查缓存是否过期（1小时）
    if cache["data"] and cache["last_update"]:
        if now - cache["last_update"] < timedelta(hours=1):
            return cache["data"]

    # 重新获取数据
    try:
        result = execute_tool("get_damai_broadcast", {})
        data = json.loads(result)
        st.session_state.damai_cache = {"data": data, "last_update": now}
        return data
    except Exception as e:
        print(f"获取大麦数据失败: {e}")
        return cache["data"]  # 返回旧缓存


def find_matching_orders(damai_data, target_date=None):
    """查找与大麦数据匹配的订单"""
    import re
    from datetime import datetime, timedelta

    if not damai_data:
        return []

    orders = st.session_state.order_manager.get_all_orders()
    upcoming = damai_data.get("即将开抢", [])

    if target_date is None:
        target_date = datetime.now()

    matched = []
    for item in upcoming:
        sale_time = str(item.get("sale_time", ""))
        item_name = str(item.get("name", ""))
        item_city = str(item.get("city", ""))

        # 检查是否是目标日期
        is_target_date = False

        # 格式1: "今天XX:XX开抢"
        if "今天" in sale_time and target_date.date() == datetime.now().date():
            is_target_date = True
        # 格式2: "明天XX:XX开抢"
        elif "明天" in sale_time and target_date.date() == (datetime.now() + timedelta(days=1)).date():
            is_target_date = True
        # 格式3: "X月X日XX:XX开抢"
        else:
            date_match = re.search(r'(\d+)月(\d+)日', sale_time)
            if date_match:
                sale_month = int(date_match.group(1))
                sale_day = int(date_match.group(2))
                if sale_month == target_date.month and sale_day == target_date.day:
                    is_target_date = True

        if not is_target_date:
            continue

        # 匹配订单
        for order in orders:
            order_name = (order.event_name or "").replace(" ", "")

            # 提取艺人名（去掉城市前缀）
            order_artist = order_name
            for city in ["上海", "南京", "北京", "广州", "深圳", "成都", "重庆", "杭州",
                         "武汉", "西安", "长沙", "青岛", "天津", "苏州", "郑州", "济南",
                         "合肥", "昆明", "大连", "厦门", "哈尔滨", "沈阳", "长春", "福州",
                         "南宁", "贵阳", "温州", "宁波", "绍兴", "临沂", "佛山", "南通", "常州"]:
                order_artist = order_artist.replace(city, "")

            # 双重匹配：艺人名 + 城市
            artist_match = any(artist in item_name for artist in [order_artist[:2], order_artist[:3]] if len(artist) > 1)
            city_match = any(city in order_name for city in [item_city] if city)

            if artist_match and city_match:
                matched.append({"order": order, "sale": item})

    return matched


# ===========================================
# 侧边栏
# ===========================================

with st.sidebar:
    st.header("功能菜单")

    page = st.radio(
        "选择功能",
        ["💬 智能对话", "📋 订单管理", "📊 数据看板"],
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

    # 今日/明日开票提醒
    st.subheader("⏰ 开票提醒")
    damai_data = get_damai_data_cached()
    if damai_data:
        today_matched = find_matching_orders(damai_data, datetime.now())
        tomorrow_matched = find_matching_orders(damai_data, datetime.now() + timedelta(days=1))

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
                    # 构建上下文（最近的对话和订单信息）
                    context = ""
                    if st.session_state.last_order:
                        lo = st.session_state.last_order
                        context = f"最近创建的订单：{lo['event_name']} {lo['event_date'] or ''} {lo['ticket_type'] or ''}（订单号：{lo['id']}）"

                    # 意图分类（使用 LLM，带上下文）
                    intent = router.classify_intent(user_input, context)

                    # 根据意图处理
                    if intent == IntentType.ORDER_PARSE:
                        # 检查是否是格式选择
                        if user_input in ["默认", "默认格式", "1", "default"]:
                            st.session_state.order_format = "default"
                            # 保存配置
                            user_config["order_format"] = "default"
                            user_config["first_visit"] = False
                            save_user_config(user_config)
                            reply = "好的，已设置为默认格式！\n\n以后整理订单会按这个格式显示：\n\n上海周杰伦 1.1号 1380单张\n全平台\n张三123456789876543212\n联系电话：12345678909\n\n请发送客户信息，我来帮你整理"
                        else:
                            # 首次发送订单，标记已访问
                            if user_config.get("first_visit", True):
                                user_config["first_visit"] = False
                                save_user_config(user_config)

                            # 尝试解析多个订单
                            orders = st.session_state.order_manager.parse_multiple_orders(user_input)

                            all_replies = []
                            for idx, order in enumerate(orders, 1):
                                order_id = st.session_state.order_manager.save_order(order)

                                # 保存最后创建的订单到会话记忆
                                st.session_state.last_order = {
                                    "id": order_id,
                                    "event_name": order.event_name,
                                    "event_date": order.event_date,
                                    "ticket_type": order.ticket_type,
                                    "platform": order.platform,
                                    "notes": order.notes,
                                    "budget": order.budget,
                                }

                                # 解析观影人数量
                                viewers_text = order.notes or order.customer_name
                                viewer_lines = [line.strip() for line in viewers_text.split('\n') if line.strip()]
                                viewer_count = len(viewer_lines)

                                # 第一行：城市+演出 日期 价位+连坐/单张+数量
                                event_line = order.event_name or "未知演出"
                                if order.event_date:
                                    event_line += f" {order.event_date}"
                                if order.ticket_type:
                                    event_line += f" {order.ticket_type}"
                                # 判断连坐还是单张
                                if order.seats and "连" in (order.seats or ""):
                                    event_line += f" 连坐{viewer_count}张"
                                else:
                                    event_line += " 单张" if viewer_count == 1 else f" {viewer_count}张"

                                # 第二行：平台
                                platform_line = order.platform or "全平台"

                                # 联系电话（脱敏）
                                phone = mask_phone(order.budget) if order.budget else "待补充"

                                # 组装单个订单回复
                                order_reply = [
                                    f"订单 {idx}（订单号：{order_id}）",
                                    "",
                                    event_line,
                                    "",
                                    "身份信息：",
                                ]
                                # 观影人（脱敏身份证号）
                                for line in viewer_lines:
                                    order_reply.append(mask_pii_in_text(line))

                                order_reply.append("")
                                order_reply.append(f"联系电话：{phone}")
                                order_reply.append(f"平台：{platform_line}")

                                all_replies.append("\n".join(order_reply))

                            reply = f"已整理 {len(orders)} 条订单：\n\n" + "\n\n---\n\n".join(all_replies)

                            # 自动检查新增订单是否今天/明天开抢
                            damai_data = get_damai_data_cached()
                            if damai_data:
                                today_matched = find_matching_orders(damai_data, datetime.now())
                                tomorrow_matched = find_matching_orders(damai_data, datetime.now() + timedelta(days=1))

                                # 检查新增的订单是否在匹配列表中
                                new_order_ids = {o.id for o in orders}
                                today_alerts = [m for m in today_matched if m["order"].id in new_order_ids]
                                tomorrow_alerts = [m for m in tomorrow_matched if m["order"].id in new_order_ids]

                                if today_alerts:
                                    reply += "\n\n🔴 注意！以下订单今天开抢：\n"
                                    for m in today_alerts:
                                        reply += f"- {m['order'].event_name} {m['sale'].get('sale_time', '')}\n"
                                    reply += "请立即准备抢票！"

                                if tomorrow_alerts:
                                    reply += "\n\n🟡 提醒：以下订单明天开抢：\n"
                                    for m in tomorrow_alerts:
                                        reply += f"- {m['order'].event_name} {m['sale'].get('sale_time', '')}\n"

                    elif intent == IntentType.EVENT_QUERY:
                        # 提取关键词（备用方案）
                        import re
                        keywords = re.sub(r'什么时候|开票|余票|票价|查询|搜索|票|演出|演唱会|音乐节|有|什么|呀|呢', '', user_input).strip()
                        if not keywords:
                            keywords = user_input

                        # 方案1: 尝试让 LLM 调用工具
                        event_system_prompt = prompts.SYSTEM_PROMPT + "\n\n用户正在查询演出信息，请调用 search_event 工具。"
                        messages = [
                            {"role": "system", "content": event_system_prompt},
                            {"role": "user", "content": user_input},
                        ]
                        response = llm.chat(messages, tools=get_all_tool_schemas())

                        # 如果 LLM 调用了工具
                        if response.get("tool_calls"):
                            tool_results = []
                            tool_calls_for_msg = []
                            for tc in response["tool_calls"]:
                                result = execute_tool(tc["function"], json.loads(tc["arguments"]))
                                tool_results.append(result)
                                tool_calls_for_msg.append({
                                    "id": tc["id"],
                                    "type": "function",
                                    "function": {"name": tc["function"], "arguments": tc["arguments"]}
                                })

                            messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls_for_msg})
                            for i, tc in enumerate(response["tool_calls"]):
                                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_results[i]})

                            final = llm.chat(messages)
                            reply = final.get("content", "查询完成")
                        else:
                            # 方案2: LLM 未调用工具，直接执行查询
                            tool_result = execute_tool("search_event", {"keyword": keywords, "city": None})

                            # 解析数据来源
                            try:
                                result_data = json.loads(tool_result)
                                if isinstance(result_data, list) and result_data:
                                    data_source = result_data[0].get("data_source", "unknown")
                                else:
                                    data_source = "unknown"
                            except Exception as e:
                                logger.warning(f"解析工具结果失败: {e}")
                                data_source = "unknown"

                            # 根据数据来源构建提示
                            source_hint = ""
                            if data_source == "damai":
                                source_hint = "\n\n✅ 数据来源：大麦播报站（官方数据），可直接信任。"
                            elif data_source == "web":
                                source_hint = "\n\n⚠️ 数据来源：网络搜索（仅供参考）。请在回复开头明确标注「以下信息来自网络搜索，仅供参考，请以官方售票平台为准」。"

                            final_messages = [
                                {"role": "system", "content": prompts.SYSTEM_PROMPT + f"\n\n查询结果：\n{tool_result}{source_hint}"},
                                {"role": "user", "content": user_input},
                            ]
                            final = llm.chat(final_messages)
                            reply = final.get("content", "查询完成")

                    elif intent == IntentType.KNOWLEDGE_QA:
                        context = retrieve_from_knowledge(user_input)
                        messages = [
                            {"role": "system", "content": prompts.SYSTEM_PROMPT + f"\n\n参考知识库：\n{context}"},
                            {"role": "user", "content": user_input},
                        ]
                        response = llm.chat(messages)
                        reply = response["content"]

                    elif intent == IntentType.ORDER_MANAGE:
                        # 检查是否是删除订单请求
                        import re as re_mod
                        delete_keywords = ["删除", "删掉", "删了", "移除", "去掉", "撤掉"]
                        edit_keywords = ["修改", "更改", "更新", "改成", "改为", "加上", "增加", "添加", "补充",
                                        "加一个", "加个", "前面加", "后面加", "改成", "改为", "换成"]
                        query_keywords = ["有", "有没有", "查", "查询", "找", "单子", "订单状态"]
                        is_delete = any(kw in user_input for kw in delete_keywords)
                        is_edit = any(kw in user_input for kw in edit_keywords)
                        is_query = any(kw in user_input for kw in query_keywords) and not is_delete and not is_edit

                        # 如果包含"把...的订单/单子"模式，也视为删除/编辑
                        if "把" in user_input and ("订单" in user_input or "单子" in user_input):
                            if not is_delete and not is_edit:
                                # 默认当作删除处理
                                is_delete = True

                        if is_delete:
                            # 提取订单号或关键词
                            # 中文数字转阿拉伯数字
                            cn_to_num = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
                                        "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
                                        "十一": "11", "十二": "12", "十三": "13", "十四": "14", "十五": "15",
                                        "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
                                        "6": "6", "7": "7", "8": "8", "9": "9"}

                            # 匹配"第N条"或"第N个"或"第N单"（支持中文数字）
                            order_num_match = re_mod.search(r'第([一二三四五六七八九十\d]+)[条个份单]', user_input)
                            if order_num_match:
                                cn_num = order_num_match.group(1)
                                if cn_num in cn_to_num:
                                    order_num_value = int(cn_to_num[cn_num])
                                else:
                                    order_num_value = None
                            else:
                                order_num_value = None

                            # 匹配"订单N"或"删除N"作为序号（不是订单号）
                            order_index_match = re_mod.search(r'(?:订单|删除)\s*(\d+)', user_input)

                            all_orders = st.session_state.order_manager.get_all_orders()

                            # 检查是否是"删除所有"
                            if any(kw in user_input for kw in ["所有", "全部", "清空"]):
                                if all_orders:
                                    count = len(all_orders)
                                    for order in all_orders:
                                        st.session_state.order_manager.delete_order(order.id)
                                    reply = f"已删除全部 {count} 条订单"
                                else:
                                    reply = "当前没有订单"
                            elif order_num_value:
                                # 按序号删除（从1开始）
                                idx = order_num_value - 1
                                if 0 <= idx < len(all_orders):
                                    target_order = all_orders[idx]
                                    deleted = st.session_state.order_manager.delete_order(target_order.id)
                                    if deleted:
                                        reply = f"已删除订单：{target_order.event_name or '未知演出'}（订单号：{target_order.id}）"
                                    else:
                                        reply = "删除失败，请重试"
                                else:
                                    reply = f"订单序号超出范围，当前共 {len(all_orders)} 条订单"
                            elif order_index_match:
                                # 按序号删除（"订单1"、"删除2"等）
                                idx = int(order_index_match.group(1)) - 1
                                if 0 <= idx < len(all_orders):
                                    target_order = all_orders[idx]
                                    deleted = st.session_state.order_manager.delete_order(target_order.id)
                                    if deleted:
                                        reply = f"已删除订单：{target_order.event_name or '未知演出'}（订单号：{target_order.id}）"
                                    else:
                                        reply = "删除失败，请重试"
                                else:
                                    reply = f"订单序号超出范围，当前共 {len(all_orders)} 条订单"
                            else:
                                # 使用 LLM 提取搜索关键词
                                extract_prompt = f"""从用户输入中提取用于搜索订单的关键词。

用户输入：{user_input}

请提取以下信息（有的才提取，没有的忽略）：
1. 人名（观影人姓名）
2. 身份证号（15位或18位数字，可能带X）
3. 演出名称/艺人名
4. 城市名

注意：
- "订单5"、"第5条"中的数字是序号，不是关键词，不要提取
- 只提取能唯一标识订单的信息（人名、身份证号、演出名）

输出 JSON 格式：
{{"keywords": ["关键词1", "关键词2"]}}

示例：
输入：把陈艺凡的订单删了
输出：{{"keywords": ["陈艺凡"]}}

输入：删除有513822199705062187这个身份证的订单
输出：{{"keywords": ["513822199705062187"]}}

输入：把薛之谦的单子撤掉
输出：{{"keywords": ["薛之谦"]}}

输入：把订单5删了
输出：{{"keywords": []}}

输入：把观影人是罗兰朵的订单删了
输出：{{"keywords": ["罗兰朵"]}}

只输出 JSON，不要解释。"""

                                try:
                                    from ticketpilot.core import llm
                                    extract_response = llm.chat(
                                        [{"role": "user", "content": extract_prompt}],
                                        temperature=0, max_tokens=100
                                    )
                                    from ticketpilot.core.utils import extract_json
                                    extract_content = extract_response.get("content", "").strip()
                                    extract_result = extract_json(extract_content)
                                    if isinstance(extract_result, dict):
                                        search_terms = extract_result.get("keywords", [])
                                    else:
                                        search_terms = []
                                except Exception as e:
                                    logger.warning(f"LLM关键词提取失败: {e}")
                                    search_terms = []

                                if search_terms:
                                    # 模糊匹配所有符合条件的订单
                                    matched_orders = []
                                    for i, order in enumerate(all_orders, 1):
                                        event = (order.event_name or "") + " " + (order.notes or "")
                                        # 检查是否有任意关键词匹配
                                        for term in search_terms:
                                            if term in event:
                                                matched_orders.append((i, order))
                                                break

                                    if len(matched_orders) == 1:
                                        # 只有一条匹配，直接删除
                                        _, matched = matched_orders[0]
                                        deleted = st.session_state.order_manager.delete_order(matched.id)
                                        if deleted:
                                            reply = f"已删除订单：{matched.event_name or '未知演出'}（订单号：{matched.id}）"
                                        else:
                                            reply = "删除失败，请重试"
                                    elif len(matched_orders) > 1:
                                        # 多条匹配，让用户选择
                                        order_list = []
                                        for i, order in matched_orders:
                                            order_list.append(f"{i}. {order.event_name or '未知演出'}（{order.event_date or '日期待定'}）")
                                        reply = f"找到 {len(matched_orders)} 条相关订单：\n" + "\n".join(order_list) + "\n\n请告诉我要删除哪一条，例如：删除第1条"
                                    else:
                                        reply = f"未找到包含「{'、'.join(search_terms)}」的订单"
                                else:
                                    # 没有提取到关键词，列出所有订单让用户选择
                                    if all_orders:
                                        order_list = []
                                        for i, order in enumerate(all_orders, 1):
                                            order_list.append(f"{i}. {order.event_name or '未知演出'}（订单号：{order.id}）")
                                        reply = "请指定要删除的订单：\n" + "\n".join(order_list)
                                    else:
                                        reply = "当前没有订单"
                        elif is_query:
                            # 查询订单
                            all_orders = st.session_state.order_manager.get_all_orders()

                            # 使用 LLM 提取查询关键词
                            extract_prompt = f"""从用户输入中提取用于查询订单的关键词。

用户输入：{user_input}

请提取以下信息（有的才提取，没有的忽略）：
1. 人名（观影人姓名）
2. 艺人名/演出名称
3. 城市名
4. 日期

输出 JSON 格式：
{{"keywords": ["关键词1", "关键词2"], "query_type": "specific|all"}}

示例：
输入：我有孙燕姿的单子吗
输出：{{"keywords": ["孙燕姿"], "query_type": "specific"}}

输入：明天有什么要开抢的
输出：{{"keywords": [], "query_type": "tomorrow"}}

输入：查看所有订单
输出：{{"keywords": [], "query_type": "all"}}

只输出 JSON，不要解释。"""

                            try:
                                from ticketpilot.core import llm
                                extract_response = llm.chat(
                                    [{"role": "user", "content": extract_prompt}],
                                    temperature=0, max_tokens=100
                                )
                                from ticketpilot.core.utils import extract_json
                                extract_content = extract_response.get("content", "").strip()
                                extract_result = extract_json(extract_content)
                                if isinstance(extract_result, dict):
                                    search_terms = extract_result.get("keywords", [])
                                    query_type = extract_result.get("query_type", "specific")
                                else:
                                    search_terms = []
                                    query_type = "all"
                            except Exception as e:
                                logger.warning(f"LLM查询类型解析失败: {e}")
                                search_terms = []
                                query_type = "all"

                            if query_type == "tomorrow" or "明天" in user_input:
                                # 查询明天开票的订单 - 使用缓存的大麦数据
                                from datetime import datetime, timedelta

                                damai_data = get_damai_data_cached()
                                matched = find_matching_orders(damai_data, datetime.now() + timedelta(days=1))

                                if matched:
                                    reply = f"明天需要抢票的订单（{len(matched)}条）：\n\n"
                                    for i, m in enumerate(matched, 1):
                                        order = m["order"]
                                        sale = m["sale"]
                                        reply += f"{i}. {order.event_name or '未知演出'}\n"
                                        reply += f"   演出时间：{order.event_date or '待定'}\n"
                                        reply += f"   票价：{order.ticket_type or '待定'}\n"
                                        reply += f"   平台：{order.platform or '全平台'}\n"
                                        reply += f"   观影人：{order.notes or '待补充'}\n"
                                        reply += f"   开抢时间：{sale.get('sale_time', '待定')}\n\n"
                                    reply += "建议提前登录各平台，准备好支付方式。"
                                else:
                                    reply = "明天没有需要抢票的订单。"
                            elif "今天" in user_input or "今日" in user_input:
                                # 查询今天开票的订单
                                from datetime import datetime

                                damai_data = get_damai_data_cached()
                                matched = find_matching_orders(damai_data, datetime.now())

                                if matched:
                                    reply = f"今天需要抢票的订单（{len(matched)}条）：\n\n"
                                    for i, m in enumerate(matched, 1):
                                        order = m["order"]
                                        sale = m["sale"]
                                        reply += f"{i}. {order.event_name or '未知演出'}\n"
                                        reply += f"   演出时间：{order.event_date or '待定'}\n"
                                        reply += f"   票价：{order.ticket_type or '待定'}\n"
                                        reply += f"   平台：{order.platform or '全平台'}\n"
                                        reply += f"   观影人：{order.notes or '待补充'}\n"
                                        reply += f"   开抢时间：{sale.get('sale_time', '待定')}\n\n"
                                    reply += "建议提前登录各平台，准备好支付方式。"
                                else:
                                    reply = "今天没有需要抢票的订单。"
                            elif search_terms:
                                # 按关键词查询
                                matched_orders = []
                                for order in all_orders:
                                    event = (order.event_name or "") + " " + (order.notes or "")
                                    for term in search_terms:
                                        if term in event:
                                            matched_orders.append(order)
                                            break

                                if matched_orders:
                                    reply = f"找到 {len(matched_orders)} 条相关订单：\n\n"
                                    for i, order in enumerate(matched_orders, 1):
                                        reply += f"{i}. {order.event_name or '未知演出'}\n"
                                        reply += f"   日期：{order.event_date or '待定'}\n"
                                        reply += f"   票价：{order.ticket_type or '待定'}\n"
                                        reply += f"   平台：{order.platform or '全平台'}\n"
                                        reply += f"   观影人：{order.notes or '待补充'}\n"
                                        reply += f"   状态：{order.status.value}\n\n"
                                else:
                                    reply = f"没有找到包含「{'、'.join(search_terms)}」的订单。"
                            else:
                                # 显示所有订单
                                if all_orders:
                                    reply = f"当前共有 {len(all_orders)} 条订单：\n\n"
                                    for i, order in enumerate(all_orders, 1):
                                        reply += f"{i}. {order.event_name or '未知演出'}"
                                        if order.event_date:
                                            reply += f" {order.event_date}"
                                        reply += f"（{order.status.value}）\n"
                                else:
                                    reply = "当前没有订单。"
                        elif is_edit:
                            # 修改订单内容 - 使用 LLM 智能理解
                            reply = ""  # 初始化
                            all_orders = st.session_state.order_manager.get_all_orders()

                            # 构建订单列表供 LLM 参考
                            order_list_text = ""
                            for i, order in enumerate(all_orders, 1):
                                order_list_text += f"{i}. {order.event_name or '未知演出'}（{order.event_date or '日期待定'}）- {order.ticket_type or ''}\n"

                            # 使用 LLM 理解用户意图
                            edit_prompt = f"""用户想要修改订单。请分析用户输入，提取以下信息：

用户输入：{user_input}

当前订单列表：
{order_list_text}

请提取：
1. target_index: 要修改的订单序号（数字），如果用户说了"订单N"或"第N条"则提取N，如果说"黑马"或艺人名则根据列表匹配对应序号
2. 修改内容：城市(event_name前缀)、票价(ticket_type)、日期(event_date)、平台(platform)、联系电话(budget)、观影人(notes)

输出JSON格式：
{{"target_index": 序号, "updates": {{"field": "value"}}}}

示例：
输入：订单12的黑马前面加一个上海站
输出：{{"target_index": 12, "updates": {{"event_name_prefix": "上海"}}}}

输入：把第3条的票价改成1680
输出：{{"target_index": 3, "updates": {{"ticket_type": "1680"}}}}

输入：把李荣浩的电话改成13800138000
输出：{{"target_index": 12, "updates": {{"budget": "13800138000"}}}}

只输出JSON，不要解释。"""

                            try:
                                from ticketpilot.core import llm
                                edit_response = llm.chat(
                                    [{"role": "user", "content": edit_prompt}],
                                    temperature=0, max_tokens=200
                                )
                                edit_content = edit_response.get("content", "").strip()
                                if "{" in edit_content:
                                    json_str = edit_content[edit_content.index("{"):edit_content.rindex("}") + 1]
                                    edit_result = json.loads(json_str)
                                    target_idx = edit_result.get("target_index")
                                    updates = edit_result.get("updates", {})

                                    if target_idx and 1 <= target_idx <= len(all_orders):
                                        target_order = all_orders[target_idx - 1]

                                        # 处理更新
                                        update_fields = {}

                                        # 城市前缀
                                        if "event_name_prefix" in updates:
                                            city = updates["event_name_prefix"]
                                            current_name = target_order.event_name or ""
                                            if city not in current_name:
                                                update_fields["event_name"] = city + current_name

                                        # 其他字段
                                        for field in ["ticket_type", "event_date", "platform", "budget", "notes"]:
                                            if field in updates:
                                                update_fields[field] = updates[field]

                                        if update_fields:
                                            success = st.session_state.order_manager.update_order(target_order.id, **update_fields)
                                            if success:
                                                changes = "、".join([f"{k}={v}" for k, v in update_fields.items()])
                                                reply = f"已更新订单「{target_order.event_name}」：{changes}"
                                            else:
                                                reply = "更新失败，请重试"
                                        else:
                                            reply = f"订单「{target_order.event_name}」无需修改"
                                    else:
                                        reply = f"订单序号超出范围，当前共 {len(all_orders)} 条订单"
                                else:
                                    reply = "抱歉，我没理解您的意思，请重新说明"
                            except Exception as e:
                                print(f"LLM编辑解析失败: {e}")
                                reply = "抱歉，处理出错，请重新说明"
                        else:
                            summary = st.session_state.order_manager.get_status_summary()
                            reply = (
                                f"当前订单状态：\n"
                                f"| 状态 | 数量 |\n|---|---|\n"
                                f"| 待抢票 | {summary['pending']} |\n"
                                f"| 已中票 | {summary['success']} |\n"
                                f"| 未中票 | {summary['failed']} |\n"
                                f"| 已撤单 | {summary['cancelled']} |\n"
                                f"| 已退款 | {summary['refunded']} |\n"
                                f"| 等待二开 | {summary['waiting_second']} |\n"
                                f"| **总计** | **{summary['total']}** |"
                            )

                    else:
                        # GENERAL 意图：允许 LLM 调用工具（如 check_time）
                        messages = [
                            {"role": "system", "content": prompts.SYSTEM_PROMPT},
                            {"role": "user", "content": user_input},
                        ]
                        response = llm.chat(messages, tools=get_all_tool_schemas())

                        # 如果 LLM 调用了工具
                        if response.get("tool_calls"):
                            tool_results = []
                            tool_calls_for_msg = []
                            for tc in response["tool_calls"]:
                                result = execute_tool(tc["function"], json.loads(tc["arguments"]))
                                tool_results.append(result)
                                tool_calls_for_msg.append({
                                    "id": tc["id"],
                                    "type": "function",
                                    "function": {"name": tc["function"], "arguments": tc["arguments"]}
                                })

                            messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls_for_msg})
                            for i, tc in enumerate(response["tool_calls"]):
                                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_results[i]})

                            final = llm.chat(messages)
                            reply = final.get("content", "处理完成")
                        else:
                            reply = response.get("content", "抱歉，我无法处理这个请求")

                    st.markdown(reply)
                    st.caption(f"意图：{intent.value}")

                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": reply,
                        "intent": intent.value,
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
