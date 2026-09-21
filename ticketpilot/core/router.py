"""
意图路由模块（v0.2 三域版）

路由选「处理范式」而不是「话题」，输出三个域：
- ORDER: 订单域（解析+管理，确定性流水线）
- AGENT: Agent 域（演出查询+通用对话，function calling 工具循环）
- QA:    知识域（检索增强问答）

分类采用置信度级联（便宜且确定的在前）：
1. 硬特征快路径：18 位身份证正则 / 「指令词+对象词」组合句式 → 直接 ORDER，零 LLM 成本
2. LLM 三域分类（失败自动回退）
3. 关键词五分类映射到三域（classify_intent_simple，纯规则容错层）

注：五类 IntentType 保留为观测/展示标签（前端与遥测依赖），不再参与路由决策。
"""

import re
import logging
from enum import Enum

logger = logging.getLogger(__name__)

# 从常量文件导入城市列表和艺人列表
from ticketpilot.data.constants import CITIES, HOT_ARTISTS


class IntentType(str, Enum):
    """意图类型枚举（v0.2 起仅作为观测/展示标签，路由决策用 Domain）"""
    ORDER_PARSE = "ORDER_PARSE"
    EVENT_QUERY = "EVENT_QUERY"
    KNOWLEDGE_QA = "KNOWLEDGE_QA"
    ORDER_MANAGE = "ORDER_MANAGE"
    GENERAL = "GENERAL"


class Domain(str, Enum):
    """路由三域：按处理范式划分"""
    ORDER = "ORDER"   # 确定性订单流水线（解析 + 管理）
    AGENT = "AGENT"   # 工具 agent（function calling 工具循环）
    QA = "QA"         # 检索增强知识问答


# ===========================================
# 关键词配置（按优先级排序）
# ===========================================

# 演出查询关键词（高优先级）
EVENT_KEYWORDS_HIGH = [
    "开票", "什么时候开", "在哪买", "哪个平台", "票价", "几号演出",
    "演唱会", "音乐节", "话剧", "抢票", "余票", "售票", "购票",
    "什么时候", "几号", "几点", "哪天", "演出信息"
]

# 艺人名单（用于组合判断）
# 使用常量文件中的城市和艺人列表
ARTIST_LIST = HOT_ARTISTS
CITY_LIST = CITIES

# 知识问答关键词
KNOWLEDGE_KEYWORDS = [
    "什么是", "什么意思", "规则", "术语", "怎么退票", "怎么买票",
    "一开", "二开", "公售", "预售", "内场", "外场", "看台",
    "连坐", "强实名", "弱实名", "延顺", "候补", "配票"
]

# 常见错别字映射
TYPO_CORRECTIONS = {
    "一开始": "一开",  # 常见笔误
}


def _has_id_card(text: str) -> bool:
    """检查是否包含身份证号（18位）"""
    return bool(re.search(r'\d{17}[\dXx]', text))


def _is_order_content(text: str) -> bool:
    """
    判断是否是订单内容（更智能的检测）

    订单特征：
    1. 包含身份证号
    2. 包含多个"姓名+数字"组合
    3. 包含日期+票价组合
    4. 包含城市+艺人+票务特征
    """
    # 特征1: 包含身份证号
    if _has_id_card(text):
        return True

    # 特征2: 包含订单关键词
    order_keywords_strict = ["帮我整理", "订单信息", "客户信息", "帮我录入", "整理一下", "录入订单",
                             "客户：", "观影人：", "联系电话：", "姓名："]
    if any(kw in text for kw in order_keywords_strict):
        return True

    # 特征3: 包含多个"姓名+数字"模式（如"张三13800138000"）
    name_number_pattern = re.findall(r'[一-龥]{2,4}\d{6,}', text)
    if len(name_number_pattern) >= 2:
        return True

    # 特征4: 票务特征词（连坐/连座/全平台）+ 票价数字（如"周杰伦580连座"）
    if re.search(r'连[坐座]|全平台', text) and re.search(r'\d{3,4}', text):
        return True

    return False


def is_order_content(text: str) -> bool:
    """订单内容硬特征检测（公开版，供编排层域内分流复用）"""
    return _is_order_content(text)


