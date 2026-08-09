from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from urllib.request import Request

import pytest

from src.ingestion.factory import _create_loader
from src.libs.loader import PdfLoader
from src.libs.loader.mineru_pdf_loader import MinerUPdfLoader


class _Response(io.BytesIO):
    def __init__(self, body: bytes = b"", status: int = 200) -> None:
        super().__init__(body)
        self.status = status
        self.status_code = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _json_response(payload: object) -> _Response:
    return _Response(json.dumps(payload).encode())


def _result_zip(*, image_path: str = "images/figure.jpg") -> bytes:
    content_list = [
        {
            "type": "image",
            "img_path": image_path,
            "image_caption": ["Figure 1. Composite result."],
            "bbox": [100, 200, 900, 700],
            "page_idx": 2,
        },
        {
            "type": "table",
            "img_path": "images/table.jpg",
            "bbox": [100, 100, 900, 900],
            "page_idx": 3,
        },
    ]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("result/full.md", f"# Paper\n\n![]({image_path})\n\nFigure 1.\n")
        archive.writestr("result/paper_content_list.json", json.dumps(content_list))
        archive.writestr(f"result/{image_path}", b"jpeg-figure")
        archive.writestr("result/images/table.jpg", b"jpeg-table")
    return output.getvalue()


def _install_successful_api(
    monkeypatch: pytest.MonkeyPatch,
    archive_bytes: bytes,
) -> list[Request]:
    requests: list[Request] = []
    responses = iter(
        [
            _json_response(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "file_urls": ["https://upload.example/signed"],
                    },
                    "msg": "ok",
                }
            ),
            _json_response(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "extract_result": [
                            {"file_name": "paper.pdf", "state": "waiting-file"}
                        ],
                    },
                    "msg": "ok",
                }
            ),
            _json_response(
                {
                    "code": 0,
                    "data": {
                        "batch_id": "batch-1",
                        "extract_result": [
                            {
                                "file_name": "paper.pdf",
                                "state": "done",
                                "full_zip_url": "https://download.example/result.zip",
                            }
                        ],
                    },
                    "msg": "ok",
                }
            ),
            _Response(archive_bytes),
        ]
    )

    def fake_urlopen(request: Request, timeout: float) -> _Response:
        requests.append(request)
        return next(responses)

    def fake_put(_session: object, url: str, **kwargs: object) -> _Response:
        assert _session.trust_env is False  # type: ignore[attr-defined]
        data = kwargs["data"]
        assert data.read() == b"pdf-bytes"  # type: ignore[attr-defined]
        request = Request(url, data=b"pdf-bytes", headers=kwargs["headers"], method="PUT")
        requests.append(request)
        return _Response()

    monkeypatch.setattr("src.libs.loader.mineru_pdf_loader.urlopen", fake_urlopen)
    monkeypatch.setattr("src.libs.loader.mineru_pdf_loader.requests.Session.put", fake_put)
    monkeypatch.setattr("src.libs.loader.mineru_pdf_loader.time.sleep", lambda _: None)
    return requests


def test_mineru_loader_uploads_polls_and_maps_figure_level_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"pdf-bytes")
    requests = _install_successful_api(monkeypatch, _result_zip())
    loader = MinerUPdfLoader(
        token="temporary-token",
        image_root=tmp_path / "images",
        poll_interval_seconds=0.01,
    )

    document = loader.load(str(source), "docs")

    assert [request.get_method() for request in requests] == ["POST", "PUT", "GET", "GET", "GET"]
    submit = requests[0]
    assert submit.full_url == "https://mineru.net/api/v4/file-urls/batch"
    assert submit.get_header("Authorization") == "Bearer temporary-token"
    assert json.loads(bytes(submit.data)) == {
        "files": [{"name": "paper.pdf"}],
        "model_version": "vlm",
    }
    upload = requests[1]
    assert upload.get_header("Authorization") is None
    assert upload.get_header("Content-type") is None
    assert upload.get_header("Content-length") == str(len(b"pdf-bytes"))
    assert requests[2].full_url.endswith("/api/v4/extract-results/batch/batch-1")

    images = document.metadata["images"]
    assert len(images) == 1
    image = images[0]
    assert image["id"] == (
        f"{hashlib.sha256(b'pdf-bytes').hexdigest()}_3_1_"
        f"{hashlib.sha256(b'jpeg-figure').hexdigest()[:12]}"
    )
    assert image["page"] == 3
    assert image["position"] == {"x0": 100.0, "y0": 200.0, "x1": 900.0, "y1": 700.0}
    assert image["mime_type"] == "image/jpeg"
    assert Path(image["path"]).read_bytes() == b"jpeg-figure"
    placeholder = f"[IMAGE: {image['id']}]"
    assert document.text[image["text_offset"] :][: image["text_length"]] == placeholder
    assert "![](images/figure.jpg)" not in document.text
    assert document.metadata["loader"] == {
        "provider": "mineru",
        "model_version": "vlm",
    }


