"""
聊天编排服务（application 层）

将 frontend/app.py 与 api/routes.py 各自维护的五个意图分支（约 600 行
重复且已漂移的实现）收敛为一份共享编排逻辑，两个入口退化为薄适配器。

v0.2：路由输出三域（ORDER 订单域 / AGENT 工具域 / QA 知识域），路由选范式不选话题；
五类 IntentType 降级为观测/展示标签（前端与遥测依赖），不再参与路由决策。

分支行为以 Streamlit 聊天页为基准（功能最全），并合并 API 版的优点：
- 域分类统一走 router.classify_domain（硬特征快路径 → LLM → 关键词容错）
- 工具循环统一为 _run_tool_loop（单轮，坏 arguments 不再抛异常）
- ORDER_PARSE 回复统一脱敏（format_order_masked），保存后回填 order.id
- EVENT_QUERY 降级提示取 API 版全量 source_hint，关键词剥离取 app 版全量正则
- 时间统一走 get_accurate_time()（NTP，5 分钟缓存，失败降级系统时间）
- 城市列表统一用 data.constants.CITIES（40 城）
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta

from ticketpilot.agent.order_manager import OrderManager
from ticketpilot.core import llm, prompts, router
from ticketpilot.core.privacy import mask_pii_in_text, redact_pii, restore_pii
from ticketpilot.core.router import Domain, IntentType
from ticketpilot.core.utils import extract_json
from ticketpilot.data.constants import CITIES
from ticketpilot.rag.retriever import retrieve_from_knowledge
from ticketpilot.tools.base import execute_tool, get_all_tool_schemas
from ticketpilot.tools.order_parser import format_order_masked
from ticketpilot.tools.time_utils import get_accurate_time

logger = logging.getLogger(__name__)

# 中文数字 → 阿拉伯数字（"第N条"序号解析）
CN_TO_NUM = {
    "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
    "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
    "十一": "11", "十二": "12", "十三": "13", "十四": "14", "十五": "15",
}


# Agent 域工具集：只读查询类工具。写操作（update_order_status 等）只走订单域
# 确定性路径，被封存的播报类工具不暴露给 LLM（Read/Write 工具分离）
AGENT_TOOL_NAMES = {
    "search_event", "get_damai_broadcast",   # 演出查询
    "check_time",                            # 时间校验
    "get_my_orders",                         # 订单只读查询
    "search_knowledge",                      # 知识检索
}
# 用于观测标签：调用了演出类工具 → intent 记 EVENT_QUERY，否则 GENERAL
EVENT_TOOL_NAMES = {"search_event", "get_damai_broadcast"}


@dataclass
class ChatResult:
    """一次对话的处理结果"""
    reply: str
    intent: str                                   # 观测/展示标签（IntentType.value），路由决策见 route
    route: str                                    # order_parse / order_manage / agent / agent_fallback_search / knowledge_qa / knowledge_qa_refused
    orders: list = field(default_factory=list)    # ORDER_PARSE 新建的 Order（id 已回填），供入口做会话记忆


class ChatService:
    """三域编排服务。实例持有 order_manager 与大麦缓存，可注入以便测试。"""

    def __init__(self, order_manager: OrderManager, damai_cache: dict | None = None):
        self.order_manager = order_manager
        # {"data": ..., "last_update": datetime}；注入方可控制/清空缓存
        self.damai_cache = damai_cache if damai_cache is not None else {"data": None, "last_update": None}

    # ===========================================
    # 公共入口
    # ===========================================

    def chat(self, user_input: str, context: str = "", use_llm_router: bool = True) -> ChatResult:
        """
        处理一条用户消息。

        Args:
            user_input: 用户输入
            context: 上下文（如最近创建的订单），仅用于 LLM 域分类
            use_llm_router: True 走 LLM 分类（失败自动回退关键词），False 纯规则路径
        """
        domain = router.classify_domain(user_input, context, use_llm=use_llm_router)
        # 日志也是输出通道：入日志前先脱敏，否则证件号明文躺在日志文件里。
        # 顺序必须是先脱敏后切片：切片可能把 18 位证件号拦腰切断，
        # 残段匹配不上整号正则，半截明文就漏进日志了。
        logger.info(f"[chat] domain={domain.value} input={mask_pii_in_text(user_input)[:50]!r}")

        if domain == Domain.ORDER:
            result = self._handle_order(user_input)
        elif domain == Domain.AGENT:
            result = self._handle_agent(user_input)
        else:
            result = self._handle_knowledge_qa(user_input)

        # 结构化日志：一行机器可读 JSON。评测脚本直接从日志统计
        # 域分布/拒答率（knowledge_qa_refused 占比）/各 route 计数，
        # 不需要为观测单独埋点。字段名即口径，改字段=改指标定义。
        logger.info(json.dumps({
            "event": "chat",
            "domain": domain.value,
            "route": result.route,
            "intent": result.intent,
            "llm_router": use_llm_router,
            "orders_created": len(result.orders),
            "reply_len": len(result.reply),
        }, ensure_ascii=False))
        return result

    def _handle_order(self, user_input: str) -> ChatResult:
        """订单域内部分流：订单内容硬特征 → 解析；否则 → 管理子分类"""
        if router.is_order_content(user_input.lower().strip()):
            return self._handle_order_parse(user_input)
        return self._handle_order_manage(user_input)

    # ===========================================
    # 大麦数据（缓存 + 开抢匹配）
    # ===========================================

    def get_damai_data_cached(self):
        """获取大麦播报数据，缓存 1 小时；获取失败返回旧缓存"""
        now = get_accurate_time()
        cache = self.damai_cache

        if cache.get("data") and cache.get("last_update"):
            if now - cache["last_update"] < timedelta(hours=1):
                return cache["data"]

        try:
            result = execute_tool("get_damai_broadcast", {})
            data = json.loads(result)
            self.damai_cache = {"data": data, "last_update": now}
            return data
        except Exception as e:
            logger.warning(f"获取大麦数据失败: {e}")
            return cache.get("data")

    def find_matching_orders(self, damai_data, target_date=None) -> list[dict]:
        """
        查找目标日期开抢且与客户订单匹配的播报项目。

        匹配规则：艺人名前缀（去掉城市前缀后取前2/前3字）+ 城市双重匹配。
        """
        if not damai_data:
            return []

        orders = self.order_manager.get_all_orders()
        upcoming = damai_data.get("即将开抢", [])

        now = get_accurate_time()
        if target_date is None:
            target_date = now

        matched = []
        for item in upcoming:
            sale_time = str(item.get("sale_time", ""))
            item_name = str(item.get("name", ""))
            item_city = str(item.get("city", ""))

            # 判断开抢日期是否是目标日期
            is_target_date = False
            if "今天" in sale_time and target_date.date() == now.date():
                is_target_date = True
            elif "明天" in sale_time and target_date.date() == (now + timedelta(days=1)).date():
                is_target_date = True
            else:
                date_match = re.search(r'(\d+)月(\d+)日', sale_time)
                if date_match:
                    if (int(date_match.group(1)) == target_date.month
                            and int(date_match.group(2)) == target_date.day):
                        is_target_date = True

            if not is_target_date:
                continue

            for order in orders:
                order_name = (order.event_name or "").replace(" ", "")

                # 提取艺人名（剥掉城市前缀，统一用 constants.CITIES）
                order_artist = order_name
                for city in CITIES:
                    order_artist = order_artist.replace(city, "")

                artist_match = any(a in item_name for a in [order_artist[:2], order_artist[:3]] if len(a) > 1)
                city_match = bool(item_city) and item_city in order_name

                if artist_match and city_match:
                    matched.append({"order": order, "sale": item})

        return matched

    def get_sale_matches(self):
        """返回 (今天开抢匹配, 明天开抢匹配)，供侧边栏/提醒复用"""
        damai_data = self.get_damai_data_cached()
        if not damai_data:
            return [], []
        now = get_accurate_time()
        return (
            self.find_matching_orders(damai_data, now),
            self.find_matching_orders(damai_data, now + timedelta(days=1)),
        )

    def build_sale_alerts(self, new_orders=None) -> str:
        """
        今明两天开抢提醒文案（追加在订单确认回复后）。

        Args:
            new_orders: 给定时只提醒这些订单（新增订单场景）；None 提醒全部
        """
        today_matched, tomorrow_matched = self.get_sale_matches()
        if new_orders is not None:
            new_ids = {o.id for o in new_orders}
            today_matched = [m for m in today_matched if m["order"].id in new_ids]
            tomorrow_matched = [m for m in tomorrow_matched if m["order"].id in new_ids]

        alert = ""
        if today_matched:
            alert += "\n\n🔴 注意！以下订单今天开抢：\n"
            for m in today_matched:
                alert += f"- {m['order'].event_name} {m['sale'].get('sale_time', '')}\n"
            alert += "请立即准备抢票！"
        if tomorrow_matched:
            alert += "\n\n🟡 提醒：以下订单明天开抢：\n"
            for m in tomorrow_matched:
                alert += f"- {m['order'].event_name} {m['sale'].get('sale_time', '')}\n"
        return alert

    # ===========================================
    # 工具循环（收敛三份复制，单轮语义）
    # ===========================================

    def _run_tool_loop(self, messages: list, response: dict, done_fallback: str = "处理完成") -> str:
        """
        执行 LLM 返回的 tool_calls（单轮），把工具结果回填 messages 后
        让 LLM 生成最终回复。messages 会被就地追加。
        """
        tool_results = []
        tool_calls_for_msg = []
        for tc in response.get("tool_calls", []):
            # 坏 arguments 不让整个请求失败：降级为空参数
            try:
                args = json.loads(tc.get("arguments") or "{}")
            except json.JSONDecodeError as e:
                logger.warning(f"工具参数非法 JSON，按空参数执行: {e}")
                args = {}
            # execute_tool 内部已捕获异常并返回错误 JSON 字符串
            result = execute_tool(tc["function"], args)
            tool_results.append(result)
            tool_calls_for_msg.append({
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["function"], "arguments": tc.get("arguments", "{}")},
            })

        messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls_for_msg})
        for i, tc in enumerate(response.get("tool_calls", [])):
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_results[i]})

        final = llm.chat(messages)
        return final.get("content") or done_fallback

    # ===========================================
    # ORDER_PARSE：订单整理
    # ===========================================

    def _handle_order_parse(self, user_input: str) -> ChatResult:
        orders = self.order_manager.parse_multiple_orders(user_input)

        if not orders:
            return ChatResult(
                reply="未能从输入中解析出订单信息。请发送包含演出、观影人、联系方式的客户信息。",
                intent=IntentType.ORDER_PARSE.value, route="order_parse",
            )

        all_replies = []
        for idx, order in enumerate(orders, 1):
            order_id = self.order_manager.save_order(order)
            # 回填 id：开抢提醒按 {o.id} 匹配、入口会话记忆都依赖它
            order.id = order_id
            all_replies.append(format_order_masked(order, order_id=order_id, idx=idx))

        reply = f"已整理 {len(orders)} 条订单（草稿）：\n\n" + "\n\n---\n\n".join(all_replies)

        # 草稿边界：LLM 解析结果不直接生效，明确告知用户如何确认
        reply += "\n\n📝 以上为草稿，请核对无误后回复「确认全部」或「确认第N条」生效。"

        # 自动检查新增订单是否今天/明天开抢
        reply += self.build_sale_alerts(new_orders=orders)

        return ChatResult(
            reply=reply,
            intent=IntentType.ORDER_PARSE.value, route="order_parse",
            orders=orders,
        )

    # ===========================================
    # EVENT_QUERY：演出查询
    # ===========================================

    def _handle_agent(self, user_input: str) -> ChatResult:
        """
        Agent 域：function calling 工具循环（演出查询 + 通用对话统一范式）。

        三条路径：
        1. LLM 首轮调用工具 → _run_tool_loop；观测标签按是否调用演出类工具区分
        2. LLM 未调工具但输入有演出查询特征 → 直接执行 search_event 后总结（降级）
        3. 纯闲聊 → 直接返回首轮响应（1 次调用，成本与独立闲聊分支持平）
        """
        agent_system_prompt = prompts.SYSTEM_PROMPT + """

