"""使用文本 LLM 对检索候选项重新排序。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.core.types import RetrievalCandidate
from src.libs.llm import BaseLLM, Message, create_llm


class LLMReranker:
    """要求 LLM 返回完整且严格的候选 ID 排名。"""

    default_prompt_path = Path("config/prompts/rerank.txt")

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        llm: BaseLLM | None = None,
        prompt_template: str | None = None,
    ) -> None:
        self.config = config or {}
        self.llm = llm or _create_llm(self.config)
        if prompt_template is not None:
            self.prompt_template = prompt_template.strip()
            if not self.prompt_template:
                raise ValueError("llm reranker prompt error: prompt_template must not be empty")
        else:
            self.prompt_template = _load_prompt(self.config, self.default_prompt_path)

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]:
        if top_k <= 0:
            raise ValueError("llm reranker top_k must be positive")
        if not candidates:
            return []

        candidate_ids = _candidate_ids(candidates)
        messages = self._messages(query, candidates)
        response = self.llm.chat(messages, trace=trace)
        ranked_ids = _parse_ranked_ids(response.content, candidate_ids)
        by_id = {candidate.chunk_id: candidate for candidate in candidates}
        return [
            replace(by_id[chunk_id], source="rerank", rank=rank)
            for rank, chunk_id in enumerate(ranked_ids[:top_k], start=1)
        ]

    def _messages(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
    ) -> list[Message]:
        system_prompt = (
            f"{self.prompt_template.rstrip()}\n\n"
            "Return only one JSON object with exactly this schema: "
            '{"ranked_ids":["candidate-id"]}. '
            "Include every supplied candidate ID exactly once, ordered from most to least relevant."
        )
        payload = {
            "query": query,
            "candidates": [
                {"id": candidate.chunk_id, "text": candidate.text}
                for candidate in candidates
            ],
        }
        return [
            Message(role="system", content=system_prompt),
            Message(role="user", content=json.dumps(payload, ensure_ascii=False)),
        ]


def _create_llm(config: Mapping[str, Any]) -> BaseLLM:
    llm_config = config.get("llm")
    if not isinstance(llm_config, Mapping):
        raise ValueError("llm reranker configuration error: missing llm settings")
    return create_llm(llm_config)


def _load_prompt(config: Mapping[str, Any], default_path: Path) -> str:
    inline = config.get("prompt_template")
    if inline is not None:
        prompt = str(inline).strip()
        if not prompt:
            raise ValueError("llm reranker prompt error: prompt_template must not be empty")
        return prompt

    path = Path(str(config.get("prompt_path") or default_path))
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"llm reranker prompt error: cannot read {path}") from exc
    if not prompt:
        raise ValueError(f"llm reranker prompt error: {path} is empty")
    return prompt


def _candidate_ids(candidates: list[RetrievalCandidate]) -> list[str]:
    candidate_ids = [candidate.chunk_id for candidate in candidates]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("llm reranker input error: candidate IDs must be unique")
    return candidate_ids


def _parse_ranked_ids(content: str, expected_ids: list[str]) -> list[str]:
    if not isinstance(content, str):
        raise ValueError("llm reranker response error: content must be a string")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("llm reranker response error: expected a JSON object") from exc
    if not isinstance(payload, dict) or set(payload) != {"ranked_ids"}:
        raise ValueError("llm reranker response error: expected only ranked_ids")

    ranked_ids = payload["ranked_ids"]
    if not isinstance(ranked_ids, list) or any(not isinstance(item, str) for item in ranked_ids):
        raise ValueError("llm reranker response error: ranked_ids must be a string list")

    if len(ranked_ids) != len(set(ranked_ids)):
        raise ValueError("llm reranker response error: ranked_ids contains duplicates")
    if len(ranked_ids) != len(expected_ids) or set(ranked_ids) != set(expected_ids):
        raise ValueError("llm reranker response error: ranked_ids must match all candidate IDs")
    return ranked_ids


__all__ = ["LLMReranker"]
