"""构建领域层查询响应。"""

from __future__ import annotations

from src.core.types import QueryRequest, QueryResponse, RetrievalCandidate
from src.ports.response import CitationGenerator, MultimodalAssembler


class ResponseBuilder:
    """统一组装答案、引用、检索项和多模态内容。"""

    def __init__(
        self,
        citation_generator: CitationGenerator | None = None,
        multimodal_assembler: MultimodalAssembler | None = None,
    ) -> None:
        from src.core.response.citation_generator import CitationGenerator as DefaultCitationGenerator
        from src.core.response.multimodal_assembler import MultimodalAssembler as DefaultAssembler

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


def _default_answer(candidates: list[RetrievalCandidate]) -> str:
    """在生成式回答尚未接入时返回可读的检索上下文。"""
    if not candidates:
        return "No relevant context found."
    lines = ["Retrieved context:"]
    for candidate in candidates:
        text = " ".join(candidate.text.split())
        lines.append(f"[{candidate.rank}] {text[:240]}")
    return "\n".join(lines)