def looks_like_event_query(text: str) -> bool:
    """
    演出查询特征检测（纯规则）。

    供 Agent 域退化路径使用：LLM 首轮未调工具时，判断是否应直接执行
    search_event 兜底查询。注意：调用方应保证订单硬特征已在上游拦截，
    此处不再做订单排除（v0.1 中该排除分支不可达，重构时移除）。
    """
    if any(kw in text for kw in EVENT_KEYWORDS_HIGH):
        return True
    if any(artist in text for artist in ARTIST_LIST):
        return True
    if any(city in text for city in CITY_LIST):
        return any(kw in text for kw in ["演出", "演唱会", "音乐节", "票"])
    return False


def classify_intent_simple(user_input: str) -> IntentType:
    """
    基于关键词的意图分类（纯规则，不调用 LLM）。

    v0.2 起作为容错映射层：LLM 分类失败时的回退，并通过
    DOMAIN_FROM_INTENT 映射到三域。
    """
    text = user_input.lower().strip()

    # 应用错别字纠正
    for typo, correct in TYPO_CORRECTIONS.items():
        text = text.replace(typo, correct)

    # 1. 订单整理（最高优先级）
    if _is_order_content(text):
        return IntentType.ORDER_PARSE

    # 1.5 订单管理操作（删除/修改等，优先级高于艺人匹配）
    manage_keywords = ["中了", "没中", "撤单", "退款", "延顺", "订单状态", "等待二开",
                       "查看订单", "我的订单", "订单列表", "更新状态", "标记中票", "标记未中",
                       "删除", "删掉", "删了", "移除", "去掉", "取消订单", "撤掉",
                       "修改", "更改", "改成", "改为", "加上", "增加", "添加", "补充", "更新订单",
                       "把", "的订单", "的单子"]  # "把...删了" 模式
    if any(kw in text for kw in manage_keywords):
        return IntentType.ORDER_MANAGE

    # 2. 演出查询
    if looks_like_event_query(text):
        return IntentType.EVENT_QUERY

    # 3. 知识问答
    if any(kw in text for kw in KNOWLEDGE_KEYWORDS):
        return IntentType.KNOWLEDGE_QA

    # 5. 默认：通用对话
    return IntentType.GENERAL


# ===========================================
# ORDER_MANAGE 子分类（删除/修改/查询/汇总）
# 从 frontend/app.py 迁入，纯关键词规则
# ===========================================

DELETE_KEYWORDS = ["删除", "删掉", "删了", "移除", "去掉", "撤掉"]
EDIT_KEYWORDS = ["修改", "更改", "更新", "改成", "改为", "加上", "增加", "添加", "补充",
                 "加一个", "加个", "前面加", "后面加", "换成"]
QUERY_KEYWORDS = ["有", "有没有", "查", "查询", "找", "单子", "订单状态"]


def classify_order_action(user_input: str) -> str:
    """
    ORDER_MANAGE 意图的子分类。

    规则（与原 Streamlit 聊天页行为一致）：
    1. delete 优先：命中删除词即删除（与编辑词同现时删除优先）
    2. query 次之：命中查询词且未命中删除/编辑词
    3. edit 再次：命中编辑词
    4. "把…订单/单子"句式无明确动作词时默认按删除处理
    5. 都不命中 → summary（订单状态汇总表）

    Args:
        user_input: 用户输入文本

    Returns:
        "delete" | "edit" | "query" | "summary"
    """
    is_delete = any(kw in user_input for kw in DELETE_KEYWORDS)
    is_edit = any(kw in user_input for kw in EDIT_KEYWORDS)
    is_query = any(kw in user_input for kw in QUERY_KEYWORDS) and not is_delete and not is_edit

    # "把...的订单/单子"模式：无明确动作词时默认当作删除
    if not is_delete and not is_edit:
        if "把" in user_input and ("订单" in user_input or "单子" in user_input):
            is_delete = True

    if is_delete:
        return "delete"
    if is_query:
        return "query"
    if is_edit:
        return "edit"
    return "summary"


# ===========================================
# 硬特征快路径（级联第 1 级：零 LLM 成本）
# ===========================================

# 「指令词+对象词」组合句式：裸指令词（如单独的"删除"）太松，不进快路径
_MANAGE_COMMAND_RE = re.compile(
    r"(把|将).{0,20}?(订单|单子).{0,10}?(删|改|撤|加|补|更新|标记)"
    r"|(删掉|删除|删了|移除|撤掉|修改|更改|改成|改为|更新).{0,10}?(订单|单子)"
)


