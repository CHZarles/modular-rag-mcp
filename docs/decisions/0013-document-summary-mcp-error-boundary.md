# ADR-0013：MCP 文档摘要查询与错误语义边界

- **状态**：当前采用
- **日期**：2026-08-01
- **决策范围**：MCP Client 如何按文档 ID 读取摘要信息、文档可见性从哪里判断，以及参数错误、未找到和系统故障如何区分
- **决策关系**：补充 ADR-0009 的 MCP 双重返回格式、ADR-0012 的知识发现流程，并沿用 ADR-0005 的 active generation 可见性
- **说明**：本文记录当前实现、语义和限制，不修改 `DEV_SPEC.md`

## 先用一句话理解

`get_document_summary` 是文档级读取入口：Tool 只校验 `doc_id` 并调用
`KnowledgeService.get_document_summary()`，成功时返回可读 Markdown 和结构化
`DocumentSummary`；文档不存在时返回可供 Agent 理解和纠正的 MCP Tool 错误，而不是把它
伪装成服务器故障。

```text
MCP Client
    │ tools/call: get_document_summary
    ▼
ProtocolHandler
    ▼
GetDocumentSummaryTool
    │ doc_id
    ▼
KnowledgeService.get_document_summary()
    ▼
DocumentSummary
    ├─ content：可读标题、摘要、来源和标签
    └─ structuredContent：稳定字段供 Agent/UI 使用
```

## 它在知识浏览流程中的位置

三个 MCP Tool 当前承担不同粒度的职责：

```text
list_collections
    │ 发现有哪些知识集合
    ▼
query_knowledge_hub
    │ 在集合中找到相关 Chunk 和来源
    ▼
get_document_summary
      查看某一文档的文档级信息
```

`get_document_summary` 不执行语义检索，也不返回全文。它适合客户端已经知道 `doc_id` 后，
进一步获取标题、摘要、标签和来源信息。

当前仍缺少专门的“列举集合内文档”Tool，查询响应中的公开引用也不保证始终直接提供
`doc_id`。因此现阶段调用方通常需要从摄取结果、文档 metadata 或其他管理入口获得文档
ID。只有当真实使用表明浏览路径不足时，才应新增文档列表 Tool，而不是让摘要 Tool 同时
承担搜索职责。

## 为什么继续通过 KnowledgeService

核心业务调用只有：

```python
document = self.get_service().get_document_summary(doc_id)
```

Tool 不直接：

- 查询 Chroma Chunk；
- 扫描源文档目录；
- 读取 SQLite 表结构；
- 从第一个 Chunk 临时拼接摘要；
- 创建新的存储客户端。

这样 MCP、CLI、Dashboard 以及未来的 HTTP 服务都可以共享同一个文档摘要契约。本地和远程
实现可以使用不同的数据来源，但 Tool 的输入、成功结果和错误语义保持稳定。

这也避免重现早期方案中的问题：让 Tool 自己初始化 Chroma、按 metadata 搜索 Chunk、推断
标题并维护另一套 `DocumentSummary`。那种实现会让 MCP 层知道具体存储结构，并与应用服务
已经拥有的目录能力重复。

## 输入契约

当前 Tool 只接受一个必填参数：

```json
{
  "doc_id": "stable-document-id"
}
```

Schema 明确要求非空字符串，并拒绝额外字段：

```json
{
  "type": "object",
  "properties": {
    "doc_id": {"type": "string", "minLength": 1}
  },
  "required": ["doc_id"],
  "additionalProperties": false
}
```

Tool 运行时再次执行相同边界校验，并在调用服务前去掉首尾空白。以下输入都会成为标准
`INVALID_PARAMS`：

```json
{}
{"doc_id": ""}
{"doc_id": "   "}
{"doc_id": 123}
{"doc_id": "doc-1", "collection": "docs"}
```

当前没有 `collection` 参数，因为稳定应用服务契约是
`get_document_summary(doc_id)`。MCP 层不应自行增加一个底层服务无法一致执行的过滤条件。

## DocumentSummary 契约

成功结果使用已有领域对象：

```python
DocumentSummary(
    doc_id="doc-1",
    source_path="/knowledge/rag-guide.pdf",
    title="RAG Guide",
    summary="A practical guide to hybrid retrieval.",
    tags=["rag", "retrieval"],
    metadata={"collection": "docs", "chunk_count": 12},
)
```

字段语义如下：

