"""构建领域层查询响应。"""

from __future__ import annotations

from src.core.types import Citation, JsonDict, QueryRequest, QueryResponse, RetrievalCandidate
from src.ports.response import CitationGenerator, MultimodalAssembler


class ResponseBuilder:
    """统一组装答案、引用、检索项和多模态内容。"""

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
        """把领域响应转换为 MCP Tool 可直接返回的文本和结构化内容。"""
        answer = _answer_for_mcp(response)
        return {
            "content": [{"type": "text", "text": _markdown(answer, response.citations)}],
            "structuredContent": {
                "answer": answer,
                "citations": [
                    _structured_citation(citation) for citation in response.citations
                ],
                "request_id": response.request_id,
                "metadata": dict(response.metadata),
            },
        }


def _default_answer(candidates: list[RetrievalCandidate]) -> str:
    """在生成式回答尚未接入时返回可读的检索上下文。"""
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
        lines.append(f"[{index}] {citation.source_path or '未知来源'}{page}")
    return "\n".join(lines)


def _structured_citation(citation: Citation) -> JsonDict:
    return {
        "id": citation.citation_id,
        "source": citation.source_path,
        "page": citation.page,
        "chunk_id": citation.chunk_id,
        "score": citation.score,
        "text": citation.text,
        "metadata": dict(citation.metadata),
    }