def _hard_feature_domain(text: str) -> Domain | None:
    """
    级联第 1 级：硬特征快路径。

    两类近零误判特征，命中直接进订单域，不烧 LLM：
    1. 18 位身份证正则
    2. 「指令词+对象词」组合句式（删除/修改订单类指令）
    """
    if _has_id_card(text):
        return Domain.ORDER
    if _MANAGE_COMMAND_RE.search(text):
        return Domain.ORDER
    return None


# 五意图 → 三域映射（容错层与观测标签共用）
DOMAIN_FROM_INTENT = {
    IntentType.ORDER_PARSE: Domain.ORDER,
    IntentType.ORDER_MANAGE: Domain.ORDER,
    IntentType.EVENT_QUERY: Domain.AGENT,
    IntentType.GENERAL: Domain.AGENT,
    IntentType.KNOWLEDGE_QA: Domain.QA,
}


# LLM 三域分类提示词（级联第 2 级）
DOMAIN_CLASSIFY_PROMPT = """你是一个票务助手的路由分类器。根据用户输入，判断其应进入哪个处理域。

## 三个域

### ORDER（订单域）
用户在提交或管理订单数据。
特征：
- 提交客户信息要求整理/记录/保存（人名+身份证号、演出+票价+日期、联系电话、批量多条）
- 查看/修改/删除/标记已有订单（"删了"、"改成"、"我的订单"、"中了没"、"撤单"）

### AGENT（Agent 域）
用户在查询外部信息或进行通用对话。
特征：
- 询问演出时间/场次/余票/票价/购票平台（会触发工具调用）
- 打招呼、闲聊、不明确的请求（不会触发工具调用）

### QA（知识域）
用户询问票务规则/术语/流程等知识性问题，不涉及具体演出。
特征：
- "实名制是什么意思"、"怎么退票"、"一开二开是什么"、"连坐怎么选"

## 输出格式

只输出一个 JSON，不要输出其他内容：
{"domain": "ORDER" 或 "AGENT" 或 "QA"}

## 示例

输入：重庆凤凰传奇 10.16 1380连坐 全平台 徐洁红510122200504170063
输出：{"domain": "ORDER"}

输入：把荣佳颖的订单删了
输出：{"domain": "ORDER"}

输入：薛之谦下半年有什么演出
输出：{"domain": "AGENT"}

输入：你好
输出：{"domain": "AGENT"}

输入：代拍流程是什么
输出：{"domain": "QA"}

## 重要规则

1. 只输出 JSON，不要解释
2. 包含人名+身份证号的内容一定是 ORDER
3. 不确定时归类为 AGENT"""


def classify_domain(user_input: str, context: str = "", use_llm: bool = True) -> Domain:
    """
    路由入口（置信度级联）：硬特征快路径 → LLM 三域分类 → 关键词容错映射。

    Args:
        user_input: 用户输入的文本
        context: 上下文信息（最近的对话历史），仅用于 LLM 分类
        use_llm: False 时跳过 LLM 级（纯规则路径，供测试/降级）

    Returns:
        Domain 枚举值
    """
    text = user_input.lower().strip()

    # 级联第 1 级：硬特征快路径（零成本、近零误判）
    hard = _hard_feature_domain(text)
    if hard is not None:
        logger.info(f"[router] 硬特征快路径命中: {hard.value}")
        return hard

    # 级联第 2 级：LLM 三域分类
    if use_llm:
        from ticketpilot.core import llm

        context_prompt = DOMAIN_CLASSIFY_PROMPT
        if context:
            context_prompt += f"\n\n## 上下文\n{context}\n\n注意：如果用户的话是对上文的补充或修改（如补充票价、修改信息），应归类为 ORDER。"

        messages = [
            {"role": "system", "content": context_prompt},
            {"role": "user", "content": user_input},
        ]
        try:
            response = llm.chat(messages, temperature=0, max_tokens=50)
            content = response.get("content", "").strip()

            from ticketpilot.core.utils import extract_json
            result = extract_json(content)
            if isinstance(result, dict):
                domain_str = result.get("domain", "")
                if domain_str in Domain.__members__:
                    return Domain[domain_str]
        except Exception as e:
            logger.warning(f"LLM 域分类失败，回退关键词: {e}")

    # 级联第 3 级：关键词五分类 → 三域映射（容错）
    return DOMAIN_FROM_INTENT[classify_intent_simple(user_input)]
