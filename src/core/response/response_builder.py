"""Build query responses that stay inside the wire-safe public boundary."""

from __future__ import annotations

from src.core.response.multimodal_assembler import build_mcp_image_content
from src.core.types import Citation, JsonDict, QueryRequest, QueryResponse, RetrievalCandidate
from src.core.wire_safety import (
    public_citation_metadata,
    public_source_label,
    sanitize_wire_string,
)
from src.ports.response import CitationGenerator, MultimodalAssembler


class ResponseBuilder:
    """Assemble answers, citations, retrieval items, and multimodal content."""

    def __init__(
        self,
        citation_generator: CitationGenerator | None = None,
        multimodal_assembler: MultimodalAssembler | None = None,
    ) -> None:
        from src.core.response.citation_generator import (
            CitationGenerator as DefaultCitationGenerator,
        )
        from src.core.response.multimodal_assembler import (
            MultimodalAssembler as DefaultAssembler,
        )

        self.citation_generator = citation_generator or DefaultCitationGenerator()
        self.multimodal_assembler = multimodal_assembler or DefaultAssembler()

    def build(
        self,
        request: QueryRequest,
        candidates: list[RetrievalCandidate],
        trace: object | None = None,
    ) -> QueryResponse:
        citations = self.citation_generator.generate(candidates)
        images = self.multimodal_assembler.resolve_images(candidates, request.include_images)
        response = QueryResponse(
            answer=_default_answer(candidates),
            citations=citations,
            items=list(candidates),
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
        """Convert a domain response to MCP text + wire-safe structured content."""
        answer = _answer_for_mcp(response)
        content: list[JsonDict] = [
            {"type": "text", "text": _markdown(answer, response.citations)}
        ]
        content.extend(build_mcp_image_content(response.images))
        return {
            "content": content,
            "structuredContent": {
                "answer": answer,
                "citations": [
                    _structured_citation(citation) for citation in response.citations
                ],
                "request_id": response.request_id,
                "trace_id": response.trace_id,
                "metadata": public_citation_metadata(response.metadata),
            },
        }


def _default_answer(candidates: list[RetrievalCandidate]) -> str:
    """Fallback answer while a generative answer is not yet wired."""
    if not candidates:
        return "No relevant context found."
    lines = ["Retrieved context:"]
    for candidate in candidates:
        text = " ".join(candidate.text.split())
        lines.append(f"[{candidate.rank}] {text[:240]}")
    return "\n".join(lines)


def _answer_for_mcp(response: QueryResponse) -> str:
    if not response.items:
        return "未找到相关知识库内容。"
    return response.answer.strip() or "已找到相关知识库内容。"


def _markdown(answer: str, citations: list[Citation]) -> str:
    if not citations:
        return answer
    lines = [answer, "", "### 引用"]
    for index, citation in enumerate(citations, start=1):
        page = f"，第 {citation.page} 页" if citation.page is not None else ""
        label = public_source_label(citation.source_path)
        lines.append(f"[{index}] {label}{page}")
    return "\n".join(lines)


def _structured_citation(citation: Citation) -> JsonDict:
    return {
        "id": citation.citation_id,
        "source": public_source_label(citation.source_path),
        "page": citation.page,
        "chunk_id": citation.chunk_id,
        "score": citation.score,
        "text": sanitize_wire_string(citation.text),
        "metadata": public_citation_metadata(citation.metadata),
    }


__all__ = ["ResponseBuilder"]
