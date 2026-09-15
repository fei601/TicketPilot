"""
工具注册机制

用装饰器注册 Function Calling 工具，扩展方便。
"""

from typing import Callable

# 全局工具注册表
_tool_registry: dict[str, dict] = {}


def register_tool(
    name: str,
    description: str,
    parameters: dict,
) -> Callable:
    """
    装饰器：注册一个 Function Calling 工具。

    用法：
        @register_tool(
            name="search_event",
            description="查询演出信息",
            parameters={"type": "object", "properties": {...}}
        )
        def search_event(keyword: str) -> dict:
            ...
    """

    def decorator(func: Callable) -> Callable:
        _tool_registry[name] = {
            "function": func,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            },
        }
        return func

    return decorator


def get_all_tool_schemas() -> list[dict]:
    """获取所有已注册工具的 schema（传给 LLM 用）"""
    return [tool["schema"] for tool in _tool_registry.values()]


def execute_tool(name: str, arguments: dict) -> str:
    """
    执行一个已注册的工具。

    Args:
        name: 工具名称
        arguments: 工具参数

    Returns:
        工具执行结果（字符串）
    """
    if name not in _tool_registry:
        return f"错误：工具 '{name}' 未注册"

    func = _tool_registry[name]["function"]
    try:
        result = func(**arguments)
        return str(result)
    except Exception as e:
        return f"工具执行错误：{e}"


def list_tools() -> list[str]:
    """列出所有已注册工具名称"""
    return list(_tool_registry.keys())
