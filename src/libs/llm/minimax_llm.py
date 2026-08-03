"""MiniMax 的 OpenAI-compatible LLM 适配器。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.libs.llm.openai_llm import OpenAICompatibleLLM


class MiniMaxLLM(OpenAICompatibleLLM):
    """MiniMax Chat Completions 客户端。"""

    provider = "minimax"
    default_base_url = "https://api.minimax.io/v1"

    def __init__(self, config: Mapping[str, Any]) -> None:
        super().__init__(config, provider=self.provider)


__all__ = ["MiniMaxLLM"]
