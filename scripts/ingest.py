"""离线 PDF 摄取命令行入口。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# 规格要求支持 ``python scripts/ingest.py``。此时 Python 默认只把 scripts/ 放入
# 模块搜索路径，因此需要先加入项目根目录，才能加载当前工作区中的 src 包。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.settings import Settings, load_settings  # noqa: E402
from src.core.types import IngestionRequest, IngestionResult  # noqa: E402
from src.ingestion.chunking import DocumentChunker  # noqa: E402
from src.ingestion.embedding import BatchProcessor, DenseEncoder, SparseEncoder  # noqa: E402
from src.ingestion.pipeline import IngestionPipeline  # noqa: E402
from src.ingestion.storage import BM25Indexer, ImageStorage  # noqa: E402
from src.ingestion.transform import ChunkRefiner, ImageCaptioner, MetadataEnricher  # noqa: E402
from src.libs.loader import PdfLoader, SQLiteIntegrityStore  # noqa: E402
from src.libs.vector_store import create_vector_store  # noqa: E402

DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def build_ingestion_pipeline(settings: Settings) -> IngestionPipeline:
    """按同一份配置装配完整 Pipeline，并共享 generation 控制面。"""
    ingestion = settings.ingestion
    storage = ingestion.get("storage")
    if not isinstance(storage, Mapping):
        raise ValueError("Missing required setting: ingestion.storage")

    image_root = _required_text(storage, "image_root", "ingestion.storage")
    integrity = SQLiteIntegrityStore(
        _required_text(storage, "integrity_db_path", "ingestion.storage")
    )
    vector_store = create_vector_store(settings)

    # BM25 与图片存储必须读取同一个控制面，否则它们无法判断哪一代已经发布。
    bm25_store = BM25Indexer(
        _required_text(storage, "bm25_path", "ingestion.storage"),
        generation_store=integrity,
    )
    image_store = ImageStorage(
        _required_text(storage, "image_db_path", "ingestion.storage"),
        image_root,
        generation_store=integrity,
    )

    return IngestionPipeline(
        integrity=integrity,
        # Loader 直接写图片文件，ImageStorage 再登记引用；二者必须共用 image_root。
        loader=PdfLoader(image_root=image_root),
        chunker=DocumentChunker(settings),
        transforms=[
            ChunkRefiner(settings),
            MetadataEnricher(settings),
            ImageCaptioner(settings),
        ],
        batch_processor=BatchProcessor(
            DenseEncoder(settings),
            SparseEncoder(),
            batch_size=_positive_int(ingestion, "batch_size", "ingestion"),
        ),
        vector_store=vector_store,
        bm25_store=bm25_store,
        image_store=image_store,
        claim_lease_seconds=_positive_float(
            ingestion,
            "claim_lease_seconds",
            "ingestion",
        ),
    )


def discover_pdf_files(source: str | Path) -> list[Path]:
    """把单个 PDF 或目录展开为稳定排序的 PDF 文件列表。"""
    path = Path(source).expanduser()
    if path.is_file():
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"只支持 PDF 文件: {path}")
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"路径不存在: {path}")

    files = sorted(
        (candidate for candidate in path.rglob("*") if candidate.is_file()),
        key=lambda candidate: str(candidate).casefold(),
    )
    pdf_files = [candidate for candidate in files if candidate.suffix.lower() == ".pdf"]
    if not pdf_files:
        raise ValueError(f"目录中没有 PDF 文件: {path}")
    return pdf_files


def main(
    argv: Sequence[str] | None = None,
    *,
    settings_path: str | Path = DEFAULT_SETTINGS_PATH,
) -> int:
    """解析 CLI 参数，逐个摄取文件，并以退出码汇总执行结果。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        files = discover_pdf_files(args.path)
        settings = load_settings(str(settings_path))
        pipeline = build_ingestion_pipeline(settings)
    except Exception as exc:
        print(f"摄取初始化失败: {exc}", file=sys.stderr)
        return 2

    results = [
        pipeline.run(
            IngestionRequest(
                source_path=str(path),
                collection=args.collection,
                force=args.force,
            )
        )
        for path in files
    ]
    for result in results:
        _print_result(result)

    succeeded = sum(result.status == "success" for result in results)
    skipped = sum(result.status == "skipped" for result in results)
    failed = sum(result.status == "failed" for result in results)
    print(f"摄取完成: 成功 {succeeded}，跳过 {skipped}，失败 {failed}")
    return 1 if failed else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把 PDF 文件离线摄取到本地 RAG 索引")
    parser.add_argument("--path", required=True, help="PDF 文件或包含 PDF 的目录")
    parser.add_argument("--collection", default="default", help="目标知识集合名称")
    parser.add_argument("--force", action="store_true", help="即使内容未变化也创建并发布新代")
    return parser


def _print_result(result: IngestionResult) -> None:
    if result.status == "success":
        print(
            f"[成功] {result.source_path} "
            f"(chunks={result.chunk_count}, images={result.image_count})"
        )
        return
    if result.status == "skipped":
        reason = result.metadata.get("reason", "unchanged")
        print(f"[跳过] {result.source_path} ({reason})")
        return
    print(f"[失败] {result.source_path} ({result.error or 'unknown error'})", file=sys.stderr)


def _required_text(config: Mapping[str, Any], key: str, section: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required setting: {section}.{key}")
    return value.strip()


def _positive_int(config: Mapping[str, Any], key: str, section: str) -> int:
    value = config.get(key)
    if value is None or isinstance(value, bool):
        raise ValueError(f"Setting {section}.{key} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Setting {section}.{key} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"Setting {section}.{key} must be a positive integer")
    return parsed


def _positive_float(config: Mapping[str, Any], key: str, section: str) -> float:
    value = config.get(key)
    if value is None or isinstance(value, bool):
        raise ValueError(f"Setting {section}.{key} must be positive")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Setting {section}.{key} must be positive") from exc
    if parsed <= 0:
        raise ValueError(f"Setting {section}.{key} must be positive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
