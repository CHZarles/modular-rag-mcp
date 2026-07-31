"""MCP 主查询 Tool：把协议参数转换成 KnowledgeService 请求。"""

from __future__ import annotations

from collections.abc import Callable

from src.core.response import ResponseBuilder
from src.core.services.knowledge_service import KnowledgeService
from src.core.types import JsonDict, QueryRequest
from src.mcp_server.tools.base import ToolArgumentError


class QueryKnowledgeHubTool:
    """通过稳定应用服务执行查询，不接触具体 Retriever 或存储实现。"""

    name = "query_knowledge_hub"
    description = "查询本地知识库，返回带来源引用的相关内容"
    input_schema: JsonDict = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "description": "要查询的问题"},
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "default": 5,
                "description": "最多返回的相关片段数",
            },
            "collection": {
                "type": "string",
                "minLength": 1,
                "default": "default",
                "description": "限定查询的知识集合",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        get_service: Callable[[], KnowledgeService],
        response_builder: ResponseBuilder | None = None,
    ) -> None:
        self.get_service = get_service
        self.response_builder = response_builder or ResponseBuilder()

    def call(self, arguments: JsonDict) -> JsonDict:
        """校验 Tool 参数，调用 KnowledgeService 并构建 MCP 结果。"""
        unknown = sorted(set(arguments) - {"query", "top_k", "collection"})
        if unknown:
            raise ToolArgumentError(f"unsupported arguments: {', '.join(unknown)}")

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolArgumentError("query must be a non-empty string")

        top_k = arguments.get("top_k", 5)
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise ToolArgumentError("top_k must be a positive integer")

        collection = arguments.get("collection", "default")
        if not isinstance(collection, str) or not collection.strip():
            raise ToolArgumentError("collection must be a non-empty string")

        request = QueryRequest(
            query=query.strip(),
            top_k=top_k,
            collection=collection.strip(),
        )
        response = self.get_service().query(request)
        return self.response_builder.build_mcp_result(response)
