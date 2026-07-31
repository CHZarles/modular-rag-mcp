"""精简的 MCP Tool 适配器接口。

具体 Tool 把 JSON 参数转换成应用服务请求，再把领域响应转换回 MCP 需要的字典结构。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.types import JsonDict


class ToolArgumentError(ValueError):
    """Tool 在应用参数校验失败时抛出的公开边界错误。"""


@runtime_checkable
class ToolHandler(Protocol):
    """MCP Tool 的名称、输入 Schema 与调用契约。"""

    name: str
    description: str
    input_schema: JsonDict

    def call(self, arguments: JsonDict) -> JsonDict: ...
