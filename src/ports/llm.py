"""LLM and vision LLM ports kept separate from response contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class ChatResponse:
    content: str
    model: str
    usage: dict[str, int] | None = None
    raw_response: Any | None = None


@runtime_checkable
class BaseLLM(Protocol):
    def chat(
        self,
        messages: list[Message],
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class ImageInput:
    path: str | Path | None = None
    data: bytes | None = None
    base64: str | None = None
    mime_type: str = "image/png"

    def __post_init__(self) -> None:
        provided = sum(value is not None for value in (self.path, self.data, self.base64))
        if provided != 1:
            raise ValueError("provide exactly one of path, data, or base64")


@runtime_checkable
class BaseVisionLLM(Protocol):
    def chat_with_image(
        self,
        text: str,
        image: ImageInput,
        messages: list[Message] | None = None,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse: ...
