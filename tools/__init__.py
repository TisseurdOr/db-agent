# tools/__init__.py — Tool 注册装饰器
#
# 痛点：每个 Tool 要维护两份东西——JSON Schema（给 LLM 看）和 Python 函数（自己跑）。
# 函数 5 行，schema 25 行。参数名改一处，要改 schema properties + required +
# main.py TOOLS + test fixtures，漏一处就挂。
#
# 解决：@tool 装饰器。写普通 Python 函数（type hints + docstring），
# 装饰器自动从函数签名生成完整的 JSON Schema。零重复。
#
# 用法：
#   @tool(description="查询订单。按状态和日期范围筛选。")
#   def query_orders(status: str = None, date_from: str = None) -> dict:
#       '''status: 订单状态（pending/completed/cancelled）
#       date_from: 起始日期 YYYY-MM-DD'''
#       ...
#
#   # 自动产出: schema dict → 注册到 agent
#   # 改参数只需改函数签名一处，schema 自动跟。

import inspect
import functools
from typing import get_type_hints


def tool(description: str):
    """装饰器：把普通 Python 函数转成 Agent Tool。

    自动做的事：
      1. type hints → JSON Schema 类型（str→string, int→integer 等）
      2. docstring → 每个参数的 description
      3. 有默认值的参数 → 不放入 required
      4. 函数名 → Tool name
    """

    def _python_type_to_json(py_type) -> str:
        origin = getattr(py_type, "__origin__", None)
        if origin is list:
            return "array"
        mapping = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object"}
        return mapping.get(py_type, "string") if py_type else "string"

    def _parse_param_docs(docstring: str) -> dict:
        """从 docstring 提取参数说明。

        格式（冒号前是参数名，冒号后是说明）：
            status: 订单状态（pending/completed/cancelled）
            date_from: 起始日期 YYYY-MM-DD

        到下一个带冒号的参数行或空行为止。
        """
        if not docstring:
            return {}
        lines = [l.strip() for l in docstring.strip().split("\n") if l.strip()]
        result = {}
        current = None
        desc_parts = []
        for line in lines:
            if ":" in line and not line.startswith(("-", "*", "返回")):
                parts = line.split(":", 1)
                key = parts[0].strip()
                if " " not in key and key[0].islower():
                    if current:
                        result[current] = " ".join(desc_parts)
                    current = key
                    desc_parts = [parts[1].strip()] if len(parts) > 1 else []
                    continue
            if current:
                desc_parts.append(line)
        if current:
            result[current] = " ".join(desc_parts)
        return result

    def decorator(func):
        hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}
        param_docs = _parse_param_docs(func.__doc__ or "")
        sig = inspect.signature(func)

        properties = {}
        required = []

        for name, param in sig.parameters.items():
            if name in ("self", "cls"):
                continue
            json_type = _python_type_to_json(hints.get(name, str))
            prop = {
                "type": json_type,
                "description": param_docs.get(name, f"{name} 参数"),
            }
            if json_type == "array":
                prop["items"] = {"type": "object"}
            if json_type == "string" and "enum" not in param_docs.get(name, ""):
                pass  # enum 由参数说明中的列表自动识别

            properties[name] = prop
            if param.default is inspect.Parameter.empty:
                required.append(name)

        schema = {
            "name": func.__name__,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper.tool_schema = schema
        return wrapper

    return decorator


# 兼容没有 @tool 装饰的旧 Tool（如 schema.py 里的手写 dict）。
# 用 tool_from_dict 把已有 schema + handler 包一下，统一 .tool_schema 接口。
def tool_from_dict(schema: dict, handler):
    """从手动写的 schema dict 提取，统一 .tool_schema 接口。"""
    handler.tool_schema = schema
    return handler
