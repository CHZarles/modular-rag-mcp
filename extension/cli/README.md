# CLI Extension

`modular-rag-cli` 是供 Agent 调用的 HTTP MCP 客户端。它不实现检索、摄取或存储逻辑，只将一次命令调用映射为现有 MCP Tool 调用。

先启动 HTTP MCP 服务：

```bash
modular-rag-mcp-http
```

随后调用：

```bash
modular-rag-cli collections
modular-rag-cli query '检索的问题' --collection default --top-k 5
modular-rag-cli document <doc_id>
modular-rag-cli upload ./document.pdf --collection default
modular-rag-cli upload ./people.csv --collection default
modular-rag-cli upload ./architecture.png --collection default
modular-rag-cli job <job_id>
```

默认端点为 `http://127.0.0.1:8766/mcp`。可用 `RAG_MCP_URL` 设置默认值，或通过任一命令的 `--url` 覆盖：

```bash
modular-rag-cli query '检索的问题' --url http://127.0.0.1:8767/mcp
```

`--request-id`、`--actor-key` 和 `--session-id` 分别映射为 `X-Request-ID`、`X-RAG-Actor-Key` 和 `X-RAG-Session-ID` 请求头。

成功或 Tool 业务错误时，stdout 仅输出该 Tool 的 `structuredContent` JSON；连接和协议诊断只写入 stderr。参数错误退出码为 `2`，连接或协议失败为 `2`，Tool 返回 `isError` 时退出码为 `1`。

`upload` 接受最大 50 MiB 的 PDF、DOCX、CSV、PNG、JPG/JPEG 和 WebP，并将文件编码为 Base64 后调用 MCP `upload_document`。命令返回 `job_id`，Agent 使用 `job` 轮询 `queued`、`running`、`success`、`skipped` 或 `failed`；CLI 不直接调用摄取 Pipeline。
