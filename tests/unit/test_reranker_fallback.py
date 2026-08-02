"""Reranker 后端编排、超时和 Fusion 顺序回退测试。"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import replace
from threading import Event

import pytest

from src.core.query_engine.reranker import FallbackReranker, NoneReranker
from src.core.trace import TraceContext
from src.core.types import RetrievalCandidate
from src.libs.reranker import RerankerFactory, create_reranker
from src.ports.query import BaseReranker


class RecordingReranker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], int, object | None]] = []

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        self.calls.append((query, [item.chunk_id for item in candidates], top_k, trace))
        return [
            replace(item, source="rerank", rank=rank)
            for rank, item in enumerate(reversed(candidates), start=1)
        ][:top_k]


class FailingReranker:
    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        raise RuntimeError("backend unavailable")


class BlockingReranker:
    def __init__(self) -> None:
        self.release = Event()

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        self.release.wait(timeout=1)
        return candidates[:top_k]


@pytest.fixture(autouse=True)
def cleanup_fake_backend() -> Iterator[None]:
    RerankerFactory.unregister("recording")
    yield
    RerankerFactory.unregister("recording")


def test_reranker_limits_backend_input_and_returns_backend_order() -> None:
    backend = RecordingReranker()
    trace = object()
    reranker = FallbackReranker(
        backend,
        backend_name="recording",
        top_m=2,
        timeout_seconds=1,
    )

    results = reranker.rerank(
        "lease state",
        [_candidate("a", 1), _candidate("b", 2), _candidate("c", 3)],
        top_k=1,
        trace=trace,
    )

    assert backend.calls == [("lease state", ["a", "b"], 1, trace)]
    assert [item.chunk_id for item in results] == ["b"]
    assert results[0].source == "rerank"
    assert "rerank" not in results[0].debug
    assert isinstance(reranker, BaseReranker)


def test_reranker_falls_back_to_fusion_order_and_marks_reason() -> None:
    candidates = [_candidate("a", 1), _candidate("b", 2), _candidate("c", 3)]
    reranker = FallbackReranker(FailingReranker(), backend_name="failing")
    trace = TraceContext()

    results = reranker.rerank("lease", candidates, top_k=2, trace=trace)

    assert [item.chunk_id for item in results] == ["a", "b"]
    assert all(item.source == "fusion" for item in results)
    assert results[0].debug == {
        "rrf": {"score": 0.1},
        "rerank": {
            "backend": "failing",
            "fallback": True,
            "reason": "backend unavailable",
        },
    }
    assert "rerank" not in candidates[0].debug
    stage = trace.stages[0]
    assert stage["stage"] == "rerank"
    assert stage["elapsed_ms"] >= 0
    assert stage["data"]["method"] == "failing"
    assert stage["data"]["provider"] == "FailingReranker"
    details = stage["data"]["details"]
    assert {key: details[key] for key in ("status", "input_count", "output_count")} == {
        "status": "fallback",
        "input_count": 3,
        "output_count": 2,
    }
    assert [item["chunk_id"] for item in details["input_candidates"]] == ["a", "b", "c"]
    assert [item["chunk_id"] for item in details["output_candidates"]] == ["a", "b"]


def test_reranker_timeout_returns_without_waiting_for_blocked_backend() -> None:
    backend = BlockingReranker()
    reranker = FallbackReranker(
        backend,
        backend_name="blocking",
        timeout_seconds=0.02,
    )

    started = time.monotonic()
    try:
        results = reranker.rerank("lease", [_candidate("a", 1)], top_k=1)
    finally:
        backend.release.set()

    assert time.monotonic() - started < 0.5
    assert results[0].debug["rerank"] == {
        "backend": "blocking",
        "fallback": True,
        "reason": "timeout after 0.02 seconds",
    }


def test_reranker_skips_backend_for_empty_candidates_and_validates_top_k() -> None:
    backend = RecordingReranker()
    reranker = FallbackReranker(backend, backend_name="recording")

    assert reranker.rerank("lease", [], top_k=3) == []
    assert backend.calls == []
    with pytest.raises(ValueError, match="top_k must be positive"):
        reranker.rerank("lease", [_candidate("a", 1)], top_k=0)
    with pytest.raises(ValueError, match="top_k must be positive"):
        NoneReranker().rerank("lease", [_candidate("a", 1)], top_k=0)


@pytest.mark.parametrize(
    ("top_m", "timeout_seconds"),
    [(0, None), (None, 0), (-1, 1), (1, -1)],
)
def test_reranker_rejects_non_positive_limits(
    top_m: int | None,
    timeout_seconds: float | None,
) -> None:
    with pytest.raises(ValueError):
        FallbackReranker(
            RecordingReranker(),
            backend_name="recording",
            top_m=top_m,
            timeout_seconds=timeout_seconds,
        )


def test_factory_wraps_registered_backend_with_fallback_policy() -> None:
    backend = RecordingReranker()
    RerankerFactory.register("recording", lambda config: backend)

    reranker = create_reranker(
        {
            "backend": "recording",
            "top_m": 2,
            "timeout_seconds": 1,
        }
    )
    results = reranker.rerank(
        "lease",
        [_candidate("a", 1), _candidate("b", 2), _candidate("c", 3)],
        top_k=1,
    )

    assert isinstance(reranker, FallbackReranker)
    assert [item.chunk_id for item in results] == ["b"]
    assert backend.calls[0][1] == ["a", "b"]


@pytest.mark.parametrize(
    "config",
    [
        {"backend": "none", "top_m": 0},
        {"backend": "none", "top_m": True},
        {"backend": "none", "timeout_seconds": 0},
        {"backend": "none", "timeout_seconds": False},
    ],
)
def test_factory_rejects_invalid_optional_limits(config: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="must be a positive"):
        create_reranker(config)


def _candidate(chunk_id: str, rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        text=f"text-{chunk_id}",
        metadata={"collection": "docs"},
        score=1.0 / rank,
        source="fusion",
        rank=rank,
        debug={"rrf": {"score": 0.1}},
    )
