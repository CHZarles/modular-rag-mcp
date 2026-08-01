from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from core.types import RetrievalCandidate
from libs.llm import ChatResponse, LLMFactory, Message
from libs.reranker import LLMReranker, RerankerFactory
from src.core.query_engine.reranker import FallbackReranker


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[tuple[list[Message], object | None, dict[str, Any]]] = []

    def chat(
        self,
        messages: list[Message],
        trace: object | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append((messages, trace, kwargs))
        return ChatResponse(content=self.content, model="fake-reranker")


class FailingLLM(FakeLLM):
    def chat(
        self,
        messages: list[Message],
        trace: object | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        raise RuntimeError("model unavailable")


@pytest.fixture(autouse=True)
def cleanup_fake_llm() -> Iterator[None]:
    LLMFactory.unregister("rerank-test")
    yield
    LLMFactory.unregister("rerank-test")


def candidate(chunk_id: str, rank: int) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        text=f"content {chunk_id}",
        metadata={"collection": "docs"},
        score=1.0 / rank,
        source="fusion",
        rank=rank,
        debug={"rrf": {"rank": rank}},
    )


def test_llm_reranker_builds_prompt_and_returns_ranked_candidates() -> None:
    llm = FakeLLM(json.dumps({"ranked_ids": ["b", "a", "c"]}))
    trace = object()
    reranker = LLMReranker(llm=llm, prompt_template="Rank by direct relevance.")

    results = reranker.rerank(
        "lease ownership",
        [candidate("a", 1), candidate("b", 2), candidate("c", 3)],
        top_k=2,
        trace=trace,
    )

    assert [item.chunk_id for item in results] == ["b", "a"]
    assert [item.rank for item in results] == [1, 2]
    assert all(item.source == "rerank" for item in results)
    assert results[0].score == 0.5
    assert results[0].debug == {"rrf": {"rank": 2}}
    messages, received_trace, kwargs = llm.calls[0]
    assert received_trace is trace
    assert kwargs == {}
    assert messages[0].role == "system"
    assert "Rank by direct relevance." in messages[0].content
    assert "every supplied candidate ID exactly once" in messages[0].content
    assert json.loads(messages[1].content) == {
        "query": "lease ownership",
        "candidates": [
            {"id": "a", "text": "content a"},
            {"id": "b", "text": "content b"},
            {"id": "c", "text": "content c"},
        ],
    }


def test_llm_reranker_reads_prompt_file(tmp_path: Path) -> None:
    prompt_path = tmp_path / "rerank.txt"
    prompt_path.write_text("Use the project ranking policy.", encoding="utf-8")
    llm = FakeLLM('{"ranked_ids":["a"]}')

    reranker = LLMReranker({"prompt_path": str(prompt_path)}, llm=llm)

    assert [item.chunk_id for item in reranker.rerank("query", [candidate("a", 1)], 1)] == [
        "a"
    ]
    assert "Use the project ranking policy." in llm.calls[0][0][0].content


def test_factory_creates_llm_backend_from_top_level_llm_config() -> None:
    llm = FakeLLM('{"ranked_ids":["b","a"]}')
    LLMFactory.register("rerank-test", lambda config: llm)

    reranker = RerankerFactory.create(
        {
            "llm": {"provider": "rerank-test"},
            "rerank": {
                "backend": "llm",
                "prompt_template": "Injected factory prompt.",
                "top_m": 2,
            },
        }
    )
    results = reranker.rerank("query", [candidate("a", 1), candidate("b", 2)], top_k=1)

    assert isinstance(reranker, FallbackReranker)
    assert isinstance(reranker.backend, LLMReranker)
    assert [item.chunk_id for item in results] == ["b"]


def test_factory_falls_back_when_llm_call_fails() -> None:
    LLMFactory.register("rerank-test", lambda config: FailingLLM(""))
    reranker = RerankerFactory.create(
        {
            "llm": {"provider": "rerank-test"},
            "rerank": {"backend": "llm", "prompt_template": "Rank."},
        }
    )

    results = reranker.rerank("query", [candidate("a", 1), candidate("b", 2)], top_k=1)

    assert [item.chunk_id for item in results] == ["a"]
    assert results[0].debug["rerank"] == {
        "backend": "llm",
        "fallback": True,
        "reason": "model unavailable",
    }


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "content must be a string"),
        ("not json", "expected a JSON object"),
        ('["a","b"]', "expected only ranked_ids"),
        ('{"ranked_ids":["a","b"],"scores":[]}', "expected only ranked_ids"),
        ('{"ranked_ids":"a"}', "string list"),
        ('{"ranked_ids":["a","a"]}', "contains duplicates"),
        ('{"ranked_ids":["a"]}', "match all candidate IDs"),
        ('{"ranked_ids":["a","missing"]}', "match all candidate IDs"),
    ],
)
def test_llm_reranker_rejects_invalid_structured_output(content: Any, message: str) -> None:
    reranker = LLMReranker(llm=FakeLLM(content), prompt_template="Rank.")

    with pytest.raises(ValueError, match=message):
        reranker.rerank("query", [candidate("a", 1), candidate("b", 2)], top_k=1)


def test_llm_reranker_validates_input_without_calling_llm() -> None:
    llm = FakeLLM('{"ranked_ids":[]}')
    reranker = LLMReranker(llm=llm, prompt_template="Rank.")

    assert reranker.rerank("query", [], top_k=1) == []
    assert llm.calls == []
    with pytest.raises(ValueError, match="top_k must be positive"):
        reranker.rerank("query", [candidate("a", 1)], top_k=0)
    with pytest.raises(ValueError, match="candidate IDs must be unique"):
        reranker.rerank("query", [candidate("a", 1), candidate("a", 2)], top_k=1)
