"""
LLM 调用封装

基于 OpenAI 兼容接口，支持 DeepSeek / Qwen / GPT 等模型。
切换模型只需修改 .env 中的配置，无需改代码。
"""

from openai import OpenAI

import config

# 全局单例客户端
_client: OpenAI | None = None

# 进程级调用计数器：编排层用「请求前后的差值」统计单次 chat 的 LLM 成本，
# 不在业务函数间传递计数参数（侵入性为零，测试 monkeypatch 时计数不动也无妨）
_CALL_COUNT = 0


def get_call_count() -> int:
    """返回本进程累计 LLM 调用次数（观测用）"""
    return _CALL_COUNT


def get_llm_client() -> OpenAI:
    """获取 LLM 客户端实例（单例模式，避免重复创建连接池）"""
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
            timeout=30,  # 添加超时
        )
    return _client


def chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    tools: list[dict] | None = None,
) -> dict:
    """
    调用 LLM 进行对话。

    Args:
        messages: 对话消息列表，格式 [{"role": "user", "content": "..."}]
        model: 模型名称，默认使用配置中的模型
        temperature: 温度参数，越低越确定
        max_tokens: 最大输出 token 数
        tools: Function Calling 工具定义列表

    Returns:
        LLM 响应对象（dict），包含 message.content 和可能的 tool_calls
    """
    global _CALL_COUNT
    _CALL_COUNT += 1  # 计入尝试：失败的调用同样产生成本与延迟

    client = get_llm_client()

    kwargs = {
        "model": model or config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    # 如果传入了工具定义，启用 Function Calling
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    response = client.chat.completions.create(**kwargs)

    # 提取第一个 choice
    choice = response.choices[0]

    result = {
        "content": choice.message.content,
        "tool_calls": None,
        "finish_reason": choice.finish_reason,
    }

    # 如果有工具调用，提取出来
    if choice.message.tool_calls:
        result["tool_calls"] = [
            {
                "id": tc.id,
                "function": tc.function.name,
                "arguments": tc.function.arguments,
            }
            for tc in choice.message.tool_calls
        ]

    return result
