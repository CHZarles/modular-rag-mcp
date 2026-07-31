"""在线查询 CLI 的装配、参数传递和输出格式测试。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import scripts.query as query_cli
from src.core.query_engine import HybridQueryEngine, NoneReranker
from src.core.settings import Settings
from src.core.types import ChunkRecord, JsonDict, QueryRequest, RetrievalCandidate, SearchHit
from src.libs.embedding import EmbeddingFactory
from src.libs.reranker import RerankerFactory
from src.libs.vector_store import VectorStoreFactory


class FakeQueryEngine:
    def __init__(self, results: list[RetrievalCandidate] | Exception) -> None:
        self.results = results
        self.calls: list[QueryRequest] = []

    def search(
        self,
        request: QueryRequest,
        trace: object | None = None,
    ) -> list[RetrievalCandidate]:
        self.calls.append(request)
        if isinstance(self.results, Exception):
            raise self.results
        return self.results


class DeterministicEmbedding:
    def embed(
        self,
        texts: list[str],
        trace: object | None = None,
    ) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


class FakeVectorStore:
    def upsert(self, records: list[ChunkRecord], trace: Any | None = None) -> None:
        return None

    def query(
        self,
        vector: list[float],
        top_k: int,
        filters: JsonDict | None = None,
        trace: Any | None = None,
    ) -> list[SearchHit]:
        return []

    def get_by_ids(self, ids: list[str]) -> list[ChunkRecord]:
        return []

    def delete_by_metadata(self, filters: JsonDict) -> int:
        return 0


@pytest.fixture(autouse=True)
def cleanup_fake_backends() -> Iterator[None]:
    EmbeddingFactory.unregister("query-test")
    RerankerFactory.unregister("query-test")
    VectorStoreFactory.unregister("query-test")
    yield
    EmbeddingFactory.unregister("query-test")
    RerankerFactory.unregister("query-test")
    VectorStoreFactory.unregister("query-test")


def test_main_runs_query_and_prints_verbose_stage_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = FakeQueryEngine([_candidate()])
    settings = _settings(Path("/tmp/query-cli"))
    monkeypatch.setattr(query_cli, "load_settings", lambda path: settings)
    monkeypatch.setattr(
        query_cli,
        "build_query_engine",
        lambda loaded, *, no_rerank: engine,
    )

    exit_code = query_cli.main(
        [
            "--query",
            "  lease state  ",
            "--top-k",
            "1",
            "--collection",
            "docs",
            "--verbose",
            "--no-rerank",
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert engine.calls == [
        QueryRequest(query="lease state", top_k=1, collection="docs")
    ]
    assert "检索结果（1 条）" in output.out
    assert "score=0.420000" in output.out
    assert "manual.pdf" in output.out
    assert "页码: 3" in output.out
    assert "Dense 召回（最终候选贡献）" in output.out
    assert "Sparse 召回（最终候选贡献）" in output.out
    assert "Rerank：已通过 --no-rerank 跳过" in output.out
    assert output.err == ""


def test_main_prints_friendly_empty_result_and_reports_query_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings(Path("/tmp/query-cli"))
    empty_engine = FakeQueryEngine([])
    monkeypatch.setattr(query_cli, "load_settings", lambda path: settings)
    monkeypatch.setattr(
        query_cli,
        "build_query_engine",
        lambda loaded, *, no_rerank: empty_engine,
    )

    assert query_cli.main(["--query", "missing"]) == 0
    assert "未找到相关文档，请先运行 ingest.py 摄取数据" in capsys.readouterr().out
    assert empty_engine.calls[0].top_k == 9

    failing_engine = FakeQueryEngine(RuntimeError("embedding unavailable"))
    monkeypatch.setattr(
        query_cli,
        "build_query_engine",
        lambda loaded, *, no_rerank: failing_engine,
    )
    assert query_cli.main(["--query", "lease"]) == 1
    assert "查询失败: embedding unavailable" in capsys.readouterr().err


def test_main_reports_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_to_load(path: str) -> Settings:
        raise ValueError("missing retrieval settings")

    monkeypatch.setattr(query_cli, "load_settings", fail_to_load)

    assert query_cli.main(["--query", "lease"]) == 2
    assert "查询初始化失败: missing retrieval settings" in capsys.readouterr().err


def test_build_query_engine_shares_generation_store_and_uses_configured_limits(
    tmp_path: Path,
) -> None:
    EmbeddingFactory.register("query-test", lambda config: DeterministicEmbedding())
    RerankerFactory.register("query-test", lambda config: NoneReranker())
    VectorStoreFactory.register("query-test", lambda config: FakeVectorStore())
    settings = _settings(tmp_path)

    engine = query_cli.build_query_engine(settings, no_rerank=False)

    assert isinstance(engine, HybridQueryEngine)
    assert engine.config.dense_top_k == 7
    assert engine.config.sparse_top_k == 8
    # 启用精排时要为 top_m=30 保留足够的 Fusion 候选。
    assert engine.config.fusion_top_k == 30
    assert engine.dense_retriever is not None
    assert engine.sparse_retriever is not None
    assert engine.dense_retriever.generation_store is engine.sparse_retriever.bm25_store.generation_store

    without_rerank = query_cli.build_query_engine(settings, no_rerank=True)
    assert isinstance(without_rerank.reranker, NoneReranker)
    assert without_rerank.config.fusion_top_k == 9


def _settings(root: Path) -> Settings:
    return Settings(
        knowledge_service={"mode": "local"},
        llm={"provider": "openai"},
        embedding={"provider": "query-test"},
        splitter={"provider": "recursive"},
        vector_store={"backend": "query-test"},
        retrieval={
            "sparse_backend": "bm25",
            "fusion_algorithm": "rrf",
            "top_k_dense": 7,
            "top_k_sparse": 8,
            "top_k_final": 9,
        },
        rerank={"backend": "query-test", "top_m": 30, "timeout_seconds": 10},
        evaluation={"backends": ["custom"]},
        observability={"enabled": False},
        ingestion={
            "storage": {
                "integrity_db_path": str(root / "db/integrity.db"),
                "bm25_path": str(root / "db/bm25"),
            }
        },
    )


def _candidate() -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id="chunk-a",
        text="A lease prevents concurrent publication.",
        metadata={"source_path": "manual.pdf", "page": 3, "collection": "docs"},
        score=0.42,
        source="fusion",
        rank=1,
        debug={
            "rrf": {
                "k": 60,
                "score": 0.42,
                "sources": [
                    {"source": "dense", "rank": 1, "score": 0.1},
                    {"source": "sparse", "rank": 2, "score": 3.2},
                ],
            }
        },
    )
