"""Build query responses that stay inside the wire-safe public boundary."""

from __future__ import annotations

import math
from collections.abc import Mapping

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
        metadata: JsonDict = {
            "collection": request.collection,
            "candidate_count": len(candidates),
            "image_count": len(images),
        }
        route_statuses = _retrieval_route_statuses(trace)
        if route_statuses:
            metadata["_retrieval_routes"] = route_statuses
        response = QueryResponse(
            results=list(candidates),
            images=images,
            request_id=request.request_id,
            metadata=metadata,
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
        route_value = response.metadata.get("_retrieval_routes")
        route_statuses = route_value if isinstance(route_value, Mapping) else {}
        return {
            "content": content,
            "structuredContent": {
                "results": [
                    _structured_result(candidate, wire_images, route_statuses)
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
    route_statuses: Mapping[object, object],
) -> JsonDict:
    return {
        "rank": candidate.rank,
        "chunk_id": candidate.chunk_id,
        "text": sanitize_wire_string(candidate.text),
        "score": candidate.score,
        "score_kind": _score_kind(candidate),
        "score_stages": _score_stages(candidate, route_statuses),
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


def _score_stages(
    candidate: RetrievalCandidate,
    route_statuses: Mapping[object, object],
) -> list[JsonDict]:
    rrf_value = candidate.debug.get("rrf")
    rrf = rrf_value if isinstance(rrf_value, Mapping) else {}
    source_value = rrf.get("sources")
    sources = source_value if isinstance(source_value, list) else []
    k = _positive_int(rrf.get("k"))
    stages = [
        _recall_score_stage(candidate, sources, route_statuses, k, "dense", "dense"),
        _recall_score_stage(candidate, sources, route_statuses, k, "bm25", "sparse"),
    ]
    fusion_score = _finite_float(rrf.get("score"))
    stages.append(
        {
            "stage": "fusion",
            "status": "success" if fusion_score is not None else "unavailable",
            "rank": candidate.rank if candidate.source == "fusion" else None,
            "score": fusion_score,
            "score_kind": "rrf",
            "rrf_contribution": None,
            "k": k,
            "backend": None,
        }
    )
    rerank_value = candidate.debug.get("rerank")
    rerank = rerank_value if isinstance(rerank_value, Mapping) else {}
    fallback = rerank.get("fallback") is True
    rerank_score = None if fallback else _finite_float(rerank.get("score"))
    rerank_status = (
        "fallback"
        if fallback
        else "success"
        if rerank_score is not None
        else "ranking_only"
        if candidate.source == "rerank"
        else "skipped"
    )
    backend = rerank.get("backend")
    stages.append(
        {
            "stage": "rerank",
            "status": rerank_status,
            "rank": candidate.rank if candidate.source == "rerank" else None,
            "score": rerank_score,
            "score_kind": "rerank" if rerank_score is not None else "none",
            "rrf_contribution": None,
            "k": None,
            "backend": sanitize_wire_string(backend) if isinstance(backend, str) else None,
        }
    )
    return stages


def _recall_score_stage(
    candidate: RetrievalCandidate,
    sources: list[object],
    route_statuses: Mapping[object, object],
    k: int | None,
    stage: str,
    source_name: str,
) -> JsonDict:
    source = next(
        (
            item
            for item in sources
            if isinstance(item, Mapping) and item.get("source") == source_name
        ),
        None,
    )
    score = _finite_float(source.get("score")) if source is not None else None
    rank = _positive_int(source.get("rank")) if source is not None else None
    route_status = route_statuses.get(source_name)
    status = (
        "hit"
        if source is not None
        else route_status
        if route_status in {"skipped", "error"}
        else "not_recalled"
        if route_status == "success" or sources
        else "unavailable"
    )
    score_kind = (
        "bm25" if stage == "bm25" else candidate.debug.get("score_kind")
    ) if source is not None else "none"
    return {
        "stage": stage,
        "status": status,
        "rank": rank,
        "score": score,
        "score_kind": score_kind if isinstance(score_kind, str) else "unknown",
        "rrf_contribution": 1.0 / (k + rank) if k is not None and rank is not None else None,
        "k": None,
        "backend": None,
    }


def _finite_float(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _retrieval_route_statuses(trace: object | None) -> JsonDict:
    if trace is None or not hasattr(trace, "get_stage_data"):
        return {}
    statuses: JsonDict = {}
    for route in ("dense", "sparse"):
        data = trace.get_stage_data(f"{route}_retrieval")
        if data is None:
            statuses[route] = "skipped"
            continue
        details = data.get("details")
        status = details.get("status") if isinstance(details, Mapping) else None
        statuses[route] = status if status in {"success", "error"} else "unavailable"
    return statuses


def _page(candidate: RetrievalCandidate) -> int | None:
    value = candidate.metadata.get("page")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = ["ResponseBuilder"]