| 字段 | 含义 |
|------|------|
| `doc_id` | 服务返回的规范文档身份 |
| `source_path` | 当前文档来源；本地模式可能是本机路径 |
| `title` | 文档标题，未知时为 `None` |
| `summary` | 文档级摘要，尚未生成时为 `None` |
| `tags` | 文档级标签，尚未生成时为空列表 |
| `metadata` | collection、generation、Chunk 数等公开扩展信息 |

MCP 层复用领域对象的 `to_dict()`，不再定义第二个同名 DTO。这样应用服务增加兼容字段时，
所有入口能保持同一数据形状。

`metadata` 和 `source_path` 都属于对外边界。当前项目定位是本地 MCP，因此返回本机来源路径
便于用户定位原文件；如果未来通过 HTTP、多租户或远程 MCP 暴露，服务层必须先把绝对路径
转换为安全的逻辑来源，且过滤内部数据库路径、用户名、密钥和诊断字段。

## 成功结果为什么有两种表达

成功示例：

```json
{
  "content": [
    {
      "type": "text",
      "text": "# RAG Guide\n\nA practical guide to hybrid retrieval.\n\n- 文档 ID：`doc-1`\n- 来源：`rag-guide.pdf`\n- 标签：rag、retrieval"
    }
  ],
  "structuredContent": {
    "document": {
      "doc_id": "doc-1",
      "source_path": "rag-guide.pdf",
      "title": "RAG Guide",
      "summary": "A practical guide to hybrid retrieval.",
      "tags": ["rag", "retrieval"],
      "metadata": {}
    }
  }
}
```

两部分来自同一个 `DocumentSummary`：

| 返回部分 | 主要使用者 | 作用 |
|----------|------------|------|
| `content` | 人、LLM、文本客户端 | 直接展示标题和摘要 |
| `structuredContent.document` | Agent、Dashboard、自动化程序 | 稳定读取 ID、标签、来源和 metadata |

这沿用 ADR-0009 和 ADR-0012 的双重表达。程序不需要解析 Markdown，普通聊天客户端也不必
自行格式化 JSON。

## 缺少摘要字段时的行为

`title`、`summary` 和 `tags` 在领域模型中允许缺失。MCP 文本结果使用稳定占位：

```text
# 未命名文档

暂无摘要。

- 文档 ID：`doc-2`
- 来源：`notes.txt`
- 标签：暂无标签
```

结构化内容不会把这些展示占位写回领域数据：

```json
{
  "title": null,
  "summary": null,
  "tags": []
}
```

这是重要的区分。“暂无摘要”是 UI 文本，不是已经保存的真实摘要。Agent 若需要判断摘要是否
存在，应读取 `structuredContent`，而不是解析展示字符串。

当前 `SQLiteKnowledgeCatalog` 从来源文件名推导标题，并返回 collection、内容版本、
generation、Chunk 数和处理时间。它尚未读取 C6 生成的 Chunk 级摘要与标签并聚合成文档级
字段，因此本地默认结果中的 `summary` 和 `tags` 很可能为空。这是目录实现的能力边界，不应
由 MCP Tool 临时扫描所有 Chunk 补偿。

## 三类错误必须区分

### 1. 参数错误

参数缺失、类型不对或包含未知字段时抛出 `ToolArgumentError`。`ProtocolHandler` 将其转换为
JSON-RPC `INVALID_PARAMS`。客户端应修改调用参数后重试。

### 2. 文档不存在

文档 ID 格式正确，但当前可见目录中没有对应文档时，返回 MCP Tool 级错误：

```json
{
  "content": [
    {"type": "text", "text": "未找到文档：`missing`。"}
  ],
  "structuredContent": {
    "error": {
      "code": "document_not_found",
      "doc_id": "missing"
    }
  },
  "isError": true
}
```

这是一次已成功路由和执行的 Tool 调用，只是领域结果为“对象不存在”。使用 `isError=true`
可以让 LLM/Agent 看到错误并选择另一个 ID，不需要把它升级成 JSON-RPC 协议故障。

### 3. 内部故障

数据库损坏、服务初始化失败或未预期异常由 `ProtocolHandler` 转换成脱敏的
`INTERNAL_ERROR`。完整堆栈只进入 stderr 日志，不能通过 MCP wire 暴露内部路径和实现细节。

```text
INVALID_PARAMS       调用方式不合法
document_not_found   调用合法，但目标不存在
INTERNAL_ERROR       系统未能完成预期操作
```

把三者分开可以避免两个危险误判：把数据库故障告诉用户成“文档不存在”，或者把正常的未找到
变成看似需要重启服务的内部错误。

