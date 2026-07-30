"""RRF 排名融合的公式、去重和确定性测试。"""

from __future__ import annotations

from typing import Literal

import pytest

from src.core.query_engine import RRFFusion, rrf_score
from src.core.types import RetrievalCandidate
from src.ports.query import FusionStrategy


def test_rrf_score_uses_standard_formula_and_configurable_k() -> None:
    assert rrf_score(rank=1) == pytest.approx(1 / 61)
    assert rrf_score(rank=3, k=10) == pytest.approx(1 / 13)
    assert rrf_score(rank=1, k=10) > rrf_score(rank=1, k=60)


@pytest.mark.parametrize(
    ("rank", "k", "message"),
    [(0, 60, "rank must be positive"), (1, 0, "k must be positive")],
)
def test_rrf_score_rejects_non_positive_parameters(
    rank: int,
    k: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        rrf_score(rank, k)


def test_fuse_combines_dense_and_sparse_ranks_without_comparing_raw_scores() -> None:
    dense = [
        _candidate("a", "dense", rank=99, score=0.01),
        _candidate("b", "dense", rank=98, score=0.20),
        _candidate("c", "dense", rank=97, score=0.30),
    ]
    sparse = [
        _candidate("b", "sparse", rank=8, score=12.0),
        _candidate("a", "sparse", rank=7, score=8.0),
        _candidate("d", "sparse", rank=6, score=5.0),
    ]

    results = RRFFusion(k=60).fuse([dense, sparse], top_k=3)

    # a、b 的 RRF 分数相同，按输入中首次出现的位置稳定选择 a 在前。
    assert [result.chunk_id for result in results] == ["a", "b", "c"]
    assert [result.rank for result in results] == [1, 2, 3]
    assert all(result.source == "fusion" for result in results)
    assert results[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert results[0].debug == {
        "original": "dense-a",
        "rrf": {
            "k": 60,
            "score": pytest.approx(1 / 61 + 1 / 62),
            "sources": [
                {"list_index": 0, "source": "dense", "rank": 1, "score": 0.01},
                {"list_index": 1, "source": "sparse", "rank": 2, "score": 8.0},
            ],
        },
    }


def test_fuse_is_deterministic_and_ignores_stale_candidate_rank() -> None:
    ranked_lists = [
        [
            _candidate("b", "dense", rank=1, score=0.1),
            _candidate("a", "dense", rank=1, score=0.2),
        ]
    ]
    fusion = RRFFusion(k=20)

    first = fusion.fuse(ranked_lists, top_k=2)
    second = fusion.fuse(ranked_lists, top_k=2)

    assert first == second
    assert [result.chunk_id for result in first] == ["b", "a"]
    assert [result.rank for result in first] == [1, 2]


def test_fuse_counts_a_chunk_at_most_once_per_ranked_list() -> None:
    duplicate = _candidate("same", "dense", rank=2, score=0.2)
    results = RRFFusion().fuse(
        [
            [_candidate("same", "dense", rank=1, score=0.1), duplicate],
            [_candidate("other", "sparse", rank=1, score=5.0)],
        ],
        top_k=2,
    )

    assert [result.chunk_id for result in results] == ["same", "other"]
    assert results[0].score == pytest.approx(1 / 61)
    assert len(results[0].debug["rrf"]["sources"]) == 1


def test_fuse_handles_empty_inputs_and_validates_top_k() -> None:
    fusion = RRFFusion()

    assert fusion.fuse([], top_k=3) == []
    assert fusion.fuse([[]], top_k=3) == []
    with pytest.raises(ValueError, match="top_k must be positive"):
        fusion.fuse([], top_k=0)


def test_fusion_constructor_and_port_contract() -> None:
    with pytest.raises(ValueError, match="k must be positive"):
        RRFFusion(k=0)
    assert isinstance(RRFFusion(), FusionStrategy)


def _candidate(
    chunk_id: str,
    source: Literal["dense", "sparse"],
    *,
    rank: int,
    score: float,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        text=f"text-{chunk_id}",
        metadata={"collection": "docs"},
        score=score,
        source=source,
        rank=rank,
        debug={"original": f"{source}-{chunk_id}"},
    )