## 重要指令
你可以调用只读查询类工具（演出查询、时间校验、订单查询、知识检索）。
- 用户查询演出信息时，必须调用 search_event 工具，不要凭记忆回答。
- 闲聊或一般问题直接回答，不要调用工具。
"""
        messages = [
            {"role": "system", "content": agent_system_prompt},
            {"role": "user", "content": user_input},
        ]
        tools = [s for s in get_all_tool_schemas()
                 if s["function"]["name"] in AGENT_TOOL_NAMES]
        response = llm.chat(messages, tools=tools)

        if response.get("tool_calls"):
            called = {tc.get("function") for tc in response.get("tool_calls", [])}
            # 兜底文案用中性默认值（Agent 域不止查询，还有闲聊/时间等工具路径）
            reply = self._run_tool_loop(messages, response)
            intent = (IntentType.EVENT_QUERY.value if called & EVENT_TOOL_NAMES
                      else IntentType.GENERAL.value)
            return ChatResult(reply=reply, intent=intent, route="agent")

        # 降级路径：LLM 未调工具但输入像演出查询 → 直接查后总结
        if router.looks_like_event_query(user_input.lower().strip()):
            keywords = re.sub(
                r'什么时候|开票|余票|票价|查询|搜索|票|演出|演唱会|音乐节|有|什么|呀|呢',
                '', user_input
            ).strip() or user_input
            logger.info(f"[AGENT] LLM 未调用工具，直接执行查询，关键词: {keywords}")
            try:
                tool_result = execute_tool("search_event", {"keyword": keywords, "city": None})
                source_hint = self._build_source_hint(tool_result)
                final_messages = [
                    {"role": "system", "content": prompts.SYSTEM_PROMPT + f"\n\n查询结果：\n{tool_result}{source_hint}"},
                    {"role": "user", "content": user_input},
                ]
                final = llm.chat(final_messages)
                reply = final.get("content") or "查询完成，但无法生成回复"
            except Exception as e:
                logger.error(f"[AGENT] 工具调用失败: {e}")
                reply = f"抱歉，查询演出信息时遇到问题：{e}"
            return ChatResult(reply=reply, intent=IntentType.EVENT_QUERY.value,
                              route="agent_fallback_search")

        # 闲聊快路径：无 tool_call 直接返回，成本与独立闲聊分支持平
        reply = response.get("content") or "抱歉，我无法处理这个请求"
        return ChatResult(reply=reply, intent=IntentType.GENERAL.value, route="agent")

    @staticmethod
    def _build_source_hint(tool_result: str) -> str:
        """根据工具结果的数据来源生成给 LLM 的可信度提示（API 版全量文案）"""
        try:
            result_data = json.loads(tool_result)
            if isinstance(result_data, list) and result_data:
                data_source = result_data[0].get("data_source", "unknown")
            else:
                data_source = "unknown"
        except Exception as e:
            logger.warning(f"解析工具结果失败: {e}")
            data_source = "unknown"

        if data_source == "damai":
            return "\n\n✅ 数据来源：大麦播报站（官方数据），可直接信任。"
        if data_source == "web":
            return ("\n\n⚠️ 数据来源：网络搜索（仅供参考）。请在回复开头明确标注"
                    "「以下信息来自网络搜索，仅供参考，请以官方售票平台为准」。"
                    "购票平台只列出大麦、猫眼、票星球等主流平台。")
        return ""

    # ===========================================
    # KNOWLEDGE_QA：知识库问答
    # ===========================================

    KB_REFUSAL = ("知识库暂无相关信息，目前功能还在完善中。"
                  "建议咨询业内人士或查阅官方资料。")

    def _handle_knowledge_qa(self, user_input: str) -> ChatResult:
        """
        QA 域：RAG 硬门控。

        检索未过阈值 → 直接返回罐头拒答，0 次 LLM 调用：把「不许编造」
        从 prompt 里的恳求升级为控制流层面的物理隔离——模型根本没机会编。
        过阈值 → 注入上下文，并要求回答末尾列出引用来源（可核查性）。
        """
        context = retrieve_from_knowledge(user_input)
        if not context:
            logger.info("[QA] 硬门控拦截：检索未达阈值，直接拒答（0 LLM）")
            return ChatResult(
                reply=self.KB_REFUSAL,
                intent=IntentType.KNOWLEDGE_QA.value, route="knowledge_qa_refused",
            )

        kb_hint = (
            f"\n\n参考知识库内容：\n{context}"
            "\n\n回答要求：只依据上述知识库内容回答，不得补充知识库之外的信息；"
            "回答末尾另起一行，用「来源：《小节标题》」列出实际引用的小节。"
        )
        messages = [
            {"role": "system", "content": prompts.SYSTEM_PROMPT + kb_hint},
            {"role": "user", "content": user_input},
        ]
        response = llm.chat(messages)
        return ChatResult(
            reply=response.get("content", ""),
            intent=IntentType.KNOWLEDGE_QA.value, route="knowledge_qa",
        )

    # ===========================================
    # ORDER_MANAGE：订单管理（delete/query/edit/summary）
    # ===========================================

    def _handle_order_manage(self, user_input: str) -> ChatResult:
        action = router.classify_order_action(user_input)
        logger.info(f"[ORDER_MANAGE] action={action}")

        if action == "delete":
            reply = self._manage_delete(user_input)
        elif action == "confirm":
            reply = self._manage_confirm(user_input)
        elif action == "query":
            reply = self._manage_query(user_input)
        elif action == "edit":
            reply = self._manage_edit(user_input)
        else:
            reply = self._manage_summary()

        return ChatResult(reply=reply, intent=IntentType.ORDER_MANAGE.value, route="order_manage")

    def _extract_order_keywords(self, user_input: str, mode: str) -> tuple[list, str]:
        """
        用 LLM 从用户输入中提取订单检索关键词。

        Args:
            mode: "delete"（人名/身份证/演出/城市）或 "query"（附带 query_type）

        Returns:
            (search_terms, query_type)；query_type 仅 query 模式有意义
        """
        # PII 输入最小化：LLM 只见占位符文本；返回的关键词若回显占位符，
        # 本地还原后再做匹配（匹配目标是库里的原文 notes）
        redacted_input, pii_map = redact_pii(user_input)
        if mode == "delete":
            extract_prompt = f"""从用户输入中提取用于搜索订单的关键词。

