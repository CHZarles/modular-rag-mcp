"""根据检索候选结果生成引用信息。"""

from __future__ import annotations

from src.core.types import Citation, RetrievalCandidate


class CitationGenerator:
    """把候选 Chunk 转换为稳定编号的引用记录。"""

    def generate(self, candidates: list[RetrievalCandidate]) -> list[Citation]:
        return [
            Citation(
                citation_id=f"c{index}",
                chunk_id=candidate.chunk_id,
                source_path=str(candidate.metadata.get("source_path", "")),
                page=candidate.metadata.get("page") if isinstance(candidate.metadata.get("page"), int) else None,
                text=_snippet(candidate.text),
                score=candidate.score,
                metadata={
                    "rank": candidate.rank,
                    "source": candidate.source,
                    **{
                        key: value
                        for key, value in candidate.metadata.items()
                        if key not in {"source_path", "page"}
                    },
                },
            )
            for index, candidate in enumerate(candidates, start=1)
        ]


def _snippet(text: str, limit: int = 320) -> str:
    """压缩空白并截取适合展示的引用片段。"""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."
