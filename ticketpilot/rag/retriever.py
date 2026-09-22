"""
RAG 检索模块（v0.2 硬门控版）

基于字符 bigram 覆盖度的知识库检索（无向量数据库、无分词器依赖）。
知识库文档来自 knowledge_base/*.md，由 loader 按 "##" 切分小节。

硬门控：得分低于 MIN_RETRIEVE_SCORE 视为未命中，retrieve 返回空——
调用方据此直接拒答。宁可拒答，不可错答：错答的代价（用户按错误
规则操作真金白银的订单）远高于拒答（用户去问别人）。
"""

import re

from ticketpilot.rag.loader import load_knowledge_docs

# 硬门控阈值：查询 bigram 至少这个比例命中文档才算检索成功（0~1）。
# 初始值 0.5，D5 评测集落地后用 误拒率/误答率 校准。
MIN_RETRIEVE_SCORE = 0.5

# 疑问句式噪声词：不携带主题信息，只会稀释覆盖度分母
_QUESTION_NOISE_RE = re.compile(r'什么是|是什么|怎么|怎样|如何|请问|吗|呢|啊|呀|的|了')


def _bigrams(text: str) -> set:
    """字符 bigram 集合；不足 2 字符时退化为单字符集合。
    为什么不用空白分词：中文整句无空格，split 后是单一 token，
    除完全子串命中外得分恒为 0，检索形同抛硬币。"""
    t = re.sub(r'\s+', '', text.lower())
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


class SimpleRetriever:
    """
    简单关键词检索器（不依赖向量数据库）。

    用作降级方案或开发阶段使用。
    """

    def __init__(self):
        self.docs = load_knowledge_docs()

    def retrieve(self, query: str, top_k: int = 3) -> list[dict]:
        """
        检索相关文档；低于阈值的弱命中直接丢弃（硬门控）。

        Args:
            query: 用户查询
            top_k: 返回结果数量

        Returns:
            相关文档列表（score >= MIN_RETRIEVE_SCORE）；未达阈值返回 []
        """
        scored_docs = []

        for doc in self.docs:
            score = self._calculate_score(query, doc["content"])
            if score >= MIN_RETRIEVE_SCORE:
                scored_docs.append({**doc, "score": score})

        # 按分数降序排列
        scored_docs.sort(key=lambda x: x["score"], reverse=True)

        return scored_docs[:top_k]

    def _calculate_score(self, query: str, content: str) -> float:
        """
        查询对文档的 bigram 覆盖度（0~1）：
        |查询bigram ∩ 文档bigram| / |查询bigram|。

        先剥疑问噪声词（"什么是/怎么/吗"），避免句式词稀释主题词；
        剥离后的查询整体命中文档时直接给满分 1.0。
        """
        query_lower = query.lower()
        cleaned = _QUESTION_NOISE_RE.sub('', query_lower)
        query_bigrams = _bigrams(cleaned or query_lower)
        if not query_bigrams:
            return 0.0

        content_lower = content.lower()
        if cleaned and cleaned in content_lower:
            return 1.0

        content_bigrams = _bigrams(content_lower)
        return len(query_bigrams & content_bigrams) / len(query_bigrams)


# 全局检索器实例
_retriever: SimpleRetriever | None = None


def get_retriever() -> SimpleRetriever:
    """获取检索器单例"""
    global _retriever
    if _retriever is None:
        _retriever = SimpleRetriever()
    return _retriever


def retrieve_from_knowledge(query: str, top_k: int = 3) -> str:
    """
    从知识库检索相关信息（供 LLM 或其他模块调用）。

    Args:
        query: 查询文本
        top_k: 返回结果数量

    Returns:
        拼接后的相关文档内容；无结果时返回空字符串 ""
        （调用方以"是否为空"判断，不依赖特定文案）
    """
    retriever = get_retriever()
    results = retriever.retrieve(query, top_k)

    if not results:
        return ""

    parts = []
    for i, doc in enumerate(results, 1):
        parts.append(f"【来源：{doc['title']}】\n{doc['content']}")

    return "\n\n---\n\n".join(parts)