用户输入：{redacted_input}

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
        else:
            extract_prompt = f"""从用户输入中提取用于查询订单的关键词。

用户输入：{redacted_input}

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
            extract_response = llm.chat(
                [{"role": "user", "content": extract_prompt}],
                temperature=0, max_tokens=100
            )
            extract_result = extract_json(extract_response.get("content", "").strip())
            if isinstance(extract_result, dict):
                keywords = [restore_pii(k, pii_map)
                            for k in extract_result.get("keywords", [])]
                return (keywords,
                        extract_result.get("query_type", "specific" if mode == "delete" else "all"))
        except Exception as e:
            logger.warning(f"LLM关键词提取失败: {e}")

        return [], "all"

    @staticmethod
    def _parse_order_index(user_input: str, verb: str = "删除") -> int | None:
        """
        解析指令中的 1-based 订单序号；删除/确认共用。

        抽公共函数的理由：「第N条」的语义必须在所有管理动作里完全一致，
        用户不该为删除和确认维护两套序号心智模型。
        verb 控制"动词N"简写（"删除3"/"确认3"）里允许的动词。
        """
        # "第N条/个/份/单"（支持中文数字）
        m = re.search(r'第([一二三四五六七八九十\d]+)[条个份单]', user_input)
        if m:
            cn_num = m.group(1)
            if cn_num in CN_TO_NUM:
                return int(CN_TO_NUM[cn_num])
            if cn_num.isdigit():
                return int(cn_num)
            return None
        # "订单N" 或 "动词N"
        m = re.search(rf'(?:订单|{verb})\s*(\d+)', user_input)
        return int(m.group(1)) if m else None

    def _manage_delete(self, user_input: str) -> str:
        """删除订单：支持 所有/全部/清空、第N条、订单N、LLM 关键词模糊匹配"""
        order_num_value = self._parse_order_index(user_input, verb="删除")

        all_orders = self.order_manager.get_all_orders()

        if any(kw in user_input for kw in ["所有", "全部", "清空"]):
            if all_orders:
                count = len(all_orders)
                for order in all_orders:
                    self.order_manager.delete_order(order.id)
                return f"已删除全部 {count} 条订单"
            return "当前没有订单"

        if order_num_value is not None:
            return self._delete_by_index(all_orders, order_num_value - 1)

        # LLM 提取关键词模糊匹配
        search_terms, _ = self._extract_order_keywords(user_input, mode="delete")

        if search_terms:
            matched_orders = []
            for i, order in enumerate(all_orders, 1):
                event = (order.event_name or "") + " " + (order.notes or "")
                if any(term in event for term in search_terms):
                    matched_orders.append((i, order))

            if len(matched_orders) == 1:
                _, matched = matched_orders[0]
                deleted = self.order_manager.delete_order(matched.id)
                if deleted:
                    return f"已删除订单：{matched.event_name or '未知演出'}（订单号：{matched.id}）"
                return "删除失败，请重试"
            elif len(matched_orders) > 1:
                order_list = [
                    f"{i}. {order.event_name or '未知演出'}（{order.event_date or '日期待定'}）"
                    for i, order in matched_orders
                ]
                return (f"找到 {len(matched_orders)} 条相关订单：\n" + "\n".join(order_list)
                        + "\n\n请告诉我要删除哪一条，例如：删除第1条")
            return f"未找到包含「{'、'.join(search_terms)}」的订单"

        # 没提取到关键词：列出所有订单让用户选
        if all_orders:
            order_list = [
                f"{i}. {order.event_name or '未知演出'}（订单号：{order.id}）"
                for i, order in enumerate(all_orders, 1)
            ]
            return "请指定要删除的订单：\n" + "\n".join(order_list)
        return "当前没有订单"

    def _delete_by_index(self, all_orders: list, idx: int) -> str:
        """按 0-based 下标删除（越界防御）"""
        if 0 <= idx < len(all_orders):
            target_order = all_orders[idx]
            deleted = self.order_manager.delete_order(target_order.id)
            if deleted:
                return f"已删除订单：{target_order.event_name or '未知演出'}（订单号：{target_order.id}）"
            return "删除失败，请重试"
        return f"订单序号超出范围，当前共 {len(all_orders)} 条订单"

    def _manage_confirm(self, user_input: str) -> str:
        """
        确认草稿：支持 全部/所有、第N条、订单N/确认N（序号语义与删除一致）。

        序号按全量订单列表定位（与删除同源），列表展示时草稿也用全局序号，
        保证「看到的编号」和「要说的编号」是同一个。
        无序号时列出全部草稿让用户选，而不是猜——确认是不可逆的写操作，
        宁可多一轮对话也不做歧义下的批量生效。
        """
        all_orders = self.order_manager.get_all_orders()
        drafts = [o for o in all_orders if not o.confirmed]

        # 显式序号优先判定：用户点名了具体订单，就该得到关于那条订单的答复
        # （"已是确认状态"），而不是被全局的"没有草稿"早退挡掉。
        order_num_value = self._parse_order_index(user_input, verb="确认")
        if order_num_value is not None:
            idx = order_num_value - 1
            if 0 <= idx < len(all_orders):
                target = all_orders[idx]
                if target.confirmed:
                    return (f"订单 {target.id}（{target.event_name or '未知演出'}）"
                            f"已是确认状态，无需重复确认")
                self.order_manager.confirm_order(target.id)
                return f"已确认订单：{target.event_name or '未知演出'}（订单号：{target.id}）"
            return f"订单序号超出范围，当前共 {len(all_orders)} 条订单"

        if not drafts:
            return "当前没有待确认的草稿订单。"

        if any(kw in user_input for kw in ["所有", "全部"]):
            for o in drafts:
                self.order_manager.confirm_order(o.id)
            return f"已确认全部 {len(drafts)} 条草稿订单"

        draft_list = [
            f"{i}. {o.event_name or '未知演出'}（订单号：{o.id}）"
            for i, o in enumerate(all_orders, 1) if not o.confirmed
        ]
        return ("待确认的草稿订单：\n" + "\n".join(draft_list)
                + "\n\n回复「确认第N条」或「确认全部」生效。")

    def _manage_query(self, user_input: str) -> str:
        """查询订单：今天/明天开抢、关键词模糊查询、全量列表"""
        all_orders = self.order_manager.get_all_orders()
        search_terms, query_type = self._extract_order_keywords(user_input, mode="query")

        if query_type == "tomorrow" or "明天" in user_input:
            damai_data = self.get_damai_data_cached()
            matched = self.find_matching_orders(damai_data, get_accurate_time() + timedelta(days=1))
            return self._format_sale_matches(matched, "明天")

        if "今天" in user_input or "今日" in user_input:
            damai_data = self.get_damai_data_cached()
            matched = self.find_matching_orders(damai_data, get_accurate_time())
            return self._format_sale_matches(matched, "今天")

        if search_terms:
            matched_orders = []
            for order in all_orders:
                event = (order.event_name or "") + " " + (order.notes or "")
                if any(term in event for term in search_terms):
                    matched_orders.append(order)

            if matched_orders:
                reply = f"找到 {len(matched_orders)} 条相关订单：\n\n"
                for i, order in enumerate(matched_orders, 1):
                    reply += f"{i}. {order.event_name or '未知演出'}\n"
                    reply += f"   日期：{order.event_date or '待定'}\n"
                    reply += f"   票价：{order.ticket_type or '待定'}\n"
                    reply += f"   平台：{order.platform or '全平台'}\n"
                    reply += f"   观影人：{mask_pii_in_text(order.notes) if order.notes else '待补充'}\n"
                    reply += f"   状态：{order.status.value}\n\n"
                return reply
            return f"没有找到包含「{'、'.join(search_terms)}」的订单。"

        # 显示所有订单
        if all_orders:
            reply = f"当前共有 {len(all_orders)} 条订单：\n\n"
            for i, order in enumerate(all_orders, 1):
                reply += f"{i}. {order.event_name or '未知演出'}"
                if order.event_date:
                    reply += f" {order.event_date}"
                reply += f"（{order.status.value}{'，待确认' if not order.confirmed else ''}）\n"
            return reply
        return "当前没有订单。"

    @staticmethod
    def _format_sale_matches(matched: list, day_label: str) -> str:
        """今天/明天开抢订单列表文案"""
        if not matched:
            return f"{day_label}没有需要抢票的订单。"
        reply = f"{day_label}需要抢票的订单（{len(matched)}条）：\n\n"
        for i, m in enumerate(matched, 1):
            order = m["order"]
            sale = m["sale"]
            reply += f"{i}. {order.event_name or '未知演出'}\n"
            reply += f"   演出时间：{order.event_date or '待定'}\n"
            reply += f"   票价：{order.ticket_type or '待定'}\n"
            reply += f"   平台：{order.platform or '全平台'}\n"
            reply += f"   观影人：{mask_pii_in_text(order.notes) if order.notes else '待补充'}\n"
            reply += f"   开抢时间：{sale.get('sale_time', '待定')}\n\n"
        reply += "建议提前登录各平台，准备好支付方式。"
        return reply

    def _manage_edit(self, user_input: str) -> str:
        """修改订单：LLM 理解目标序号 + 更新字段"""
        all_orders = self.order_manager.get_all_orders()
        if not all_orders:
            return "当前没有订单，无法修改。"

        # PII 输入最小化：LLM 只见占位符文本；updates 里回显的占位符
        # （如"把电话改成[PHONE_1]"）在本地还原后再落库
        redacted_input, pii_map = redact_pii(user_input)

        # 构建订单列表供 LLM 参考
        order_list_text = ""
        for i, order in enumerate(all_orders, 1):
            order_list_text += f"{i}. {order.event_name or '未知演出'}（{order.event_date or '日期待定'}）- {order.ticket_type or ''}\n"

        edit_prompt = f"""用户想要修改订单。请分析用户输入，提取以下信息：

