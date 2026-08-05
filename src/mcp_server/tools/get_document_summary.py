"""MCP Tool for retrieving one document's public summary metadata."""

from __future__ import annotations

from collections.abc import Callable

from src.core.services.knowledge_service import KnowledgeService
from src.core.types import DocumentSummary, JsonDict
from src.mcp_server.tools.base import (
    ToolArgumentError,
    public_citation_metadata,
    public_source_label,
    validate_identifier,
)

_ALLOWED_ARGUMENTS: frozenset[str] = frozenset({"doc_id"})


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
                "maxLength": 128,
                "description": "摄取后生成的稳定文档 ID",
            }
        },
        "required": ["doc_id"],
        "additionalProperties": False,
    }

    def __init__(self, get_service: Callable[[], KnowledgeService]) -> None:
        self.get_service = get_service

    def call(self, arguments: JsonDict) -> JsonDict:
        """Validate the document ID and surface not-found vs internal failures."""
        unknown = sorted(set(arguments) - _ALLOWED_ARGUMENTS)
        if unknown:
            raise ToolArgumentError(f"unsupported arguments: {', '.join(unknown)}")

        doc_id = validate_identifier(arguments.get("doc_id"), field="doc_id")

        try:
            document = self.get_service().get_document_summary(doc_id)
        except KeyError:
            return _not_found_result(doc_id)

        public_document = _public_document_summary(document)
        return {
            "content": [{"type": "text", "text": _markdown(public_document)}],
            "structuredContent": {"document": public_document.to_dict()},
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
            f"- 来源：`{public_source_label(document.source_path)}`",
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


def _public_document_summary(document: DocumentSummary) -> DocumentSummary:
    """Return a summary with only the wire-safe metadata fields."""
    sanitized = public_citation_metadata(dict(document.metadata))
    sanitized.pop("source_path", None)
    return DocumentSummary(
        doc_id=document.doc_id,
        source_path=public_source_label(document.source_path),
        title=document.title,
        summary=document.summary,
        tags=list(document.tags),
        metadata=sanitized,
    )


__all__ = ["GetDocumentSummaryTool"]
