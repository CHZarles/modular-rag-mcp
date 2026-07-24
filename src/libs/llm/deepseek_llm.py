"""DeepSeek LLM provider — uses the OpenAI-compatible Chat Completions API.

DeepSeek's public endpoint is OpenAI-format, so this is a thin wrapper
around :class:`OpenAILLM` that defaults to DeepSeek's base URL.
"""

from __future__ import annotations

from src.core.settings import LLMConfig
from src.libs.llm._http import Transport
from src.libs.llm.openai_llm import OpenAILLM
from src.ports.llm import BaseLLM

_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
_DEFAULT_MODEL = "deepseek-chat"


class DeepSeekLLM(OpenAILLM):
    """DeepSeek Chat Completions client."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: Transport | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(
            config,
            base_url=base_url or _DEFAULT_BASE_URL,
            transport=transport,
        )
        # Override model default if not set in config.
        if not self.config.model:
            object.__setattr__(self.config, "model", _DEFAULT_MODEL)