from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.query_engine import FallbackReranker, NoneReranker
from core.settings import Settings
from core.types import RetrievalCandidate
from libs.reranker import BaseReranker, RerankerFactory, create_reranker


class ReverseReranker:
    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        return list(reversed(candidates))[:top_k]


@pytest.fixture(autouse=True)
def cleanup_fake_backend() -> Iterator[None]:
    RerankerFactory.unregister("reverse")
    yield
    RerankerFactory.unregister("reverse")


def candidate(chunk_id: str, rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        text=f"text {chunk_id}",
        metadata={},
        score=1.0 / rank,
        source="fusion",
        rank=rank,
    )


def test_reranker_factory_none_keeps_order_and_applies_top_k() -> None:
    settings = Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "openai"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "chroma"},
        retrieval={"sparse_backend": "bm25"},
        rerank={"backend": "none"},
        evaluation={"backends": ["custom"]},
        observability={"enabled": True},
    )
    reranker = RerankerFactory.create(settings)
    candidates = [candidate("a", 1), candidate("b", 2), candidate("c", 3)]

    result = reranker.rerank("query", candidates, top_k=2)

    assert isinstance(reranker, BaseReranker)
    assert isinstance(reranker, NoneReranker)
    assert [item.chunk_id for item in result] == ["a", "b"]


def test_reranker_factory_routes_custom_backend() -> None:
    RerankerFactory.register("reverse", lambda config: ReverseReranker())
    candidates = [candidate("a", 1), candidate("b", 2)]

    reranker = create_reranker({"backend": "reverse"})

    assert isinstance(reranker, FallbackReranker)
    assert [item.chunk_id for item in reranker.rerank("query", candidates, top_k=2)] == ["b", "a"]


def test_reranker_factory_names_unknown_backend() -> None:
    with pytest.raises(ValueError, match="Unsupported Reranker backend: missing"):
        RerankerFactory.create({"rerank": {"backend": "missing"}})


def test_reranker_factory_requires_backend() -> None:
    with pytest.raises(ValueError, match=r"rerank\.backend"):
        RerankerFactory.create({"rerank": {}})


@pytest.mark.parametrize(
    "config",
    [
        {"backend": "none", "top_m": 0},
        {"backend": "none", "top_m": True},
        {"backend": "none", "timeout_seconds": 0},
        {"backend": "none", "timeout_seconds": False},
        {"backend": "none", "timeout_seconds": "1"},
    ],
)
def test_reranker_factory_rejects_invalid_policy_limits(config: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="must be a positive"):
        create_reranker(config)


def test_reranker_factory_passes_top_level_llm_to_backend_without_mutating_settings() -> None:
    received: list[dict[str, object]] = []

    def build(config: object) -> ReverseReranker:
        assert isinstance(config, dict)
        received.append(config)
        return ReverseReranker()

    RerankerFactory.register("reverse", build)
    settings = {
        "llm": {"provider": "openai", "model": "rank-model"},
        "rerank": {"backend": "reverse", "top_m": 3},
    }

    reranker = create_reranker(settings)

    assert isinstance(reranker, FallbackReranker)
    assert received == [
        {
            "backend": "reverse",
            "top_m": 3,
            "llm": {"provider": "openai", "model": "rank-model"},
        }
    ]
    assert settings["rerank"] == {"backend": "reverse", "top_m": 3}


def test_reranker_factory_rejects_empty_registered_name() -> None:
    with pytest.raises(ValueError, match="backend name must not be empty"):
        RerankerFactory.register(" ", lambda config: ReverseReranker())
