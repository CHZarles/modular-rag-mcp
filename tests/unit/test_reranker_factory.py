"""Tests for B5: reranker factory routing + None fallback."""

from __future__ import annotations

import pytest

from src.core.query_engine.reranker import NoneReranker
from src.core.settings import RerankConfig
from src.core.types import RetrievalCandidate
from src.libs.reranker.reranker_factory import (
    RerankerFactory,
    RerankerFactoryError,
    register_default_rerankers,
)
from src.ports.query import BaseReranker


class FakeCrossEncoderReranker(BaseReranker):
    def __init__(self, config: RerankConfig) -> None:
        self.config = config
        self.calls = 0

    def rerank(self, query, candidates, top_k, trace=None):  # type: ignore[override]
        self.calls += 1
        # Reverse the order as a stand-in for "scoring and re-sorting".
        return list(reversed(candidates))[:top_k]


@pytest.fixture(autouse=True)
def _isolate_registry():
    snapshot = dict(RerankerFactory._registry)
    RerankerFactory.reset()
    yield
    RerankerFactory._registry.clear()
    RerankerFactory._registry.update(snapshot)


def test_none_backend_returns_none_reranker() -> None:
    """The ``none`` backend must produce a NoneReranker that preserves order."""
    register_default_rerankers()
    reranker = RerankerFactory.create(RerankConfig(backend="none"))
    assert isinstance(reranker, NoneReranker)
    assert isinstance(reranker, BaseReranker)


def test_none_reranker_is_passthrough() -> None:
    """NoneReranker.rerank must keep the input order up to top_k."""
    register_default_rerankers()
    reranker = RerankerFactory.create(RerankConfig(backend="none"))
    candidates = [
        RetrievalCandidate(
            chunk_id=f"c{i}",
            text=str(i),
            metadata={},
            score=1.0 / (i + 1),
            source="fusion",
            rank=i,
        )
        for i in range(5)
    ]
    out = reranker.rerank("query", candidates, top_k=3)
    assert [c.chunk_id for c in out] == ["c0", "c1", "c2"]


def test_cross_encoder_backend_via_custom_registration() -> None:
    """A custom backend (cross_encoder) should be reachable through the factory."""
    RerankerFactory.register("cross_encoder", lambda c: FakeCrossEncoderReranker(c))
    reranker = RerankerFactory.create(RerankConfig(backend="cross_encoder", model="ce"))
    assert isinstance(reranker, FakeCrossEncoderReranker)
    assert reranker.config.model == "ce"


def test_cross_encoder_rerank_reverses_order() -> None:
    RerankerFactory.register("cross_encoder", lambda c: FakeCrossEncoderReranker(c))
    reranker = RerankerFactory.create(RerankConfig(backend="cross_encoder"))
    candidates = [
        RetrievalCandidate(
            chunk_id="a", text="A", metadata={}, score=1.0, source="fusion", rank=0
        ),
        RetrievalCandidate(
            chunk_id="b", text="B", metadata={}, score=0.5, source="fusion", rank=1
        ),
    ]
    out = reranker.rerank("query", candidates, top_k=2)
    assert [c.chunk_id for c in out] == ["b", "a"]


def test_create_unknown_backend_raises_with_registered_hint() -> None:
    RerankerFactory.reset()
    with pytest.raises(RerankerFactoryError) as ei:
        RerankerFactory.create(RerankConfig(backend="llm"))
    msg = str(ei.value)
    assert "llm" in msg
    assert "registered=" in msg


def test_register_default_rerankers_is_idempotent() -> None:
    """Calling register_default_rerankers twice must not error."""
    register_default_rerankers()
    register_default_rerankers()
    assert "none" in RerankerFactory.registered_backends()


def test_register_rejects_empty_backend_name() -> None:
    RerankerFactory.reset()
    with pytest.raises(RerankerFactoryError, match="non-empty"):
        RerankerFactory.register("", lambda c: NoneReranker())


def test_register_rejects_non_callable_builder() -> None:
    RerankerFactory.reset()
    with pytest.raises(RerankerFactoryError, match="callable"):
        RerankerFactory.register("oops", 42)  # type: ignore[arg-type]


def test_reset_clears_registry_and_unregisters_defaults() -> None:
    register_default_rerankers()
    assert "none" in RerankerFactory.registered_backends()
    RerankerFactory.reset()
    assert RerankerFactory.registered_backends() == []
    # After reset, "none" must need to be re-registered.
    with pytest.raises(RerankerFactoryError):
        RerankerFactory.create(RerankConfig(backend="none"))