"""
票务知识库问答工具

核心原则：
1. 优先从知识库检索
2. 搜到就回答
3. 搜不到就说不知道，禁止胡编乱造
"""

import json

from ticketpilot.rag.retriever import retrieve_from_knowledge
from ticketpilot.tools.base import register_tool


@register_tool(
    name="search_knowledge",
    description="从票务知识库中搜索信息。包括术语解释、平台规则、抢票技巧等。优先使用此工具回答票务知识问题。",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索问题，如：一开是什么意思、大麦退票规则、抢票技巧",
            },
        },
        "required": ["query"],
    },
)
def search_knowledge(query: str) -> str:
    """
    从知识库搜索票务知识。

    搜到 → 返回相关内容
    搜不到 → 明确告知无相关信息
    """
    # 检索知识库
    result = retrieve_from_knowledge(query, top_k=3)

    # 判断是否有结果
    if "未在知识库中找到" in result:
        return json.dumps({
            "found": False,
            "message": f"抱歉，知识库暂无「{query}」的相关信息，目前功能还在完善中。",
            "suggestion": "建议您咨询业内人士或查阅官方资料。",
        }, ensure_ascii=False)

    return json.dumps({
        "found": True,
        "content": result,
        "message": "以下是从知识库检索到的相关信息：",
    }, ensure_ascii=False, indent=2)
