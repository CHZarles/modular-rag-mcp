"""Document-to-chunk adapter.

The splitter stays a pure text component; this class owns stable chunk ids and
metadata inheritance.
"""

from __future__ import annotations

import hashlib

from src.core.types import Chunk, Document, JsonDict
from src.ports.ingestion import BaseSplitter


class DocumentChunker:
    def __init__(self, splitter: BaseSplitter) -> None:
        self.splitter = splitter

    def split_document(self, document: Document, trace: object | None = None) -> list[Chunk]:
        texts = self.splitter.split_text(document.text, trace=trace)
        chunks: list[Chunk] = []
        search_from = 0

        for index, text in enumerate(texts):
            start = document.text.find(text, search_from)
            if start < 0:
                start = None
                end = None
            else:
                end = start + len(text)
                search_from = end

            metadata = self._metadata_for_chunk(document, index, start, end)
            chunk = Chunk(
                id=self._chunk_id(document.id, index, text),
                text=text,
                metadata=metadata,
                source_ref=document.id,
                chunk_index=index,
                start_offset=start,
                end_offset=end,
            )
            chunks.append(chunk)

        return chunks

    def _metadata_for_chunk(
        self,
        document: Document,
        index: int,
        start: int | None,
        end: int | None,
    ) -> JsonDict:
        metadata = dict(document.metadata)
        metadata["chunk_index"] = index
        metadata["source_ref"] = document.id
        if start is not None:
            metadata["start_offset"] = start
        if end is not None:
            metadata["end_offset"] = end

        images = metadata.get("images")
        if isinstance(images, list) and start is not None and end is not None:
            metadata["images"] = [
                image
                for image in images
                if _image_overlaps_chunk(image, start, end)
            ]

        return metadata

    @staticmethod
    def _chunk_id(document_id: str, index: int, text: str) -> str:
        digest = hashlib.sha256(f"{document_id}\0{index}\0{text}".encode("utf-8")).hexdigest()
        return f"{document_id}:chunk:{index}:{digest[:12]}"


def _image_overlaps_chunk(image: object, start: int, end: int) -> bool:
    if not isinstance(image, dict):
        return False
    offset = image.get("text_offset")
    length = image.get("text_length", 0)
    if not isinstance(offset, int):
        return False
    image_end = offset + (length if isinstance(length, int) else 0)
    return offset < end and image_end >= start
