"""
意图路由模块（纯关键词版，不依赖 LLM）

根据用户输入判断应该走哪条处理路线：
- ORDER_PARSE: 订单信息整理
- EVENT_QUERY: 演出信息查询
- KNOWLEDGE_QA: 票务知识问答
- ORDER_MANAGE: 订单管理操作
- GENERAL: 通用对话
"""

import re
import logging
from enum import Enum

logger = logging.getLogger(__name__)

# 从常量文件导入城市列表和艺人列表
from ticketpilot.data.constants import CITIES, HOT_ARTISTS


class IntentType(str, Enum):
    """意图类型枚举"""
    ORDER_PARSE = "ORDER_PARSE"
    EVENT_QUERY = "EVENT_QUERY"
    KNOWLEDGE_QA = "KNOWLEDGE_QA"
    ORDER_MANAGE = "ORDER_MANAGE"
    GENERAL = "GENERAL"


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


def classify_intent_simple(user_input: str) -> IntentType:
    """
    基于关键词的意图分类（纯规则，不调用 LLM）。

    Args:
        user_input: 用户输入的文本

    Returns:
        IntentType 枚举值
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

    # 2. 演出查询（高优先级）
    # 2.1 直接包含演出相关关键词
    if any(kw in text for kw in EVENT_KEYWORDS_HIGH):
        return IntentType.EVENT_QUERY

    # 2.2 艺人名 + 询问性内容（如"薛之谦下半年演出"）
    # 但排除订单内容（如"重庆凤凰传奇 10.16 1380连坐..."）
    if any(artist in text for artist in ARTIST_LIST):
        # 如果同时包含日期和票价，可能是订单
        if re.search(r'\d+\.\d+|\d+月\d+', text) and re.search(r'\d{3,4}', text):
            if any(kw in text for kw in ["连坐", "连座", "全平台"]):
                return IntentType.ORDER_PARSE
        return IntentType.EVENT_QUERY

    # 2.3 城市 + 演出相关词
    if any(city in text for city in CITY_LIST):
        if any(kw in text for kw in ["演出", "演唱会", "音乐节", "票"]):
            return IntentType.EVENT_QUERY

    # 3. 知识问答
    if any(kw in text for kw in KNOWLEDGE_KEYWORDS):
        return IntentType.KNOWLEDGE_QA

    # 5. 默认：通用对话
    return IntentType.GENERAL


# LLM 意图分类提示词
INTENT_CLASSIFY_PROMPT = """你是一个票务助手的意图分类器。根据用户输入，判断其意图类别。

## 意图类别

### ORDER_PARSE（订单整理）
用户发送了客户订单信息，需要整理保存。
特征：
- 包含人名+身份证号（如"张三 123456789012345678"）
- 包含演出信息+票价+日期（如"上海薛之谦 10.11 1680"）
- 包含联系电话
- 批量发送多条订单信息要求整理
- 包含"整理"、"记录"、"保存"等词+订单内容

### ORDER_MANAGE（订单管理）
用户要查看、修改或删除已有订单。
特征：
- 删除类："删了"、"删除"、"去掉"、"移除"、"把XX的订单删了"、"清空订单"
- 修改类："修改"、"改成"、"改为"、"把票价改成"、"加个观影人"、"更新"
- 查看类："查看订单"、"我的订单"、"订单列表"、"订单状态"、"中了没"
- 状态类："标记中票"、"标记未中"、"撤单"、"退款"

### EVENT_QUERY（演出查询）
用户询问演出/票务相关信息。
特征：
- 询问演出时间、场次（"薛之谦什么时候开票"、"周杰伦演出时间"）
- 询问票务信息（"有没有余票"、"票价多少"）
- 包含艺人名/城市名+询问词
- 询问购票平台

### KNOWLEDGE_QA（知识问答）
用户询问票务相关的知识性问题。
特征：
- 询问流程（"代拍流程是什么"、"怎么抢票"）
- 询问规则（"实名制是什么意思"、"连坐怎么选"）
- 不涉及具体演出

### GENERAL（通用对话）
其他所有内容，包括：
- 打招呼、闲聊
- 不明确的请求
- 格式选择（"默认"、"默认格式"）

## 输出格式

只输出一个 JSON，不要输出其他内容：
{"intent": "意图类别"}

## 示例

输入：重庆凤凰传奇 10.16 1380连坐 全平台 徐洁红510122200504170063 顾良强510623200101267518
输出：{"intent": "ORDER_PARSE"}

输入：把荣佳颖的订单删了
输出：{"intent": "ORDER_MANAGE"}

输入：删除所有订单
输出：{"intent": "ORDER_MANAGE"}

输入：薛之谦下半年有什么演出
输出：{"intent": "EVENT_QUERY"}

输入：代拍流程是什么
输出：{"intent": "KNOWLEDGE_QA"}

输入：你好
输出：{"intent": "GENERAL"}

输入：默认
输出：{"intent": "ORDER_PARSE"}

## 重要规则

1. 只输出 JSON，不要解释
2. 如果用户发送了包含人名+身份证号的内容，一定是 ORDER_PARSE
3. "删了"、"删除"、"去掉"等词出现在任何上下文中，都是 ORDER_MANAGE
4. 不确定时归类为 GENERAL"""


def classify_intent_with_llm(user_input: str, context: str = "") -> IntentType:
    """
    使用 LLM 进行意图分类。

    Args:
        user_input: 用户输入的文本
        context: 上下文信息（最近的对话历史）

    Returns:
        IntentType 枚举值
    """
    from ticketpilot.core import llm

    # 构建包含上下文的提示
    context_prompt = INTENT_CLASSIFY_PROMPT
    if context:
        context_prompt += f"\n\n## 上下文\n{context}\n\n注意：如果用户的话是对上文的补充或修改（如补充票价、修改信息），应归类为 ORDER_MANAGE。"

    messages = [
        {"role": "system", "content": context_prompt},
        {"role": "user", "content": user_input},
    ]

    try:
        response = llm.chat(messages, temperature=0, max_tokens=50)
        content = response.get("content", "").strip()

        # 提取 JSON
        from ticketpilot.core.utils import extract_json
        result = extract_json(content)
        if isinstance(result, dict):
            intent_str = result.get("intent", "GENERAL")

            # 映射到 IntentType
            intent_map = {
                "ORDER_PARSE": IntentType.ORDER_PARSE,
                "ORDER_MANAGE": IntentType.ORDER_MANAGE,
                "EVENT_QUERY": IntentType.EVENT_QUERY,
                "KNOWLEDGE_QA": IntentType.KNOWLEDGE_QA,
                "GENERAL": IntentType.GENERAL,
            }
            return intent_map.get(intent_str, IntentType.GENERAL)
    except Exception as e:
        logger.warning(f"LLM 意图分类失败: {e}")

    # 失败时回退到关键词分类
    return classify_intent_simple(user_input)


def classify_intent(user_input: str, context: str = "") -> IntentType:
    """
    意图分类入口：优先使用 LLM，失败时回退到关键词。

    Args:
        user_input: 用户输入的文本
        context: 上下文信息

    Returns:
        IntentType 枚举值
    """
    return classify_intent_with_llm(user_input, context)
