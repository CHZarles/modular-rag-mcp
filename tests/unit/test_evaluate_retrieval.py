from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.evaluate_retrieval import _Case, _evaluate, _selected_strategy
from src.core.types import QueryRequest, RetrievalCandidate


class _Engine:
    def search(self, request: QueryRequest) -> list[RetrievalCandidate]:
        section = "expected" if request.query == "hit" else "other"
        return [
            RetrievalCandidate(
                chunk_id=section,
                text=section,
                metadata={"section_id": section},
                score=1.0,
                source="fusion",
                rank=1,
            )
        ]


class _ImageEngine:
    def __init__(self, image_path: Path) -> None:
        self.image_path = image_path

    def search(self, request: QueryRequest) -> list[RetrievalCandidate]:
        image_path = (
            self.image_path
            if request.query == "available"
            else self.image_path.with_name("missing.png")
        )
        return [
            RetrievalCandidate(
                chunk_id=request.query,
                text="image caption",
                metadata={
                    "section_id": request.query,
                    "images": [
                        {"id": "image-1", "path": str(image_path), "mime_type": "image/png"}
                    ],
                },
                score=1.0,
                source="sparse",
                rank=1,
            )
        ]


def test_retrieval_metrics_count_hits_and_misses() -> None:
    report = _evaluate(
        _Engine(),  # type: ignore[arg-type]
        [
            _Case("hit", frozenset({"expected"})),
            _Case("miss", frozenset({"expected"})),
        ],
    )

    assert report == {
        "case_count": 2,
        "hit_at_5": 0.5,
        "mrr_at_5": 0.5,
        "misses": ["miss"],
    }


def test_image_metrics_require_the_original_image_to_be_returnable(tmp_path: Path) -> None:
    image_path = tmp_path / "diagram.png"
    image_path.write_bytes(b"png")

    report = _evaluate(
        _ImageEngine(image_path),  # type: ignore[arg-type]
        [
            _Case("available", frozenset({"available"}), kind="image"),
            _Case("missing", frozenset({"missing"}), kind="image"),
        ],
    )

    assert report["hit_at_5"] == 0.5
    assert report["misses"] == ["missing"]


@pytest.mark.parametrize(
    ("dense", "sparse", "expected"),
    [(True, True, "hybrid"), (True, False, "dense"), (False, True, "bm25")],
)
def test_selected_strategy_matches_production_flags(
    dense: bool,
    sparse: bool,
    expected: str,
) -> None:
    settings = SimpleNamespace(
        retrieval={"enable_dense": dense, "enable_sparse": sparse}
    )

    assert _selected_strategy(settings) == expected
