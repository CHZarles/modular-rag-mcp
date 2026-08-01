"""MCP Tool for retrieving one document's public summary metadata."""

from __future__ import annotations

from collections.abc import Callable

from src.core.services.knowledge_service import KnowledgeService
from src.core.types import DocumentSummary, JsonDict
from src.mcp_server.tools.base import ToolArgumentError


class GetDocumentSummaryTool:
    """Adapt document-summary lookup to a stable MCP result."""

    name = "get_document_summary"
    description = "按文档 ID 获取标题、摘要、标签和来源信息"
    input_schema: JsonDict = {
        "type": "object",
        "properties": {
            "doc_id": {
                "type": "string",
                "minLength": 1,
                "description": "摄取后生成的稳定文档 ID",
            }
        },
        "required": ["doc_id"],
        "additionalProperties": False,
    }

    def __init__(self, get_service: Callable[[], KnowledgeService]) -> None:
        self.get_service = get_service

    def call(self, arguments: JsonDict) -> JsonDict:
        """Validate the document ID and distinguish not-found from server failures."""
        unknown = sorted(set(arguments) - {"doc_id"})
        if unknown:
            raise ToolArgumentError(f"unsupported arguments: {', '.join(unknown)}")

        doc_id = arguments.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise ToolArgumentError("doc_id must be a non-empty string")
        normalized_id = doc_id.strip()

        try:
            document = self.get_service().get_document_summary(normalized_id)
        except KeyError:
            return _not_found_result(normalized_id)

        return {
            "content": [{"type": "text", "text": _markdown(document)}],
            "structuredContent": {"document": document.to_dict()},
        }


def _markdown(document: DocumentSummary) -> str:
    title = document.title.strip() if document.title and document.title.strip() else "未命名文档"
    summary = (
        document.summary.strip()
        if document.summary and document.summary.strip()
        else "暂无摘要。"
    )
    tags = "、".join(document.tags) if document.tags else "暂无标签"
    return "\n".join(
        [
            f"# {title}",
            "",
            summary,
            "",
            f"- 文档 ID：`{document.doc_id}`",
            f"- 来源：`{document.source_path}`",
            f"- 标签：{tags}",
        ]
    )


def _not_found_result(doc_id: str) -> JsonDict:
    return {
        "content": [{"type": "text", "text": f"未找到文档：`{doc_id}`。"}],
        "structuredContent": {
            "error": {"code": "document_not_found", "doc_id": doc_id}
        },
        "isError": True,
    }


__all__ = ["GetDocumentSummaryTool"]
