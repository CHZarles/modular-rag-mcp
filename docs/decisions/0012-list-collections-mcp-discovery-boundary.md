# ADR-0012：MCP 集合发现与 `list_collections` 边界

- **状态**：当前采用
- **日期**：2026-08-01
- **决策范围**：MCP Client 如何发现可用知识集合、集合统计从哪里取得，以及 Tool 与存储层之间的边界
- **决策关系**：补充 ADR-0009 的 MCP Tool 边界，并沿用 ADR-0005 的 active generation 可见性
- **说明**：本文记录当前实现知识、返回格式和演进边界，不修改 `DEV_SPEC.md`

## 先用一句话理解

`list_collections` 是知识库的发现入口：它不执行检索，也不扫描具体存储，而是通过
`KnowledgeService.list_collections()` 取得统一的 `CollectionInfo`，再同时返回可读文本和
结构化统计。

```text
MCP Client
    │ tools/call: list_collections
    ▼
ProtocolHandler
    ▼
ListCollectionsTool
    ▼
KnowledgeService.list_collections()
    ▼
CollectionInfo[]
    ├─ content：适合人和模型直接阅读
    └─ structuredContent：适合 Agent、UI 和自动化程序读取
```

它解决的是“有哪些集合可以查”，不是“集合中哪些内容能回答当前问题”。后者仍由
`query_knowledge_hub` 负责。

## 为什么需要独立的发现 Tool

查询 Tool 虽然接受 `collection` 参数，但客户端在调用前未必知道有哪些合法集合。如果把
集合发现混进查询 Tool，会产生几个问题：

- 客户端只能猜测集合名，拼错后容易得到看似正常的空结果；
- Dashboard、Agent 和聊天客户端需要各自实现一套集合枚举逻辑；
- MCP 层可能被迫直接访问 SQLite、Chroma 或本地目录；
- 查询和管理能力耦合，后续共享知识服务更难替换。

因此当前把职责拆开：

| Tool | 职责 | 主要输入 | 主要输出 |
|------|------|----------|----------|
| `list_collections` | 发现知识范围 | 无 | 集合名称及统计 |
| `query_knowledge_hub` | 在指定范围内检索 | query、top_k、collection | 答案、片段和引用 |

典型 Agent 流程是先列举集合，再选择目标集合执行查询；已经知道集合名的客户端也可以直接
查询，不强制多一次调用。

## Tool 为什么只依赖 KnowledgeService

核心实现有意只有一个业务调用：

```python
collections = self.get_service().list_collections()
```

Tool 不直接访问：

- Chroma collection；
- BM25 快照；
- SQLite 摄取表；
- `data/images` 或文档目录；
- QueryEngine 和 Retriever。

`KnowledgeService` 是 MCP、CLI 和 Dashboard 共用的应用服务边界。这样本地实现、未来的
HTTP 实现和测试 Fake 都可以返回相同的 `CollectionInfo`，MCP Tool 无需理解数据来自哪种
部署模式。

这个边界也避免把“物理 collection”和“面向用户的知识集合”错误地视为同一概念。例如
Chroma 可以使用一个物理 collection，并通过 metadata 中的 `collection` 字段隔离多个逻辑
知识集合；直接枚举 Chroma collection 会得到错误的业务列表。

## CollectionInfo 契约

当前每个集合使用统一领域对象表示：

```python
CollectionInfo(
    name="docs",
    document_count=2,
    chunk_count=12,
    image_count=3,
    metadata={"description": "Engineering documents"},
)
```

字段语义如下：

| 字段 | 含义 |
|------|------|
| `name` | 查询请求中使用的逻辑集合名 |
| `document_count` | 当前可见的文档数量 |
| `chunk_count` | 当前可见文档对应的 Chunk 数量 |
| `image_count` | 当前实现能够提供的图片数量；未知时为 `0` |
| `metadata` | 面向客户端公开的扩展字段，例如集合描述 |

`metadata` 不是内部对象的任意转储。未来如果加入磁盘路径、密钥、数据库连接信息或仅供
服务端使用的诊断字段，必须在进入 `CollectionInfo` 前过滤，不能因为它是扩展字典就默认
适合通过 MCP 暴露。

## 返回为什么分为两部分

调用结果示例：

```json
{
  "content": [
    {
      "type": "text",
      "text": "可用知识集合：\n- `docs`：2 个文档，12 个片段，3 张图片"
    }
  ],
  "structuredContent": {
    "collections": [
      {
        "name": "docs",
        "document_count": 2,
        "chunk_count": 12,
        "image_count": 3,
        "metadata": {}
      }
    ],
    "total": 1
  }
}
```

两部分来自同一次服务调用：

| 返回部分 | 面向对象 | 用途 |
|----------|----------|------|
| `content` | 人、LLM、只展示文本的客户端 | 直接阅读集合及数量 |
| `structuredContent` | Agent、Dashboard、自动化程序 | 构造选择器、决定查询范围、读取统计 |

如果只有 Markdown，程序需要解析排版；如果只有 JSON，聊天客户端的直接展示体验较差。
双重表达沿用了 ADR-0009 对查询结果的相同决策。

集合描述当前从 `metadata["description"]` 读取并加入可读文本；结构化内容仍保留完整
`CollectionInfo` 形状，不为了展示字段再复制一套数据模型。

## 为什么 Tool 不接受参数

当前输入 Schema 是严格空对象：

