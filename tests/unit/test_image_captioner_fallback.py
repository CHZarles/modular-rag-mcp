from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.types import Chunk
from ingestion.transform import ImageCaptioner
from libs.llm import ChatResponse, ImageInput, Message
from ports.ingestion import BaseTransform


class FakeVisionLLM:
    def __init__(
        self,
        captions: dict[str, str] | None = None,
        failures: set[str] | None = None,
    ) -> None:
        self.captions = captions or {}
        self.failures = failures or set()
        self.calls: list[tuple[str, ImageInput, Any | None]] = []

    def chat_with_image(
        self,
        text: str,
        image: ImageInput,
        messages: list[Message] | None = None,
        trace: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append((text, image, trace))
        name = Path(image.path or "").name
        if name in self.failures:
            raise RuntimeError("vision model unavailable")
        return ChatResponse(content=self.captions.get(name, "A technical diagram."), model="fake")


def settings(enabled: bool) -> dict[str, object]:
    return {"ingestion": {"image_captioner": {"enabled": enabled}}}


def make_chunk(images: list[dict[str, object]]) -> Chunk:
    refs = [str(image["id"]) for image in images]
    text = "Context before.\n\n" + "\n\n".join(f"[IMAGE: {ref}]" for ref in refs)
    return Chunk(
        id="chunk-images",
        text=text,
        metadata={
            "source_path": "guide.pdf",
            "image_refs": refs,
            "images": images,
        },
        source_ref="document",
        chunk_index=0,
        start_offset=0,
        end_offset=len(text),
    )


def test_chunk_without_images_is_returned_without_calling_model() -> None:
    llm = FakeVisionLLM()
    chunk = Chunk(
        id="text-only",
        text="Text only",
        metadata={"source_path": "guide.pdf"},
        source_ref="document",
        chunk_index=0,
    )

    result = ImageCaptioner(settings(True), vision_llm=llm).transform([chunk])[0]

    assert result is chunk
    assert llm.calls == []


def test_disabled_mode_preserves_images_and_marks_them_unprocessed(tmp_path: Path) -> None:
    image: dict[str, object] = {"id": "img-1", "path": str(tmp_path / "one.png")}
    chunk = make_chunk([image])
    llm = FakeVisionLLM()

    result = ImageCaptioner(settings(False), vision_llm=llm).transform([chunk])[0]

    assert result.text == chunk.text
    assert result.metadata["image_refs"] == ["img-1"]
    assert result.metadata["images"] == [image]
    assert result.metadata["has_unprocessed_images"] is True
    assert result.metadata["unprocessed_image_refs"] == ["img-1"]
    assert "image_captions" not in result.metadata
    assert "has_unprocessed_images" not in chunk.metadata
    assert llm.calls == []


def test_enabled_mode_adds_caption_to_metadata_and_searchable_text(tmp_path: Path) -> None:
    image_path = tmp_path / "architecture.png"
    image_path.write_bytes(b"image")
    image: dict[str, object] = {
        "id": "img-1",
        "path": str(image_path),
        "mime_type": "image/png",
    }
    chunk = make_chunk([image])
    llm = FakeVisionLLM(
        {"architecture.png": "<think>inspect nodes</think>\nA RAG ingestion architecture diagram."}
    )
    trace = object()

    result = ImageCaptioner(settings(True), vision_llm=llm).transform([chunk], trace=trace)[0]

    assert result.metadata["image_captions"] == {
        "img-1": "A RAG ingestion architecture diagram."
    }
    assert result.metadata["image_captioned_by"] == "vision_llm"
    assert result.metadata["has_unprocessed_images"] is False
    assert "unprocessed_image_refs" not in result.metadata
    assert result.metadata["image_refs"] == ["img-1"]
    assert result.text == "Context before.\n\n[图片描述: A RAG ingestion architecture diagram.]"
    assert len(llm.calls) == 1
    assert llm.calls[0][1] == ImageInput(path=image_path, mime_type="image/png")
    assert "Context before." in llm.calls[0][0]
    assert llm.calls[0][2] is trace


def test_partial_failure_keeps_successful_caption_and_failed_placeholder(tmp_path: Path) -> None:
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    chunk = make_chunk(
        [
            {"id": "img-1", "path": str(first_path)},
            {"id": "img-2", "path": str(second_path)},
        ]
    )
    llm = FakeVisionLLM(
        captions={"first.png": "First diagram."},
        failures={"second.png"},
    )

    result = ImageCaptioner(settings(True), vision_llm=llm).transform([chunk])[0]

    assert result.metadata["image_captions"] == {"img-1": "First diagram."}
    assert result.metadata["has_unprocessed_images"] is True
    assert result.metadata["unprocessed_image_refs"] == ["img-2"]
    assert "[图片描述: First diagram.]" in result.text
    assert "[IMAGE: img-2]" in result.text


def test_missing_vision_client_falls_back_without_losing_reference(tmp_path: Path) -> None:
    chunk = make_chunk([{"id": "img-1", "path": str(tmp_path / "one.png")}])

    result = ImageCaptioner(settings(True)).transform([chunk])[0]

    assert result.text == chunk.text
    assert result.metadata["has_unprocessed_images"] is True
    assert result.metadata["unprocessed_image_refs"] == ["img-1"]


def test_enabled_mode_builds_current_openai_compatible_vision_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "one.png"
    image_path.write_bytes(b"image")
    llm = FakeVisionLLM({"one.png": "Caption from configured model."})
    monkeypatch.setattr(
        "src.ingestion.transform.image_captioner.create_vision_llm", lambda _: llm
    )

    result = ImageCaptioner(settings(True)).transform(
        [make_chunk([{"id": "img-1", "path": str(image_path)}])]
    )[0]

    assert result.metadata["image_captions"] == {
        "img-1": "Caption from configured model."
    }
    assert result.metadata["image_captioned_by"] == "vision_llm"


def test_missing_image_metadata_is_marked_unprocessed() -> None:
    chunk = make_chunk([])
    chunk = Chunk(
        id=chunk.id,
        text="[IMAGE: missing]",
        metadata={"image_refs": ["missing"]},
        source_ref=chunk.source_ref,
        chunk_index=chunk.chunk_index,
    )

    result = ImageCaptioner(settings(True), vision_llm=FakeVisionLLM()).transform([chunk])[0]

    assert result.metadata["has_unprocessed_images"] is True
    assert result.metadata["unprocessed_image_refs"] == ["missing"]


def test_empty_caption_is_treated_as_failure(tmp_path: Path) -> None:
    image_path = tmp_path / "empty.png"
    image_path.write_bytes(b"image")
    chunk = make_chunk([{"id": "img-1", "path": str(image_path)}])
    llm = FakeVisionLLM({"empty.png": "  "})

    result = ImageCaptioner(settings(True), vision_llm=llm).transform([chunk])[0]

    assert result.metadata["has_unprocessed_images"] is True
    assert "image_captions" not in result.metadata


def test_successful_captioning_is_idempotent(tmp_path: Path) -> None:
    image_path = tmp_path / "one.png"
    image_path.write_bytes(b"image")
    llm = FakeVisionLLM({"one.png": "Stable caption."})
    captioner = ImageCaptioner(settings(True), vision_llm=llm)

    first = captioner.transform([make_chunk([{"id": "img-1", "path": str(image_path)}])])
    second = captioner.transform(first)

    assert second == first
    assert len(llm.calls) == 1


def test_retry_reuses_successful_caption_and_only_calls_failed_image(tmp_path: Path) -> None:
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    first_path.write_bytes(b"first")
    second_path.write_bytes(b"second")
    llm = FakeVisionLLM(
        captions={"first.png": "First diagram.", "second.png": "Second diagram."},
        failures={"second.png"},
    )
    captioner = ImageCaptioner(settings(True), vision_llm=llm)
    chunk = make_chunk(
        [
            {"id": "img-1", "path": str(first_path)},
            {"id": "img-2", "path": str(second_path)},
        ]
    )

    first = captioner.transform([chunk])[0]
    llm.failures.clear()
    second = captioner.transform([first])[0]

    called_names = [Path(call[1].path or "").name for call in llm.calls]
    assert called_names == ["first.png", "second.png", "second.png"]
    assert second.metadata["image_captions"] == {
        "img-1": "First diagram.",
        "img-2": "Second diagram.",
    }
    assert second.metadata["has_unprocessed_images"] is False
    assert second.text.count("First diagram.") == 1
    assert second.text.count("Second diagram.") == 1


def test_missing_prompt_uses_fallback_with_chunk_context(tmp_path: Path) -> None:
    image_path = tmp_path / "one.png"
    image_path.write_bytes(b"image")
    llm = FakeVisionLLM({"one.png": "Caption."})
    captioner = ImageCaptioner(
        settings(True),
        vision_llm=llm,
        prompt_path=tmp_path / "missing.txt",
    )

    captioner.transform([make_chunk([{"id": "img-1", "path": str(image_path)}])])

    assert "Context before." in llm.calls[0][0]


def test_image_captioner_matches_transform_protocol() -> None:
    assert isinstance(ImageCaptioner(settings(False)), BaseTransform)