当前本地目录使用 `KeyError` 表示未找到，Tool 因此只把 `KeyError` 转换为
`document_not_found`。如果未来存在其他可能抛出 `KeyError` 的目录实现，应新增明确的领域异常
如 `DocumentNotFoundError`，避免把实现缺陷误判成正常未找到。

## 本地可见性与文档身份

默认本地链路是：

```text
GetDocumentSummaryTool
    ▼
LocalKnowledgeService
    ▼
SQLiteKnowledgeCatalog
    ▼
摄取控制面的 published + active generation 记录
```

Catalog 不查询残留在 Chroma 或 BM25 中的旧 Chunk。只有满足以下条件的控制面记录才可见：

```text
attempt_status == published
且
row.generation == active_generations[row.doc_key]
```

因此文档更新后，摘要 Tool 返回当前发布代，不会因为旧向量仍未清理而读到过期信息。

本地实现接受两种输入身份：

- `doc_key`：稳定逻辑文档身份，也是推荐的规范 ID；
- `file_hash`：兼容摄取结果中可能出现的内容版本 ID。

服务返回时总是把规范 `doc_key` 写入 `DocumentSummary.doc_id`。调用方可以借此把兼容 ID
规范化为稳定身份。

内容哈希不是天然的全局文档身份。同一份内容如果被摄取到多个 collection，可能共享
`file_hash`。当前 API 又没有 collection 参数，因此用哈希查询可能存在歧义。可靠调用应优先
使用 `doc_key`；若跨集合同内容成为常见场景，应收紧契约为只接受规范 ID，或引入显式的
`DocumentRef(collection, doc_id)`，不能依赖数据库返回顺序选择第一条。

## 延迟注入与异步边界

`get_document_summary` 与另外两个 MCP Tool 共用 Server 注入的 `get_service`：

```text
initialize / tools/list
        │
        └─ 不创建完整 KnowledgeService

第一次 tools/call
        │
        └─ 构建并缓存默认服务

后续 tools/call
        │
        └─ 复用同一实例
```

单元测试可以注入 Fake，只证明 Tool 调用了正确应用边界；真实运行则延迟创建本地目录和查询
依赖。同步目录读取通过 `asyncio.to_thread()` 执行，不阻塞官方 MCP SDK 的异步协议循环。

## 当前限制与升级条件

当前实现有意不承担以下能力：

- 不按标题或正文搜索文档；
- 不列举某个 collection 下的文档；
- 不返回全文或所有 Chunk；
- 不聚合 Chunk 级 summary/tags；
- 不接受 collection 作为消歧参数；
- 不执行权限检查或租户隔离；
- 不对 Markdown 中的文档字段做专门转义；
- 不把绝对路径转换为远程可访问 URL。

出现以下需求时应扩展领域服务，而不是把逻辑堆进 Tool：

1. 客户端需要浏览集合内文档，应新增稳定的文档列表请求和分页契约；
2. 需要可靠文档级摘要，应在摄取阶段生成并持久化文档聚合结果；
3. 内容哈希在多个 collection 中产生歧义，应采用规范 doc_key 或复合引用；
4. 进入远程或多租户部署，应在服务层授权并清理来源路径和 metadata；
5. 目录实现增加后，应以明确的 `DocumentNotFoundError` 代替宽泛的 `KeyError`；
6. 文档标题或摘要可能来自不可信输入时，客户端应把 Tool 内容视为引用数据，而不是新的系统
   指令。

## 验证证据

- `tests/unit/test_get_document_summary.py` 覆盖 Fake Service 注入、ID 规范化、成功双重返回、
  缺少可选字段、文档不存在以及非法参数。
- `tests/integration/test_mcp_server.py` 验证官方 MCP `tools/list` 会发布
  `get_document_summary`。
- `tests/integration/test_knowledge_service_local.py` 覆盖 active generation 文档摘要读取和未找到
  行为。
- E5 完成时全量回归为 `335 passed, 5 skipped`，相关文件通过 Ruff、Mypy 和
  `git diff --check`。

## 关联实现与决策

- `src/mcp_server/tools/get_document_summary.py`
- `src/mcp_server/server.py`
- `src/mcp_server/protocol_handler.py`
- `src/core/services/knowledge_service.py`
- `src/core/services/local_knowledge_service.py`
- `src/core/types.py` 中的 `DocumentSummary`
- `docs/decisions/0005-ingestion-generation-state-machine.md`
- `docs/decisions/0009-query-knowledge-hub-mcp-boundary.md`
- `docs/decisions/0012-list-collections-mcp-discovery-boundary.md`