用户输入：{redacted_input}

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

输入：把张三的电话改成[PHONE_1]
输出：{{"target_index": 12, "updates": {{"budget": "[PHONE_1]"}}}}
（输入中的 [ID_n]/[PHONE_n] 是占位符，输出时必须原样保留，不要改写）

只输出JSON，不要解释。"""

        try:
            edit_response = llm.chat(
                [{"role": "user", "content": edit_prompt}],
                temperature=0, max_tokens=200
            )
            # 统一走 extract_json（替代原手写 index/rindex JSON 切片）
            edit_result = extract_json(edit_response.get("content", "").strip())
            if not isinstance(edit_result, dict):
                return "抱歉，我没理解您的意思，请重新说明"

            target_idx = edit_result.get("target_index")
            updates = edit_result.get("updates", {})

            if target_idx and 1 <= target_idx <= len(all_orders):
                target_order = all_orders[target_idx - 1]
                update_fields = {}

                # 城市前缀：拼在 event_name 最前面
                if "event_name_prefix" in updates:
                    city = updates["event_name_prefix"]
                    current_name = target_order.event_name or ""
                    if city not in current_name:
                        update_fields["event_name"] = city + current_name

                for f in ["ticket_type", "event_date", "platform", "budget", "notes"]:
                    if f in updates:
                        update_fields[f] = restore_pii(updates[f], pii_map)

                if update_fields:
                    success = self.order_manager.update_order(target_order.id, **update_fields)
                    if success:
                        changes = "、".join([f"{k}={v}" for k, v in update_fields.items()])
                        return f"已更新订单「{target_order.event_name}」：{changes}"
                    return "更新失败，请重试"
                return f"订单「{target_order.event_name}」无需修改"
            return f"订单序号超出范围，当前共 {len(all_orders)} 条订单"
        except Exception as e:
            logger.warning(f"LLM编辑解析失败: {e}")
            return "抱歉，处理出错，请重新说明"

    def _manage_summary(self) -> str:
        """订单状态汇总表（无明确管理动作时的兜底）"""
        summary = self.order_manager.get_status_summary()
        # 草稿是独立维度（confirmed 位），不进 OrderStatus 枚举，单列一行
        draft_count = sum(1 for o in self.order_manager.get_all_orders() if not o.confirmed)
        return (
            f"当前订单状态：\n"
            f"| 状态 | 数量 |\n|---|---|\n"
            f"| 待抢票 | {summary['pending']} |\n"
            f"| 已中票 | {summary['success']} |\n"
            f"| 未中票 | {summary['failed']} |\n"
            f"| 已撤单 | {summary['cancelled']} |\n"
            f"| 已退款 | {summary['refunded']} |\n"
            f"| 等待二开 | {summary['waiting_second']} |\n"
            f"| 待确认草稿 | {draft_count} |\n"
            f"| **总计** | **{summary['total']}** |"
        )

    # ===========================================
    # GENERAL：通用对话（带工具循环）
    # ===========================================

