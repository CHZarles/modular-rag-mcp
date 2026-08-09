"""MinerU PDF adapter: submit, poll, download, and normalize one document."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import tempfile
import time
import zipfile
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import requests

from src.core.types import Document, JsonDict
from src.libs.loader.file_integrity import compute_sha256

_API_BASE = "https://mineru.net/api/v4"
_PENDING_STATES = {"waiting-file", "uploading", "pending", "running", "converting"}
_SUPPORTED_MODELS = {"pipeline", "vlm"}
_MAX_JSON_BYTES = 64 * 1024 * 1024
_MAX_MARKDOWN_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_IMAGE_BYTES = 128 * 1024 * 1024
_MAX_TOTAL_IMAGE_BYTES = 512 * 1024 * 1024


class MinerUPdfLoader:
    """Use MinerU's standard API and map its ZIP output to ``Document``."""

    supported_extensions: tuple[str, ...] = (".pdf",)

    def __init__(
        self,
        token: str,
        image_root: str | Path = "data/images",
        *,
        model_version: str = "vlm",
        poll_interval_seconds: float = 3,
        poll_timeout_seconds: float = 900,
        request_timeout_seconds: float = 120,
    ) -> None:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("MinerU configuration error: token must be non-empty")
        if model_version not in _SUPPORTED_MODELS:
            raise ValueError("MinerU configuration error: model_version must be pipeline or vlm")
        for name, value in (
            ("poll_interval_seconds", poll_interval_seconds),
            ("poll_timeout_seconds", poll_timeout_seconds),
            ("request_timeout_seconds", request_timeout_seconds),
        ):
            if isinstance(value, bool) or value <= 0:
                raise ValueError(f"MinerU configuration error: {name} must be positive")

        self._token = token.strip()
        self.image_root = Path(image_root).expanduser()
        self.model_version = model_version
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.poll_timeout_seconds = float(poll_timeout_seconds)
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.revision = f"mineru:{model_version}:document-v1"

    def load(
        self,
        source_path: str,
        collection: str,
        trace: Any | None = None,
    ) -> Document:
        path = Path(source_path).expanduser()
        _validate_input(path, collection)
        file_hash = compute_sha256(path)

        batch_id, upload_url = self._create_upload(path.name)
        self._upload(path, upload_url)
        archive_url = self._poll(batch_id, path.name)

        with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as archive_file:
            self._download(archive_url, archive_file)
            archive_file.seek(0)
            text, images = self._map_archive(archive_file, collection, file_hash)

        return Document(
            id=file_hash,
            text=text,
            metadata={
                "source_path": str(path),
                "collection": collection,
                "doc_type": "pdf",
                "title": path.stem,
                "images": images,
                "loader": {
                    "provider": "mineru",
                    "model_version": self.model_version,
                },
            },
        )

    def _create_upload(self, file_name: str) -> tuple[str, str]:
        data = self._api_json(
            Request(
                f"{_API_BASE}/file-urls/batch",
                data=json.dumps(
                    {
                        "files": [{"name": file_name}],
                        "model_version": self.model_version,
                    }
                ).encode(),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            ),
            "MinerU upload URL request",
        )
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls")
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise RuntimeError("MinerU upload URL response is missing batch_id")
        if (
            not isinstance(file_urls, list)
            or len(file_urls) != 1
            or not isinstance(file_urls[0], str)
        ):
            raise RuntimeError("MinerU upload URL response must contain one file URL")
        _require_https(file_urls[0], "upload URL")
        return batch_id, file_urls[0]

    def _upload(self, path: Path, upload_url: str) -> None:
        try:
            with path.open("rb") as source, requests.Session() as session:
                # Signed OSS uploads are direct; local HTTP proxies commonly break large PUT bodies.
                session.trust_env = False
                with session.put(
                    upload_url,
                    data=source,
                    headers={"Content-Length": str(path.stat().st_size)},
                    timeout=self.request_timeout_seconds,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise RuntimeError(
                            f"MinerU file upload failed with HTTP {response.status_code}"
                        )
        except requests.RequestException as exc:
            raise RuntimeError(f"MinerU file upload failed: {type(exc).__name__}") from exc

    def _poll(self, batch_id: str, file_name: str) -> str:
        deadline = time.monotonic() + self.poll_timeout_seconds
        while True:
            data = self._api_json(
                Request(
                    f"{_API_BASE}/extract-results/batch/{batch_id}",
                    headers={"Authorization": f"Bearer {self._token}"},
                    method="GET",
                ),
                "MinerU result request",
            )
            result = _select_result(data.get("extract_result"), file_name)
            state = result.get("state")
            if state == "done":
                archive_url = result.get("full_zip_url")
                if not isinstance(archive_url, str) or not archive_url.strip():
                    raise RuntimeError("MinerU completed result is missing full_zip_url")
                _require_https(archive_url, "result URL")
                return archive_url
            if state == "failed":
                message = _short_message(result.get("err_msg"), "unknown error")
                raise RuntimeError(f"MinerU parsing failed: {message}")
            if state not in _PENDING_STATES:
                raise RuntimeError(f"MinerU returned unknown task state: {state!r}")
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"MinerU parsing timed out after {self.poll_timeout_seconds:g} seconds"
                )
            time.sleep(self.poll_interval_seconds)

    def _download(self, archive_url: str, target: Any) -> None:
        with self._open(Request(archive_url, method="GET"), "MinerU result download") as response:
            self._require_success(response, "MinerU result download")
            _copy_limited(response, target, _MAX_ARCHIVE_BYTES, "MinerU result archive")

    def _map_archive(
        self,
        archive_file: Any,
        collection: str,
        file_hash: str,
    ) -> tuple[str, list[JsonDict]]:
        try:
            with zipfile.ZipFile(archive_file) as archive:
                markdown_info = _single_member(archive, lambda path: path.name == "full.md", "full.md")
                content_info = _single_member(
                    archive,
                    lambda path: path.name.endswith("_content_list.json")
                    and not path.name.endswith("_content_list_v2.json"),
                    "content_list.json",
                )
                text = (
                    _read_member(archive, markdown_info, _MAX_MARKDOWN_BYTES)
                    .decode("utf-8-sig")
                    .strip()
                )
                content = json.loads(_read_member(archive, content_info, _MAX_JSON_BYTES))
                if not isinstance(content, list):
                    raise RuntimeError("MinerU content_list.json must contain a list")
                return self._map_content(archive, text, content, collection, file_hash)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            raise RuntimeError("MinerU result archive is invalid") from exc

    def _map_content(
        self,
        archive: zipfile.ZipFile,
        text: str,
        content: list[object],
        collection: str,
        file_hash: str,
    ) -> tuple[str, list[JsonDict]]:
        images: list[JsonDict] = []
        output_dir = self.image_root / collection
        total_size = 0
        cursor = 0

        for raw in content:
            if not isinstance(raw, Mapping) or raw.get("type") not in {"image", "chart"}:
                continue
            image_path = raw.get("img_path")
            if not isinstance(image_path, str) or not image_path.strip():
                raise RuntimeError("MinerU image entry is missing img_path")
            asset_parts = _safe_parts(image_path, "image path")
            member = _asset_member(archive, asset_parts)
            if member.file_size > _MAX_IMAGE_BYTES:
                raise RuntimeError("MinerU image exceeds the extraction size limit")
            total_size += member.file_size
            if total_size > _MAX_TOTAL_IMAGE_BYTES:
                raise RuntimeError("MinerU images exceed the extraction size limit")

            page = _page_number(raw.get("page_idx"))
            position = _position(raw.get("bbox"))
            sequence = len(images) + 1
            image_id, local_path, mime_type = _write_image(
                archive,
                member,
                output_dir,
                file_hash,
                page,
                sequence,
            )
            placeholder = f"[IMAGE: {image_id}]"
            text, offset, cursor = _replace_markdown_image(
                text,
                image_path,
                placeholder,
                cursor,
            )
            images.append(
                {
                    "id": image_id,
                    "path": str(local_path),
                    "page": page,
                    "mime_type": mime_type,
                    "text_offset": offset,
                    "text_length": len(placeholder),
                    "position": position,
                }
            )
        # Fallback insertion can change earlier offsets; derive them from the final text.
        for image in images:
            image["text_offset"] = text.index(f"[IMAGE: {image['id']}]")
        return text, images

    def _api_json(self, request: Request, label: str) -> JsonDict:
        with self._open(request, label) as response:
            self._require_success(response, label)
            raw = _read_limited(response, _MAX_JSON_BYTES, f"{label} response")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{label} returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise RuntimeError(f"{label} returned an invalid response")
        if payload.get("code") != 0:
            code = payload.get("code")
            message = _short_message(payload.get("msg"), "unknown error")
            raise RuntimeError(f"{label} failed with code {code}: {message}")
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise RuntimeError(f"{label} response is missing data")
        return dict(data)

    def _open(self, request: Request, label: str) -> Any:
        try:
            return urlopen(request, timeout=self.request_timeout_seconds)
        except HTTPError as exc:
            raise RuntimeError(f"{label} failed with HTTP {exc.code}") from exc
        except URLError as exc:
            reason = _short_message(exc.reason, type(exc.reason).__name__)
            raise RuntimeError(f"{label} failed: {reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise RuntimeError(f"{label} failed: {type(exc).__name__}") from exc

    @staticmethod
    def _require_success(response: Any, label: str) -> None:
        status = getattr(response, "status", 200)
        if not isinstance(status, int) or not 200 <= status < 300:
            raise RuntimeError(f"{label} failed with HTTP {status}")


def _validate_input(path: Path, collection: str) -> None:
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"MinerU loader input error: unsupported file type {path.suffix!r}")
    if not path.is_file():
        raise FileNotFoundError(f"MinerU loader input error: file not found: {path}")
    if not collection.strip() or Path(collection).name != collection or collection in {".", ".."}:
        raise ValueError("MinerU loader input error: collection must be a simple name")


