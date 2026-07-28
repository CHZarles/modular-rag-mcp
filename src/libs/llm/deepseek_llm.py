"""DeepSeek 的 OpenAI-compatible LLM 适配器。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.libs.llm.openai_llm import OpenAICompatibleLLM


class DeepSeekLLM(OpenAICompatibleLLM):
    """DeepSeek Chat Completions 客户端。"""

    provider = "deepseek"
    default_base_url = "https://api.deepseek.com/v1"

    def __init__(self, config: Mapping[str, Any]) -> None:
        super().__init__(config, provider=self.provider)


__all__ = ["DeepSeekLLM"]
