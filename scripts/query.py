"""本地混合检索命令行入口。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# 支持直接执行 ``python scripts/query.py``，与 ingest.py 保持相同的导入行为。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.query_engine import HybridQueryEngine  # noqa: E402
from src.core.services import build_local_query_engine  # noqa: E402
from src.core.settings import Settings, load_settings  # noqa: E402
from src.core.types import QueryRequest, RetrievalCandidate  # noqa: E402

DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def build_query_engine(
    settings: Settings,
    *,
    no_rerank: bool = False,
) -> HybridQueryEngine:
    """使用 Core 层统一工厂装配查询引擎，避免 CLI 复制依赖关系。"""
    return build_local_query_engine(settings, no_rerank=no_rerank)


def main(
    argv: Sequence[str] | None = None,
    *,
    settings_path: str | Path = DEFAULT_SETTINGS_PATH,
) -> int:
    """加载本地索引，执行一次查询并输出适合终端阅读的结果。"""
    args = _build_parser().parse_args(argv)

    try:
        settings = load_settings(str(settings_path))
        top_k = args.top_k or _positive_int(
            settings.retrieval,
            "top_k_final",
            "retrieval",
        )
        engine = build_query_engine(settings, no_rerank=args.no_rerank)
    except Exception as exc:
        print(f"查询初始化失败: {exc}", file=sys.stderr)
        return 2

    request = QueryRequest(
        query=args.query,
        top_k=top_k,
        collection=args.collection,
    )
    try:
        results = engine.search(request)
    except Exception as exc:
        print(f"查询失败: {exc}", file=sys.stderr)
        return 1

    if not results:
        print("未找到相关文档，请先运行 ingest.py 摄取数据。")
        return 0

    _print_results(results)
    if args.verbose:
        rerank_backend = _required_text(settings.rerank, "backend", "rerank").lower()
        _print_verbose(
            request,
            results,
            rerank_backend=rerank_backend,
            no_rerank=args.no_rerank,
        )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="查询本地 RAG 索引")
    parser.add_argument("--query", required=True, type=_nonempty_argument, help="要检索的问题")
    parser.add_argument(
        "--top-k",
        type=_positive_argument,
        default=None,
        help="最终返回数量；默认读取 retrieval.top_k_final",
    )
    parser.add_argument(
        "--collection",
        type=_nonempty_collection_argument,
        default="default",
        help="限定知识集合",
    )
    parser.add_argument("--verbose", action="store_true", help="显示最终候选的各阶段排名贡献")
    parser.add_argument("--no-rerank", action="store_true", help="跳过可选精排，直接使用 RRF 顺序")
    return parser


def _print_results(results: list[RetrievalCandidate]) -> None:
    print(f"检索结果（{len(results)} 条）")
    for index, candidate in enumerate(results, start=1):
        source_path = _metadata_text(candidate, "source_path") or _metadata_text(
            candidate, "source"
        )
        page = candidate.metadata.get("page")
        page_label = str(page) if isinstance(page, int) and not isinstance(page, bool) else "-"
        print(f"\n[{index}] score={candidate.score:.6f} stage={candidate.source}")
        print(f"来源: {source_path or '未知来源'} | 页码: {page_label}")
        print(_summarize(candidate.text))


def _print_verbose(
    request: QueryRequest,
    results: list[RetrievalCandidate],
    *,
    rerank_backend: str,
    no_rerank: bool,
) -> None:
    """根据最终候选的 debug 信息解释召回贡献，不引入 F3 可观测性实现。"""
    print("\n--- 查询预处理 ---")
    print(f"query={request.query!r} collection={request.collection!r} top_k={request.top_k}")

    for source, title in (
        ("dense", "Dense 召回（最终候选贡献）"),
        ("sparse", "Sparse 召回（最终候选贡献）"),
    ):
        print(f"\n--- {title} ---")
        contributions = _source_contributions(results, source)
        if not contributions:
            print("无")
            continue
        for chunk_id, contribution in contributions:
            print(
                f"chunk={chunk_id} rank={contribution.get('rank', '-')} "
                f"raw_score={contribution.get('score', '-')}"
            )

    print("\n--- Fusion 结果 ---")
    for index, candidate in enumerate(results, start=1):
        rrf = candidate.debug.get("rrf")
        score = rrf.get("score") if isinstance(rrf, Mapping) else candidate.score
        print(f"[{index}] chunk={candidate.chunk_id} rrf_score={score}")

    print("\n--- Rerank 结果 ---")
    if no_rerank:
        print("Rerank：已通过 --no-rerank 跳过")
    elif rerank_backend == "none":
        print("Rerank：backend=none，保持 Fusion 顺序")
    for index, candidate in enumerate(results, start=1):
        rerank = candidate.debug.get("rerank")
        fallback = rerank.get("fallback") if isinstance(rerank, Mapping) else False
        suffix = f" fallback={fallback}" if rerank is not None else ""
        print(f"[{index}] chunk={candidate.chunk_id} score={candidate.score}{suffix}")


def _source_contributions(
    results: list[RetrievalCandidate],
    source: str,
) -> list[tuple[str, Mapping[str, Any]]]:
    contributions: list[tuple[str, Mapping[str, Any]]] = []
    for candidate in results:
        rrf = candidate.debug.get("rrf")
        sources = rrf.get("sources") if isinstance(rrf, Mapping) else None
        if not isinstance(sources, list):
            continue
        contributions.extend(
            (candidate.chunk_id, item)
            for item in sources
            if isinstance(item, Mapping) and item.get("source") == source
        )
    return sorted(
        contributions,
        key=lambda item: (
            item[1].get("rank") if isinstance(item[1].get("rank"), int) else sys.maxsize,
            item[0],
        ),
    )


def _metadata_text(candidate: RetrievalCandidate, key: str) -> str | None:
    value = candidate.metadata.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _summarize(text: str, max_chars: int = 240) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3]}..."


def _required_text(config: Mapping[str, Any], key: str, section: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required setting: {section}.{key}")
    return value.strip()


def _positive_int(config: Mapping[str, Any], key: str, section: str) -> int:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Setting {section}.{key} must be a positive integer")
    return value


def _nonempty_argument(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise argparse.ArgumentTypeError("query must not be empty")
    return normalized


def _nonempty_collection_argument(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise argparse.ArgumentTypeError("collection must not be empty")
    return normalized


def _positive_argument(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("top-k must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("top-k must be a positive integer")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