def _require_https(url: str, label: str) -> None:
    if not url.startswith("https://"):
        raise RuntimeError(f"MinerU {label} must use HTTPS")


def _select_result(value: object, file_name: str) -> Mapping[str, Any]:
    if not isinstance(value, list):
        raise RuntimeError("MinerU result response is missing extract_result")
    candidates = [item for item in value if isinstance(item, Mapping)]
    for item in candidates:
        if item.get("file_name") == file_name:
            return item
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError("MinerU result response does not contain the uploaded file")


def _short_message(value: object, fallback: str) -> str:
    message = value.strip() if isinstance(value, str) else ""
    if isinstance(value, BaseException):
        message = str(value).strip()
    return (message or fallback)[:300]


def _copy_limited(source: Any, target: Any, limit: int, label: str) -> None:
    total = 0
    while block := source.read(1024 * 1024):
        total += len(block)
        if total > limit:
            raise RuntimeError(f"{label} exceeds the size limit")
        target.write(block)


def _read_limited(source: Any, limit: int, label: str) -> bytes:
    output = BytesIO()
    _copy_limited(source, output, limit, label)
    return output.getvalue()


def _safe_parts(raw_path: str, label: str) -> tuple[str, ...]:
    normalized = raw_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts or ":" in path.parts[0]:
        raise RuntimeError(f"MinerU archive contains unsafe {label}")
    return path.parts