```json
{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

Tool 在运行时也会拒绝所有额外参数。Schema 是给客户端的能力说明，运行时校验则保护真正
的服务边界；不能假设每个 MCP Client 都会在本地完整执行 JSON Schema 校验。

例如以下调用会返回标准参数错误，而不是静默忽略拼错的字段：

```json
{"collection": "docs"}
{"unknown": 1}
```

当前数据规模不需要 filter、分页或排序参数。需要这些能力时，应先扩展
`KnowledgeService.list_collections()` 的领域请求契约，再扩展 MCP Tool；不应让 MCP 层
独自实现一套只对某个存储后端有效的查询语义。

## 本地集合统计从哪里来

默认本地链路是：

```text
ListCollectionsTool
    ▼
LocalKnowledgeService
    ▼
SQLiteKnowledgeCatalog
    ▼
摄取控制面的 published + active generation 记录
```

本地实现不会通过下面这些方式猜测集合：

- 扫描文档目录名；
- 枚举 Chroma 的物理 collection；
- 读取可能包含旧数据的全部向量 metadata；
- 从 BM25 快照推导集合。

原因是摄取控制面才知道哪个 generation 已经成功发布。旧 generation 即使仍残留在 Chroma
或 BM25 中，也不应出现在集合统计里。当前目录按名称进行不区分大小写的稳定排序，使相同
数据得到确定的返回顺序。

现阶段 `SQLiteKnowledgeCatalog` 已统计文档数和 Chunk 数；图片统计尚未接入图片索引，因而
本地返回的 `image_count` 使用领域对象默认值 `0`。这个值目前表示“当前目录没有提供图片
统计”，不能据此断言集合绝对不含图片。未来补齐统计时应由 Catalog 聚合 ImageStorage，
而不是让 MCP Tool 扫描图片目录。

## 延迟依赖注入

Server 为查询 Tool 和集合 Tool 注入同一个 `get_service`：

```text
initialize / tools/list
        │
        └─ 只发布 Tool Schema，不创建完整 KnowledgeService

第一次 tools/call
        │
        └─ 构建并缓存默认 KnowledgeService

后续 tools/call
        │
        └─ 复用同一个实例
```

测试可以注入 `FakeKnowledgeService`，完全不需要 Chroma、BM25、Embedding 或真实文件；默认
运行则通过缓存工厂延迟装配本地服务。这样 MCP Client 即使在模型服务暂时不可用时，仍能
完成握手和 `tools/list`。

同步的目录读取仍由 `ProtocolHandler` 通过 `asyncio.to_thread()` 执行，避免阻塞官方 MCP
SDK 的异步协议循环。

## 空集合和错误语义

没有可用集合是一次成功调用：

```json
{
  "content": [{"type": "text", "text": "当前没有可用的知识集合。"}],
  "structuredContent": {"collections": [], "total": 0}
}
```

它与故障明确区分：

| 情况 | MCP 行为 |
|------|----------|
| 当前没有已发布集合 | 正常 Tool 结果，列表为空 |
| 传入未支持参数 | JSON-RPC `INVALID_PARAMS` |
| Tool 名称不存在 | JSON-RPC `METHOD_NOT_FOUND` |
| Catalog 或服务内部失败 | 脱敏后的 `INTERNAL_ERROR`，完整异常只写日志 |

空集合不应被包装成异常，否则 Agent 无法区分“知识库尚未摄取内容”和“服务已经损坏”。

## 当前限制与演进条件

当前实现有意保持简单：

- 没有分页；集合规模应能一次返回；
- 没有调用方自定义排序，默认沿用服务返回顺序；
- 没有按用户、租户或权限过滤集合；
- `image_count` 的本地真实聚合尚未接入；
- 没有集合级更新时间、大小、语言或标签字段；
- 集合描述暂存在 `metadata`，尚未成为强类型字段。

出现以下情况时应扩展契约，而不是在 Tool 中添加存储特例：

1. 集合数量大到单次返回影响 MCP 消息大小或延迟；
2. 引入多用户权限，需要确保未授权集合连名称都不可见；
3. Dashboard 需要稳定的更新时间、标签、存储大小等字段；
4. HTTP KnowledgeService 需要游标分页；
5. 图片数量成为 UI 或容量治理所需的可靠指标。

权限尤其必须在 `KnowledgeService` 或更下层的授权边界处理。只在 MCP 文本输出中隐藏集合，
但仍把它放进 `structuredContent`，不构成访问控制。

## 验证证据

- `tests/unit/test_list_collections.py` 覆盖 Fake Service 注入、统计序列化、描述展示、空集合
  和额外参数拒绝。
- `tests/integration/test_mcp_server.py` 验证官方 MCP `tools/list` 会同时发布
  `query_knowledge_hub` 和 `list_collections`。
- `tests/integration/test_knowledge_service_local.py` 覆盖本地 Catalog 对 active generation
  集合和统计的读取。
- E4 完成时全量回归为 `327 passed, 5 skipped`，相关文件通过 Ruff、Mypy 和
  `git diff --check`。

## 关联实现与决策

- `src/mcp_server/tools/list_collections.py`
- `src/mcp_server/server.py`
- `src/mcp_server/protocol_handler.py`
- `src/core/services/knowledge_service.py`
- `src/core/services/local_knowledge_service.py`
- `src/core/types.py` 中的 `CollectionInfo`
- `docs/decisions/0005-ingestion-generation-state-machine.md`
- `docs/decisions/0009-query-knowledge-hub-mcp-boundary.md`
