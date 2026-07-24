"""Tests for B7.7: LLM Reranker (mock LLM)."""

from __future__ import annotations

from typing import Any

import pytest

from src.core.settings import RerankConfig
from src.core.types import RetrievalCandidate
from src.libs.reranker.llm_reranker import LLMReranker, RerankerFallback
from src.ports.llm import BaseLLM, ChatResponse, Message


class _MockLLM(BaseLLM):
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[str] = []

    def chat(self, messages, trace=None, **kwargs):  # type: ignore[override]
        self.calls.append(messages[-1].content)
        reply = self.replies.pop(0) if self.replies else "[]"
        return ChatResponse(content=reply, model="mock")


def _cands(*ids: str) -> list[RetrievalCandidate]:
    return [
        RetrievalCandidate(
            chunk_id=cid,
            text=f"text {cid}",
            metadata={},
            score=1.0 - i * 0.1,
            source="fusion",
            rank=i,
        )
        for i, cid in enumerate(ids)
    ]


def test_llm_reranker_reorders_per_response() -> None:
    llm = _MockLLM(
        [
            '[{"id": "b", "reason": "more on-topic"}, '
            '{"id": "a", "reason": "less relevant"}]'
        ]
    )
    reranker = LLMReranker(RerankConfig(backend="llm"), llm=llm, max_candidates=10)
    out = reranker.rerank("query", _cands("a", "b"), top_k=2)
    assert [c.chunk_id for c in out] == ["b", "a"]


def test_llm_reranker_truncates_to_top_k() -> None:
    llm = _MockLLM(['[{"id": "c"}, {"id": "a"}, {"id": "b"}]'])
    reranker = LLMReranker(RerankConfig(backend="llm"), llm=llm)
    out = reranker.rerank("query", _cands("a", "b", "c", "d"), top_k=2)
    assert [c.chunk_id for c in out] == ["c", "a"]


def test_llm_reranker_appends_unranked_at_end() -> None:
    llm = _MockLLM(['[{"id": "b"}]'])
    reranker = LLMReranker(RerankConfig(backend="llm"), llm=llm)
    out = reranker.rerank("query", _cands("a", "b", "c"), top_k=3)
    assert [c.chunk_id for c in out] == ["b", "a", "c"]


def test_llm_reranker_tolerates_prose_around_json() -> None:
    llm = _MockLLM(['Sure! Here you go:\n[{"id":"a"}]\nCheers.'])
    reranker = LLMReranker(RerankConfig(backend="llm"), llm=llm)
    out = reranker.rerank("q", _cands("a", "b"), top_k=1)
    assert out[0].chunk_id == "a"


def test_llm_reranker_falls_back_on_invalid_json() -> None:
    llm = _MockLLM(["not json at all"])
    reranker = LLMReranker(RerankConfig(backend="llm"), llm=llm)
    with pytest.raises(RerankerFallback, match="no JSON array"):
        reranker.rerank("q", _cands("a"), top_k=1)


def test_llm_reranker_falls_back_on_missing_id_field() -> None:
    llm = _MockLLM(['[{"reason": "no id here"}]'])
    reranker = LLMReranker(RerankConfig(backend="llm"), llm=llm)
    with pytest.raises(RerankerFallback, match="must be an object with id"):
        reranker.rerank("q", _cands("a"), top_k=1)


def test_llm_reranker_falls_back_on_duplicate_ids() -> None:
    llm = _MockLLM(['[{"id":"a"},{"id":"a"}]'])
    reranker = LLMReranker(RerankConfig(backend="llm"), llm=llm)
    with pytest.raises(RerankerFallback, match="duplicate"):
        reranker.rerank("q", _cands("a"), top_k=1)


def test_llm_reranker_falls_back_when_chat_raises() -> None:
    class BoomLLM(BaseLLM):
        def chat(self, messages, trace=None, **kwargs):  # type: ignore[override]
            raise RuntimeError("network down")

    with pytest.raises(RerankerFallback, match="chat failed"):
        LLMReranker(RerankConfig(backend="llm"), llm=BoomLLM()).rerank(
            "q", _cands("a"), top_k=1
        )


def test_llm_reranker_empty_candidates_returns_empty() -> None:
    reranker = LLMReranker(RerankConfig(backend="llm"), llm=_MockLLM([]))
    assert reranker.rerank("q", [], top_k=5) == []


def test_llm_reranker_invalid_top_k_raises() -> None:
    reranker = LLMReranker(RerankConfig(backend="llm"), llm=_MockLLM([]))
    with pytest.raises(ValueError, match="top_k"):
        reranker.rerank("q", _cands("a"), top_k=0)


def test_llm_reranker_uses_injected_template() -> None:
    captured: dict[str, Any] = {}

    class CaptureLLM(BaseLLM):
        def chat(self, messages, trace=None, **kwargs):  # type: ignore[override]
            captured["prompt"] = messages[-1].content
            return ChatResponse(content='[{"id":"a"}]', model="mock")

    reranker = LLMReranker(
        RerankConfig(backend="llm"),
        llm=CaptureLLM(),
        prompt_template="Q={QUERY};C={CANDIDATES}",
    )
    reranker.rerank("foo", _cands("a"), top_k=1)
    assert captured["prompt"] == "Q=foo;C=[0] id=a text='text a'"