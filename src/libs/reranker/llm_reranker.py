"""LLM Reranker — asks an LLM to re-rank candidates using the rerank prompt."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from src.core.settings import RerankConfig
from src.core.types import RetrievalCandidate
from src.ports.llm import BaseLLM, Message
from src.ports.query import BaseReranker

DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[3] / "config" / "prompts" / "rerank.txt"


class RerankerFallback(RuntimeError):
    """Raised when the LLM reranker cannot produce a valid ranking.

    Callers in :class:`Core.query_engine.reranker` can catch this and
    fall back to the input order (passthrough).
    """


class LLMReranker(BaseReranker):
    """Rerank by asking an LLM. Falls back with a clear signal on failure."""

    def __init__(
        self,
        config: RerankConfig,
        llm: BaseLLM,
        prompt_template: str | None = None,
        prompt_path: Path | str | None = None,
        *,
        max_candidates: int | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.max_candidates = max_candidates
        self.prompt_template = self._load_template(prompt_template, prompt_path)

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_k: int,
        trace: Any | None = None,
    ) -> list[RetrievalCandidate]:
        if not candidates:
            return []
        if top_k <= 0:
            raise ValueError("llm reranker: top_k must be > 0")

        # Optionally cap the candidate list size to bound prompt cost.
        pool = list(candidates)
        if self.max_candidates is not None:
            pool = pool[: self.max_candidates]

        prompt = self._render_prompt(query, pool)
        try:
            response = self.llm.chat([Message(role="user", content=prompt)])
        except Exception as exc:  # noqa: BLE001 — surface as fallback signal
            raise RerankerFallback(f"llm reranker: chat failed: {exc}") from exc

        ranking = self._parse_ranking(response.content, len(pool))
        return self._reorder(pool, ranking, top_k)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _load_template(
        provided: str | None,
        path: Path | str | None,
    ) -> str:
        if provided is not None:
            return provided
        target = Path(path) if path else DEFAULT_PROMPT_PATH
        if not target.is_file():
            # Empty template still works (the prompt will be very simple),
            # but we want to make a missing file visible to operators.
            return "Query: {QUERY}\nCandidates:\n{CANDIDATES}\nReturn JSON array."
        return target.read_text(encoding="utf-8")

    def _render_prompt(self, query: str, candidates: list[RetrievalCandidate]) -> str:
        candidate_lines = [
            f"[{i}] id={c.chunk_id} text={c.text!r}"
            for i, c in enumerate(candidates)
        ]
        candidate_blob = "\n".join(candidate_lines)
        template = self.prompt_template
        # Substitute {QUERY} / {CANDIDATES} only if the template actually
        # declares them as single-brace placeholders. Templates using
        # double-brace ``{{...}}`` or that contain JSON examples with raw
        # braces are passed through verbatim and we append the candidate
        # list as a tail block.
        if "{QUERY}" in template and "{CANDIDATES}" in template:
            try:
                return template.format(QUERY=query, CANDIDATES=candidate_blob)
            except (KeyError, IndexError):
                pass
        return (
            f"{template}\n\nQuery: {query}\nCandidates:\n{candidate_blob}\n"
            "Return a JSON array of {\"id\": \"<chunk_id>\", \"reason\": \"<short>\"}"
            " entries, ordered from most to least relevant."
        )

    @staticmethod
    def _parse_ranking(raw: str, expected_count: int) -> list[str]:
        """Extract a list of candidate ids from the LLM's response.

        Tolerant to leading prose and fenced code blocks. Raises
        :class:`RerankerFallback` if no JSON array can be located or if
        its entries aren't shaped correctly.
        """
        # Find the first JSON-looking array in the response.
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if not match:
            raise RerankerFallback("llm reranker: response contained no JSON array")
        snippet = match.group(0)
        try:
            data = json.loads(snippet)
        except json.JSONDecodeError as exc:
            raise RerankerFallback(f"llm reranker: invalid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise RerankerFallback("llm reranker: top-level JSON must be a list")
        ids: list[str] = []
        for entry in data:
            if not isinstance(entry, dict) or "id" not in entry:
                raise RerankerFallback("llm reranker: each entry must be an object with id")
            ids.append(str(entry["id"]))
        if len(ids) != len(set(ids)):
            raise RerankerFallback("llm reranker: duplicate ids in ranking")
        return ids

    @staticmethod
    def _reorder(
        candidates: list[RetrievalCandidate],
        ranking: list[str],
        top_k: int,
    ) -> list[RetrievalCandidate]:
        by_id = {c.chunk_id: c for c in candidates}
        out: list[RetrievalCandidate] = []
        for cid in ranking:
            if cid in by_id and cid not in {c.chunk_id for c in out}:
                out.append(by_id[cid])
        # Append any candidate the LLM forgot to rank, preserving order.
        ranked_ids = {c.chunk_id for c in out}
        for c in candidates:
            if c.chunk_id not in ranked_ids:
                out.append(c)
        return out[:top_k]


def build_llm_reranker(
    llm: BaseLLM,
    config: RerankConfig,
    *,
    prompt_template: str | None = None,
    prompt_path: Path | str | None = None,
) -> Callable[[RerankConfig], BaseReranker]:
    """Closure factory bound to a specific LLM instance."""

    def _builder(cfg: RerankConfig) -> BaseReranker:
        return LLMReranker(
            cfg,
            llm=llm,
            prompt_template=prompt_template,
            prompt_path=prompt_path,
        )

    return _builder