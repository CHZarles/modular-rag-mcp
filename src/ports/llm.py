"""独立于响应契约的 LLM 与视觉 LLM 端口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class Message:
    """发送给 LLM 的单条角色消息。"""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class ChatResponse:
    """统一的文本模型响应及用量信息。"""

    content: str
    model: str
    usage: dict[str, int] | None = None
    raw_response: Any | None = None


@runtime_checkable
class BaseLLM(Protocol):
    """文本对话模型的最小端口。"""

    def chat(
        self,
        messages: list[Message],
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse: ...


@dataclass(frozen=True)
class ImageInput:
    """视觉模型输入；路径、字节或 Base64 三者必须且只能提供一种。"""

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
    """接受文本与单张图片的视觉模型端口。"""

    def chat_with_image(
        self,
        text: str,
        image: ImageInput,
        messages: list[Message] | None = None,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse: ...
