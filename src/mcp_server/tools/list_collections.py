"""MCP Tool for listing available knowledge collections."""

from __future__ import annotations

from collections.abc import Callable

from src.core.services.knowledge_service import KnowledgeService
from src.core.types import CollectionInfo, JsonDict
from src.mcp_server.tools.base import ToolArgumentError


class ListCollectionsTool:
    """Expose collection statistics through the KnowledgeService boundary."""

    name = "list_collections"
    description = "列出当前可用的知识集合及其文档、片段和图片数量"
    input_schema: JsonDict = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, get_service: Callable[[], KnowledgeService]) -> None:
        self.get_service = get_service

    def call(self, arguments: JsonDict) -> JsonDict:
        """Reject unsupported input and return text plus structured statistics."""
        if arguments:
            unknown = ", ".join(sorted(arguments))
            raise ToolArgumentError(f"unsupported arguments: {unknown}")

        collections = self.get_service().list_collections()
        return {
            "content": [{"type": "text", "text": _markdown(collections)}],
            "structuredContent": {
                "collections": [collection.to_dict() for collection in collections],
                "total": len(collections),
            },
        }


def _markdown(collections: list[CollectionInfo]) -> str:
    if not collections:
        return "当前没有可用的知识集合。"

    lines = ["可用知识集合："]
    for collection in collections:
        summary = (
            f"- `{collection.name}`：{collection.document_count} 个文档，"
            f"{collection.chunk_count} 个片段，{collection.image_count} 张图片"
        )
        description = collection.metadata.get("description")
        if isinstance(description, str) and description.strip():
            summary = f"{summary}。{description.strip()}"
        lines.append(summary)
    return "\n".join(lines)


__all__ = ["ListCollectionsTool"]
