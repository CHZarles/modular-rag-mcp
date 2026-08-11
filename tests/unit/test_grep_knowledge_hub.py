from __future__ import annotations

import pytest

from src.core.services.grep_service import GrepMatch, GrepResponse
from src.core.types import JsonDict
from src.mcp_server.tools.base import ToolArgumentError, ToolExecutionError
from src.mcp_server.tools.grep_knowledge_hub import GrepKnowledgeHubTool


class _Service:
    def __init__(self) -> None:
        self.calls: list[JsonDict] = []

    def search(self, **kwargs: object) -> GrepResponse:
        self.calls.append(dict(kwargs))
        return GrepResponse(
            matches=[
                GrepMatch(
                    chunk_id="chunk-1",
                    text="  exact needle text  ",
                    source_path="/private/docs/manual.pdf",
                    page=3,
                    metadata={
                        "collection": "docs",
                        "title": "Manual",
                        "private": "drop me",
                    },
                    match_count=2,
                )
            ]
        )


class _Collector:
    def __init__(self) -> None:
        self.records: list[JsonDict] = []

    def collect(self, trace: object) -> None:
        self.records.append(trace.to_dict())  # type: ignore[attr-defined]


def test_tool_preserves_pattern_and_emits_sanitized_match_without_pattern_trace() -> None:
    service = _Service()
    collector = _Collector()
    tool = GrepKnowledgeHubTool(lambda: service, get_collector=lambda: collector)

    result = tool.call(
        {
            "pattern": " needle ",
            "collection": "docs",
            "top_k": 10,
            "case_sensitive": True,
        }
    )

    assert service.calls == [
        {
            "pattern": " needle ",
            "collection": "docs",
            "top_k": 10,
            "case_sensitive": True,
        }
    ]
    match = result["structuredContent"]["matches"][0]
    assert match == {
        "chunk_id": "chunk-1",
        "text": "  exact needle text  ",
        "source": "manual.pdf",
        "page": 3,
        "metadata": {"collection": "docs", "title": "Manual"},
        "match_count": 2,
    }
    metadata = collector.records[0]["metadata"]
    assert metadata["pattern_length"] == len(" needle ")
    assert "pattern" not in metadata
    assert result["structuredContent"]["trace_id"] == collector.records[0]["trace_id"]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"pattern": "ab"},
        {"pattern": "e\u0301x"},
        {"pattern": "abc\0def"},
        {"pattern": "abc", "case_sensitive": 1},
        {"pattern": "abc", "top_k": True},
        {"pattern": "abc", "top_k": 21},
        {"pattern": "a" * 4001},
        {"pattern": "abc", "extra": True},
    ],
)
def test_tool_rejects_invalid_literal_boundaries(arguments: JsonDict) -> None:
    with pytest.raises(ToolArgumentError):
        GrepKnowledgeHubTool(lambda: _Service()).call(arguments)


def test_tool_accepts_three_spaces_without_trimming() -> None:
    service = _Service()

    GrepKnowledgeHubTool(lambda: service).call({"pattern": "   "})

    assert service.calls[0]["pattern"] == "   "


def test_tool_maps_internal_errors_without_leaking_pattern_or_path_to_trace() -> None:
    class _FailingService:
        def search(self, **kwargs: object) -> GrepResponse:
            raise RuntimeError(f"{kwargs['pattern']} failed at /private/index.db")

    collector = _Collector()
    tool = GrepKnowledgeHubTool(
        lambda: _FailingService(),  # type: ignore[arg-type]
        get_collector=lambda: collector,
    )

    with pytest.raises(ToolExecutionError, match="grep_failed") as error:
        tool.call({"pattern": "secret needle"})

    assert str(error.value) == "grep_failed"
    trace = collector.records[0]
    assert trace["metadata"]["status"] == "failed"
    assert "secret needle" not in str(trace)
    assert "/private/index.db" not in str(trace)
