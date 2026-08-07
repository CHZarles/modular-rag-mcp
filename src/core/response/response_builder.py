"""Build query responses that stay inside the wire-safe public boundary."""

from __future__ import annotations

from src.core.response.multimodal_assembler import build_mcp_image_content
from src.core.types import ImagePayload, JsonDict, QueryRequest, QueryResponse, RetrievalCandidate
from src.core.wire_safety import (
    public_citation_metadata,
    public_source_label,
    sanitize_wire_string,
)
from src.ports.response import MultimodalAssembler


class ResponseBuilder:
    """Assemble retrieval results and their multimodal evidence."""

    def __init__(
        self,
        multimodal_assembler: MultimodalAssembler | None = None,
    ) -> None:
        from src.core.response.multimodal_assembler import (
            MultimodalAssembler as DefaultAssembler,
        )

        self.multimodal_assembler = multimodal_assembler or DefaultAssembler()

    def build(
        self,
        request: QueryRequest,
        candidates: list[RetrievalCandidate],
        trace: object | None = None,
    ) -> QueryResponse:
        images = self.multimodal_assembler.resolve_images(candidates, request.include_images)
        response = QueryResponse(
            results=list(candidates),
            images=images,
            request_id=request.request_id,
            metadata={
                "collection": request.collection,
                "candidate_count": len(candidates),
                "image_count": len(images),
            },
        )
        if trace is not None and hasattr(trace, "record_stage"):
            trace.record_stage("response_build", response.metadata)
        return response

    def build_mcp_result(self, response: QueryResponse) -> JsonDict:
        """Convert retrieval results to MCP text, structured data, and images."""
        content: list[JsonDict] = [{"type": "text", "text": _markdown(response.results)}]
        wire_images: list[ImagePayload] = []
        for image in response.images:
            image_content = build_mcp_image_content([image])
            if image_content:
                wire_images.append(image)
                content.extend(image_content)
        return {
            "content": content,
            "structuredContent": {
                "results": [
                    _structured_result(candidate, wire_images)
                    for candidate in response.results
                ],
                "request_id": response.request_id,
                "trace_id": response.trace_id,
                "metadata": public_citation_metadata(response.metadata),
            },
        }


def _markdown(results: list[RetrievalCandidate]) -> str:
    if not results:
        return "未找到相关知识库内容。"
    lines = ["检索结果："]
    for result in results:
        page_number = _page(result)
        page = f"，第 {page_number} 页" if page_number is not None else ""
        source = public_source_label(result.metadata.get("source_path"))
        text = sanitize_wire_string(result.text.strip())
        lines.extend(["", f"[{result.rank}] {source}{page}", text])
    return "\n".join(lines)


def _structured_result(
    candidate: RetrievalCandidate,
    images: list[ImagePayload],
) -> JsonDict:
    return {
        "rank": candidate.rank,
        "chunk_id": candidate.chunk_id,
        "text": sanitize_wire_string(candidate.text),
        "score": candidate.score,
        "score_kind": _score_kind(candidate),
        "source": public_source_label(candidate.metadata.get("source_path")),
        "page": _page(candidate),
        "metadata": public_citation_metadata(candidate.metadata),
        "images": [
            {
                "image_id": image.image_id,
                "mime_type": image.mime_type,
                "content_index": index + 1,
            }
            for index, image in enumerate(images)
            if image.source_ref == candidate.chunk_id
        ],
    }


def _score_kind(candidate: RetrievalCandidate) -> str:
    if candidate.source == "fusion":
        return "rrf"
    if candidate.source == "rerank":
        return "rerank"
    value = candidate.debug.get("score_kind")
    return value if isinstance(value, str) else "unknown"


def _page(candidate: RetrievalCandidate) -> int | None:
    value = candidate.metadata.get("page")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = ["ResponseBuilder"]