def test_mineru_loader_reports_failed_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"pdf-bytes")
    responses = iter(
        [
            _json_response(
                {
                    "code": 0,
                    "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example"]},
                }
            ),
            _json_response(
                {
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {"file_name": "paper.pdf", "state": "failed", "err_msg": "bad PDF"}
                        ]
                    },
                }
            ),
        ]
    )
    monkeypatch.setattr(
        "src.libs.loader.mineru_pdf_loader.urlopen",
        lambda request, timeout: next(responses),
    )
    monkeypatch.setattr(
        "src.libs.loader.mineru_pdf_loader.requests.Session.put",
        lambda *args, **kwargs: _Response(),
    )

    with pytest.raises(RuntimeError, match="MinerU parsing failed: bad PDF"):
        MinerUPdfLoader(token="temporary-token", image_root=tmp_path / "images").load(
            str(source), "docs"
        )


def test_mineru_loader_times_out_while_task_is_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"pdf-bytes")
    responses = iter(
        [
            _json_response(
                {
                    "code": 0,
                    "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example"]},
                }
            ),
            _json_response(
                {
                    "code": 0,
                    "data": {
                        "extract_result": [{"file_name": "paper.pdf", "state": "pending"}]
                    },
                }
            ),
        ]
    )
    monkeypatch.setattr(
        "src.libs.loader.mineru_pdf_loader.urlopen",
        lambda request, timeout: next(responses),
    )
    monkeypatch.setattr(
        "src.libs.loader.mineru_pdf_loader.requests.Session.put",
        lambda *args, **kwargs: _Response(),
    )
    monotonic = iter([0.0, 2.0])
    monkeypatch.setattr("src.libs.loader.mineru_pdf_loader.time.monotonic", lambda: next(monotonic))

    with pytest.raises(TimeoutError, match="MinerU parsing timed out"):
        MinerUPdfLoader(
            token="temporary-token",
            image_root=tmp_path / "images",
            poll_timeout_seconds=1,
        ).load(str(source), "docs")


def test_mineru_loader_rejects_archive_path_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"pdf-bytes")
    _install_successful_api(monkeypatch, _result_zip(image_path="../outside.jpg"))

    with pytest.raises(RuntimeError, match="unsafe image path"):
        MinerUPdfLoader(token="temporary-token", image_root=tmp_path / "images").load(
            str(source), "docs"
        )
    assert not (tmp_path / "outside.jpg").exists()


def test_mineru_loader_rejects_invalid_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="token must be non-empty"):
        MinerUPdfLoader(token="", image_root=tmp_path)
    with pytest.raises(ValueError, match="model_version"):
        MinerUPdfLoader(token="token", image_root=tmp_path, model_version="MinerU-HTML")


def test_ingestion_factory_selects_loader_without_a_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(_create_loader({}, str(tmp_path)), PdfLoader)

    monkeypatch.setenv("MINERU_API_TOKEN", "environment-token")
    loader = _create_loader(
        {"loader": {"provider": "mineru", "model_version": "pipeline"}},
        str(tmp_path),
    )
    assert isinstance(loader, MinerUPdfLoader)
    assert loader.model_version == "pipeline"

    monkeypatch.delenv("MINERU_API_TOKEN")
    with pytest.raises(ValueError, match="MINERU_API_TOKEN"):
        _create_loader({"loader": {"provider": "mineru"}}, str(tmp_path))
