"""Small MCP tool adapter interface.

Concrete tools should translate JSON arguments into application service
requests, then translate domain responses back into MCP-shaped dictionaries.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.types import JsonDict


@runtime_checkable
class ToolHandler(Protocol):
    name: str
    input_schema: JsonDict

    def call(self, arguments: JsonDict) -> JsonDict: ...
