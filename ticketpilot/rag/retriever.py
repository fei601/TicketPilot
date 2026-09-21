"""
RAG 检索模块

基于关键词匹配的知识库检索（无向量数据库依赖）。
知识库文档来自 knowledge_base/*.md，由 loader 按 "##" 小节切分。
"""

from ticketpilot.rag.loader import load_knowledge_docs


class SimpleRetriever:
    """
    简单关键词检索器（不依赖向量数据库）。

    用作降级方案或开发阶段使用。
    """

    def __init__(self):
        self.docs = load_knowledge_docs()

    def retrieve(self, query: str, top_k: int = 3) -> list[dict]:
        """
        基于关键词匹配检索相关文档。

        Args:
            query: 用户查询
            top_k: 返回结果数量

        Returns:
            相关文档列表
        """
        scored_docs = []

        for doc in self.docs:
            score = self._calculate_score(query, doc["content"])
            if score > 0:
                scored_docs.append({**doc, "score": score})

        # 按分数降序排列
        scored_docs.sort(key=lambda x: x["score"], reverse=True)

        return scored_docs[:top_k]

    def _calculate_score(self, query: str, content: str) -> float:
        """
        计算查询与文档的匹配分数（简单的关键词匹配）。

        Args:
            query: 查询文本
            content: 文档内容

        Returns:
            匹配分数
        """
        query_lower = query.lower()
        content_lower = content.lower()

        score = 0.0

        # 完整匹配
        if query_lower in content_lower:
            score += 10.0

        # 分词匹配
        query_words = query_lower.split()
        for word in query_words:
            if len(word) >= 2 and word in content_lower:
                score += 1.0

        return score


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