def _member_parts(info: zipfile.ZipInfo) -> tuple[str, ...] | None:
    try:
        return _safe_parts(info.filename, "member path")
    except RuntimeError:
        return None


def _single_member(
    archive: zipfile.ZipFile,
    predicate: Any,
    label: str,
) -> zipfile.ZipInfo:
    matches = []
    for info in archive.infolist():
        parts = _member_parts(info)
        if not info.is_dir() and parts is not None and predicate(PurePosixPath(*parts)):
            matches.append(info)
    if len(matches) != 1:
        raise RuntimeError(f"MinerU result archive must contain one {label}")
    return matches[0]


def _asset_member(archive: zipfile.ZipFile, asset_parts: tuple[str, ...]) -> zipfile.ZipInfo:
    matches = []
    for info in archive.infolist():
        parts = _member_parts(info)
        if not info.is_dir() and parts is not None and parts[-len(asset_parts) :] == asset_parts:
            matches.append(info)
    if len(matches) != 1:
        raise RuntimeError("MinerU result archive does not contain one matching image")
    return matches[0]


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise RuntimeError("MinerU result archive member exceeds the size limit")
    with archive.open(info) as source:
        return _read_limited(source, limit, "MinerU result archive member")


def _write_image(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    output_dir: Path,
    file_hash: str,
    page: int,
    sequence: int,
) -> tuple[str, Path, str]:
    suffix = PurePosixPath(member.filename).suffix.lower()
    mime_type = mimetypes.guess_type(f"image{suffix}")[0]
    if mime_type is None or not mime_type.startswith("image/"):
        raise RuntimeError("MinerU result contains an unsupported image type")

    output_dir.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    digest = hashlib.sha256()
    try:
        with tempfile.NamedTemporaryFile(dir=output_dir, delete=False) as target:
            temporary_path = Path(target.name)
            with archive.open(member) as source:
                while block := source.read(1024 * 1024):
                    digest.update(block)
                    target.write(block)
            target.flush()
            os.fsync(target.fileno())
        image_id = f"{file_hash}_{page}_{sequence}_{digest.hexdigest()[:12]}"
        final_path = output_dir / f"{image_id}{suffix}"
        os.replace(temporary_path, final_path)
        temporary_path = None
        return image_id, final_path, mime_type
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _page_number(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError("MinerU image entry has an invalid page_idx")
    return value + 1


def _position(value: object) -> JsonDict:
    if (
        not isinstance(value, list | tuple)
        or len(value) != 4
        or any(not isinstance(item, int | float) or isinstance(item, bool) for item in value)
    ):
        raise RuntimeError("MinerU image entry has an invalid bbox")
    return dict(zip(("x0", "y0", "x1", "y1"), map(float, value), strict=True))


def _replace_markdown_image(
    text: str,
    image_path: str,
    placeholder: str,
    cursor: int,
) -> tuple[str, int, int]:
    normalized = image_path.replace("\\", "/").removeprefix("./")
    pattern = re.compile(
        r"!\[[^\]\r\n]*\]\(\s*<?(?:\./)?"
        + re.escape(normalized)
        + r">?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
    )
    match = pattern.search(text, cursor) or pattern.search(text)
    if match is not None:
        offset = match.start()
        updated = text[:offset] + placeholder + text[match.end() :]
        return updated, offset, offset + len(placeholder)

    separator = "\n\n" if text and not text.endswith("\n\n") else ""
    offset = len(text) + len(separator)
    updated = f"{text}{separator}{placeholder}"
    return updated, offset, offset + len(placeholder)


__all__ = ["MinerUPdfLoader"]
