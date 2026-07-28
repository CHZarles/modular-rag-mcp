from __future__ import annotations

import json

from core.types import Chunk, ChunkRecord, Document, ImageRef


def test_document_with_image_metadata_is_json_serializable() -> None:
    image_id = "doc-hash_2_1"
    placeholder = f"[IMAGE: {image_id}]"
    text = f"Architecture overview\n\n{placeholder}\n"
    document = Document(
        id="doc-hash",
        text=text,
        metadata={
            "source_path": "docs/architecture.pdf",
            "images": [
                {
                    "id": image_id,
                    "path": f"data/images/default/{image_id}.png",
                    "page": 2,
                    "text_offset": text.index(placeholder),
                    "text_length": len(placeholder),
                    "position": {"x": 10, "y": 20},
                }
            ],
        },
    )

    payload = document.to_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert Document.from_dict(payload) == document
    assert payload["metadata"]["source_path"] == "docs/architecture.pdf"
    assert payload["metadata"]["images"][0]["text_offset"] == text.index(placeholder)


def test_chunk_roundtrip_preserves_source_and_offsets() -> None:
    chunk = Chunk(
        id="doc-hash:chunk:0",
        text="Architecture overview",
        metadata={"source_path": "docs/architecture.pdf", "chunk_index": 0},
        source_ref="doc-hash",
        chunk_index=0,
        start_offset=0,
        end_offset=21,
    )

    assert Chunk.from_dict(chunk.to_dict()) == chunk
    assert chunk.to_dict() == {
        "id": "doc-hash:chunk:0",
        "text": "Architecture overview",
        "metadata": {"source_path": "docs/architecture.pdf", "chunk_index": 0},
        "source_ref": "doc-hash",
        "chunk_index": 0,
        "start_offset": 0,
        "end_offset": 21,
    }


def test_chunk_record_from_chunk_keeps_storage_fields_stable() -> None:
    chunk = Chunk(
        id="chunk-1",
        text="Dense and sparse retrieval",
        metadata={"source_path": "docs/rag.md"},
        source_ref="doc-1",
        chunk_index=1,
    )

    record = ChunkRecord.from_chunk(
        chunk,
        dense_vector=[0.1, 0.2],
        sparse_vector={"retrieval": 1.0},
        content_hash="sha256",
    )

    assert ChunkRecord.from_dict(record.to_dict()) == record
    assert record.to_dict() == {
        "id": "chunk-1",
        "text": "Dense and sparse retrieval",
        "metadata": {"source_path": "docs/rag.md"},
        "dense_vector": [0.1, 0.2],
        "sparse_vector": {"retrieval": 1.0},
        "content_hash": "sha256",
    }


def test_image_ref_accepts_metadata_id_alias() -> None:
    image = ImageRef.from_dict(
        {
            "id": "image-1",
            "path": "data/images/default/image-1.png",
            "collection": "default",
            "source_path": "docs/rag.pdf",
            "page": 1,
        }
    )

    assert image.image_id == "image-1"
    assert image.to_dict()["source_path"] == "docs/rag.pdf"
