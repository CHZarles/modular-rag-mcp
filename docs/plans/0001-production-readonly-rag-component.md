# 内网只读 RAG 组件增量开发规格

> 文档类型：Incremental DEV SPEC  
> 版本：1.1  
> 状态：待实施  
> 日期：2026-08-04  
> 实施范围：C0、C1、C2、C4、C5  
> 延后范围：C3 Trace 分页与偏好洞察  
> 基线：当前 `DEV_SPEC.md`、ADR 0001-0026 和 `mac-local` 分支实现

## 0. 文档关系

本规格沿用 `DEV_SPEC.md` 的分层架构、任务拆分、修改文件、验收标准和测试方法格式，描述一次
定位收敛后的增量开发。发生冲突时，本规格在以下范围内优先：

1. MCP HTTP 是供上层业务系统调用的内网只读组件入口。
2. React + FastAPI 是私有管理控制台，Streamlit 退役。
3. Query Trace 的原始问题采集是明确产品需求，但必须使用匿名上下文、紧凑载荷和保留策略。
4. Trace 持久化从单文件 JSONL 迁移到独立 SQLite。
5. 持久化摄取 Job 和偏好洞察 C3 不属于本期。

实施完成后更新 `DEV_SPEC.md` 的当前架构、配置、目录树和运行说明；历史排期只标记
`Superseded`，不抹除项目演进记录。

---

## 1. 项目定位与范围

### 1.1 目标定位

本项目作为上层业务系统调用的内网 RAG/MCP 组件，提供知识查询、引用、Collection 发现、文档
摘要、查询 Trace 和健康状态。它不直接识别终端用户，也不承担公网网关职责。

```text
终端用户
   |
   v
上层业务系统
认证 / 权限 / 租户 / HTTPS / 业务限流
   |
   | 内网 Streamable HTTP
   v
MCP Server
只读 Tool / Query Trace / Health
   |
   +--> Query Engine --> Chroma / BM25 / FileIntegrity / Provider

管理员
   |
   +--> CLI / Synology Import / 私有 React Dashboard
                                      |
                                      v
                               Ingestion Pipeline
```

### 1.2 参与者

| ID | 参与者 | 目标 |
|---|---|---|
| ACT-01 | 上层业务系统 | 通过 MCP 获取知识上下文，并关联自身请求日志 |
| ACT-02 | 终端用户 | 通过上层系统间接使用知识能力，不直接访问本组件 |
| ACT-03 | 管理员 | 摄取、删除、配置、查看最近 Trace 和诊断问题 |
| ACT-04 | 运维系统 | 使用 health endpoint 判断进程是否可接收请求 |
| ACT-05 | 产品/知识运营 | 后续使用 Trace 分析客户常问问题和知识缺口 |

### 1.3 责任边界

| 能力 | 上层业务系统 | 本组件 |
|---|---:|---:|
| 用户认证、权限、租户 | 是 | 否 |
| TLS、公网防护、外部限流 | 是 | 否 |
| 匿名 actor/session 标识生成 | 是 | 校验并记录 |
| MCP 参数、响应和错误契约 | 否 | 是 |
| 查询、引用和 generation 可见性 | 否 | 是 |
| Query Trace 采集与保留 | 提供上下文 | 是 |
| 点击、点赞、满意度、转化 | 是 | 否 |
| 文档摄取和索引发布 | 触发可选 | 是 |

### 1.4 术语

| 术语 | 定义 |
|---|---|
| Public MCP payload | MCP wire response 中可交给上层业务系统的数据 |
| `actor_key` | 上层生成的稳定匿名用户关联键，不是姓名、邮箱或 Token |
| `session_id` | 上层生成的匿名会话标识 |
| `request_id` | 上层和本组件之间的单次请求关联 ID |
| `trace_id` | 本组件为一次执行生成的内部追踪 ID |
| compact Trace | 生产默认 Trace，只保存必要字段、最终结果摘要和阶段统计 |
| debug Trace | 临时诊断 Trace，可保存更多候选快照，不用于长期生产采集 |
| source label | 可公开的文件名或业务来源名，不是服务器绝对路径 |
| active generation | FileIntegrity 当前已发布、允许查询看见的文档版本 |

---

## 2. 需求规格

### 2.1 功能需求

| ID | 优先级 | 需求 |
|---|---|---|
| FR-01 | P0 | MCP `tools/list` 必须且只能返回三个只读 Tool |
| FR-02 | P0 | Tool 必须同时通过 JSON Schema 和 Python 运行时校验输入边界 |
| FR-03 | P0 | MCP 正常响应和错误响应不得暴露服务器绝对路径、秘密或堆栈 |
| FR-04 | P0 | HTTP MCP 必须接收并隔离 request/actor/session 上下文 |
| FR-05 | P0 | 每次 Query Tool 调用必须创建 compact Trace，并记录最终结果摘要 |
| FR-06 | P0 | Trace 必须持久化到独立 SQLite，支持并发短事务和进程重启读取 |
| FR-07 | P0 | 私有 Dashboard 现有 Trace API 必须从 SQLite 返回最近 200 条记录 |
| FR-08 | P0 | Trace 必须支持 90 天自动保留及按时间、actor_key 的私有 CLI 删除 |
| FR-09 | P0 | MCP HTTP 必须提供 liveness 和 readiness |
| FR-10 | P0 | Streamlit 运行时代码、依赖、启动入口和专属测试必须删除 |
| FR-11 | P1 | 管理员必须能继续使用 React Dashboard、CLI 和群晖脚本摄取数据 |
| FR-12 | P1 | Trace 写入失败不得覆盖成功查询结果，但 health 必须报告 degraded |
| FR-13 | P1 | Stdio MCP 必须保持兼容；缺少 HTTP Header 时自动生成 request_id |

### 2.2 非功能需求

| ID | 类别 | 可验证约束 |
|---|---|---|
| NFR-01 | 容量 | 单进程完成 100 个并发 fake MCP 调用，不死锁、不丢响应 |
| NFR-02 | 运行画像 | 发布验收覆盖 100 个在线会话和常态 20 个并发查询 |
| NFR-03 | 一致性 | 摄取发布期间，查询只引用完整旧 generation 或完整新 generation |
| NFR-04 | 隐私 | Trace 不记录姓名、邮箱、手机号、认证 Token、IP、完整回答和 Chunk 正文 |
| NFR-05 | 保留 | 默认保留 90 天，删除操作可重复执行且结果可审计 |
| NFR-06 | 可用性 | Trace Store 故障时查询仍返回；核心索引不可读时 readiness 返回 503 |
| NFR-07 | 成本 | readiness 不调用 Embedding、LLM 或其他付费外部请求 |
| NFR-08 | 兼容性 | CLI、Stdio MCP、现有三个 Tool 名称和核心 structuredContent 保持兼容 |
| NFR-09 | 依赖 | 不新增第三方运行时依赖；使用 Python 标准库 SQLite、ContextVar 和现有框架 |
| NFR-10 | 部署 | 本期保持单 MCP 进程，不增加 Semaphore、自定义 Executor 或多副本 |

### 2.3 延后需求

以下需求属于 C3，保留数据兼容性设计，但不计入本期完成条件：

| ID | 状态 | 内容 |
|---|---|---|
| DF-01 | 延后 | Trace 游标分页 API |
| DF-02 | 延后 | 热门问题、Collection、来源和无结果问题聚合 |
| DF-03 | 延后 | P50/P95 趋势和 React 偏好洞察页面 |
| DF-04 | 延后 | 语义相似问题聚类或 LLM 离线分析 |
| DF-05 | 延后 | 点击、点赞和满意度反馈写接口 |
| DF-06 | 延后 | 持久化摄取 Job、Job lease 和自动恢复 |

### 2.4 明确非目标

- 不实现用户登录、RBAC、ACL 和多租户。
- 不把 Dashboard API 暴露到上层业务调用网络。
- 不引入 Redis、Celery、Kafka、PostgreSQL、OpenTelemetry Collector 或服务网格。
- 不实现多进程查询、查询缓存、背压队列和自动扩容。
- 不保证 React 上传任务在 Dashboard 进程崩溃后恢复。
- 不保存或推断真实用户身份。

---

## 3. 核心使用场景

### UC-01：成功查询并写入 Trace

| 项目 | 内容 |
|---|---|
| 主要参与者 | ACT-01 上层业务系统 |
| 前置条件 | MCP ready；至少一个 active generation 可查询 |
| 触发 | 调用 `query_knowledge_hub` |
| 后置条件 | 返回引用；SQLite 中存在同 request_id/trace_id 的 compact Trace |

主流程：

1. MCP SDK 把当前 Streamable HTTP Request 放入 `ServerRequestContext.request`。
2. `server.on_call_tool()` 从该 Request 读取并校验三个关联 Header。
3. 缺失 `X-Request-ID` 时生成 UUID；actor/session 缺失时保持 `None`。
4. `ProtocolHandler` 在调用边界 set ContextVar，并使用 `asyncio.to_thread()` 执行 Query Tool。
5. Tool 从 `ContextVar` 读取请求上下文，构造 `QueryRequest.request_id` 和 `TraceContext`。
6. `KnowledgeService` 执行 Query Processing、Dense/Sparse、Fusion、Filter、Rerank 和 Response。
7. Tool 将最终 Top-K 摘要和状态写入 Trace。
8. SQLiteTraceStore 在短事务中写入一行。
9. MCP 返回 sanitized response，其中包含 `request_id` 和 `trace_id`。
10. `ProtocolHandler` 在 `finally` 中 reset ContextVar。

### UC-02：查询无结果

1. 检索链路正常完成，但最终候选为空。
2. MCP 返回成功 Tool result，答案为“未找到相关知识库内容”。
3. Trace 保存 `status=success`、`result_count=0`、`zero_result=1`。
4. 不得把无结果记为系统异常。

### UC-03：查询失败或 Trace 写失败

查询失败：

1. 所有召回路线失败或应用服务抛出异常。
2. 私有 stderr 日志记录完整异常。
3. wire response 只返回稳定 `query_failed` 或 `internal_error`。
4. 能写 Trace 时保存失败状态和安全错误码，不保存原始异常文本。

Trace 写失败：

1. 查询和响应构建已经成功。
2. Collector 捕获 SQLite 错误并写 warning。
3. MCP 仍返回成功查询结果。
4. `/health/ready` 的 Trace check 返回 `degraded`，核心 ready 状态保持 200。

### UC-04：健康检查

`/health/live`：

- 不加载索引，不访问 Provider，返回进程状态。

`/health/ready`：

1. 加载并校验 settings。
2. 构造或复用本地 KnowledgeService，验证 Chroma/BM25/FileIntegrity 可读。
3. 对 Trace DB 执行可回滚的短写事务。
4. 核心检查失败返回 503；只有 Trace 写检查失败返回 200 degraded。

### UC-05：管理员摄取期间持续查询

1. 管理员通过 CLI 或群晖脚本摄取 V2。
2. V2 写入带新 generation 的 Chroma、BM25 和 Image Store 记录。
3. 发布前查询继续使用 V1 active generation。
4. FileIntegrity 原子发布 V2。
5. 发布后新查询只使用 V2。
6. 任意一次响应不得混合 V1/V2 的同一文档 Chunk。

### UC-06：清理 Trace

1. 管理员执行 `scripts/purge_traces.py`，必须提供 `--before` 或 `--actor-key`。
2. CLI 展示匹配条件和删除条数，不输出 query 正文。
3. 重复执行返回删除 0 条并成功退出。
4. 自动保留清理和人工清理使用同一 Store 方法。

---

## 4. 架构与模块设计

### 4.1 组件关系

```text
HTTP Request
    |
    v
MCP SDK Streamable HTTP App
    |
    v
server.on_call_tool(ServerRequestContext.request.headers)
    |
    v
ProtocolHandler --> ContextVar<RequestContext>
    | asyncio.to_thread
    v
QueryKnowledgeHubTool
    |---------------------> KnowledgeService
    |                           |
    |                           v
    |                      Query Engine
    |                           |
    |                           v
    |                     QueryResponse
    |
    +--> TraceContext --> SQLiteTraceStore --> traces.db
    |
    v
ResponseBuilder --> sanitized MCP result
```

私有管理读路径：

```text
React Query Trace Page
        |
        v
GET /api/traces/{trace_type}
        |
        v
TraceService --> SQLiteTraceStore.list_recent(limit=200)
```

### 4.2 模块职责

| 模块 | 职责 | 不负责 |
|---|---|---|
| `http_server.py` | HTTP app 和 health route | Tool 业务逻辑 |
| `server.py` | 从 MCP SDK request context 提取当前 HTTP Header | 用户认证 |
| `request_context.py` | Header 校验和匿名请求上下文模型 | 用户认证 |
| `protocol_handler.py` | MCP 调用路由、ContextVar 生命周期、协议错误映射、线程切换 | Provider 细节 |
| `tools/*.py` | 参数适配、Trace 生命周期、公开响应 | 存储实现 |
| `KnowledgeService` | 查询、目录和响应领域能力 | HTTP、Header |
| `TraceContext` | 内存阶段与耗时 | 持久化策略 |
| `SQLiteTraceStore` | Schema、写入、最近读取、清理、健康检查 | UI 聚合分析 |
| `TraceService` | SQLite payload 到 Dashboard 只读模型 | Trace 写入 |
| Dashboard API | 私有管理 API | 上层业务调用 |

### 4.3 依赖方向

```text
MCP / Dashboard adapters
          |
          v
Application services and core contracts
          |
          v
SQLite / Chroma / BM25 adapters
```

`QueryEngine`、`Pipeline` 和 Provider 不得依赖 FastAPI、Header、Dashboard 或具体 Trace DB。

---

## 5. 外部接口契约

### 5.1 MCP Tool 清单

`tools/list` 的顺序和名称固定为：

```json
[
  "query_knowledge_hub",
  "list_collections",
  "get_document_summary"
]
```

不得注册 ingestion、delete、evaluation、configuration 或 trace management Tool。

### 5.2 输入边界

| 常量 | 值 | 使用位置 |
|---|---:|---|
| `MAX_QUERY_CHARS` | 4000 | Query JSON Schema 和运行时 |
| `MAX_TOP_K` | 20 | Query JSON Schema 和运行时 |
| `MAX_IDENTIFIER_CHARS` | 128 | collection、doc_id、Header |

`query_knowledge_hub` Schema 核心约束：

```json
{
  "query": {"type": "string", "minLength": 1, "maxLength": 4000},
  "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
  "collection": {"type": "string", "minLength": 1, "maxLength": 128}
}
```

运行时在 trim 后再次校验。仅由空白构成的 query、collection 或 doc_id 必须拒绝。

### 5.3 Query 成功响应

```json
{
  "content": [
    {"type": "text", "text": "..."}
  ],
  "structuredContent": {
    "answer": "...",
    "citations": [
      {
        "id": "citation-id",
        "source": "guide.pdf",
        "doc_key": "stable-doc-key",
        "page": 3,
        "chunk_id": "chunk-id",
        "score": 0.91,
        "text": "retrieved citation text",
        "metadata": {
          "collection": "docs",
          "title": "Guide"
        }
      }
    ],
    "request_id": "upstream-request-id",
    "trace_id": "component-trace-id",
    "metadata": {
      "collection": "docs",
      "candidate_count": 5,
      "image_count": 0
    }
  }
}
```

公开 citation metadata 使用白名单。不得透传任意内部 metadata、绝对 `source_path`、图片磁盘路径
或 generation claim token。文档摘要响应遵守同一规则。

### 5.4 错误契约

| 场景 | MCP 表达 | 稳定 component code |
|---|---|---|
| 参数非法 | JSON-RPC `INVALID_PARAMS` | `invalid_params` |
| Tool 不存在 | JSON-RPC `METHOD_NOT_FOUND` | `tool_not_found` |
| 文档不存在 | `CallToolResult.isError=true` | `document_not_found` |
| 查询链路失败 | JSON-RPC server error | `query_failed` |
| 未分类内部错误 | JSON-RPC internal error | `internal_error` |

wire error 的 `message` 使用稳定文本，`data` 只包含 Tool 名和 component code。原始异常仅写 stderr。

### 5.5 HTTP Header 契约

| Header | 必填 | 缺失行为 | 校验 |
|---|---:|---|---|
| `X-Request-ID` | 否 | 生成 UUID | trim 后 1..128 个可打印字符 |
| `X-RAG-Actor-Key` | 否 | `None` | trim 后 1..128 个可打印字符 |
| `X-RAG-Session-ID` | 否 | `None` | trim 后 1..128 个可打印字符 |

Header 中出现控制字符、空白值或超长值时，返回 MCP `INVALID_PARAMS`，不进入 Tool。组件不信任
并且不解析 actor_key 的业务含义。

Stdio MCP 没有 Header：每次 Query Tool 调用生成 request_id，actor/session 保持 `None`。

### 5.6 私有 Trace API 兼容契约

本期保留现有路径：

```text
GET /api/traces/query
GET /api/traces/ingestion
```

返回结构保持：

```json
{
  "trace_type": "query",
  "traces": [],
  "malformed_line_count": 0
}
```

行为变化：数据源改为 SQLite；按 `started_at DESC, trace_id DESC` 返回最近 200 条；非法
`trace_type` 返回 400。`malformed_line_count` 为兼容 React 类型暂时固定为 0。

本期不得新增 cursor、days 或 insights 参数。

### 5.7 Health 契约

`GET /health/live`：

```json
{"status": "ok"}
```

`GET /health/ready` 正常：

```json
{
  "status": "ready",
  "checks": {
    "settings": "ok",
    "knowledge_store": "ok",
    "trace_store": "ok"
  }
}
```

Trace 降级时 HTTP 200：

```json
{
  "status": "degraded",
  "checks": {
    "settings": "ok",
    "knowledge_store": "ok",
    "trace_store": "unwritable"
  }
}
```

核心不可用时 HTTP 503，响应不得包含底层异常文本。

---

## 6. Trace 设计

### 6.1 Trace 生命周期

```text
server.on_call_tool() reads SDK request headers
        |
        v
ProtocolHandler.set(ContextVar)
        |
        v
Query Tool creates TraceContext(detail=compact)
        |
        v
Query stages record timings and counts
        |
        v
Tool adds final result summaries and stable status
        |
        v
SQLiteTraceStore.collect()
        |
        +--> success: return response with trace_id
        |
        +--> failure: warning + degraded health, still return query response
        |
        v
ProtocolHandler.reset(ContextVar)
```

### 6.2 Compact Query Trace 字段

顶层列和 `payload_json` 必须包含以下稳定字段：

```json
{
  "schema_version": 1,
  "trace_id": "uuid",
  "trace_type": "query",
  "started_at": "UTC ISO-8601",
  "finished_at": "UTC ISO-8601",
  "total_elapsed_ms": 123.45,
  "metadata": {
    "request_id": "request-id",
    "actor_key": "anonymous-key",
    "session_id": "session-id",
    "query": "raw customer question",
    "query_normalized": "normalized customer question",
    "collection": "docs",
    "top_k": 5,
    "status": "success",
    "error_code": null,
    "result_count": 2,
    "zero_result": false,
    "results": [
      {
        "rank": 1,
        "doc_key": "doc-key",
        "chunk_id": "chunk-id",
        "title": "Guide",
        "score": 0.91
      }
    ]
  },
  "stages": []
}
```

规范化 query 仅执行：Unicode 原文保留、首尾 trim、连续空白折叠、`casefold()`。不做分词、同义词
归并、脱敏猜测或 LLM 改写。

### 6.3 Compact 与 Debug

| 内容 | compact | debug |
|---|---:|---:|
| 原始 query | 是 | 是 |
| 最终 Top-K 标识和分数 | 是 | 是 |
| 阶段耗时、状态、数量 | 是 | 是 |
| Dense/Sparse/Fusion 全部候选 | 否 | 最多每阶段 20 条 |
| 原始异常文本 | 否 | 私有阶段可记录 |
| Chunk 正文、完整回答、Token | 否 | 否 |

生产默认 `compact`。切换 `debug` 只用于短期诊断，不改变 SQLite Schema。

### 6.4 SQLite Schema

数据库路径：`./data/db/traces.db`。

```sql
CREATE TABLE IF NOT EXISTS trace_event (
    trace_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL DEFAULT 1,
    trace_type TEXT NOT NULL,
    request_id TEXT,
    actor_key TEXT,
    session_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    total_elapsed_ms REAL NOT NULL CHECK (total_elapsed_ms >= 0),
    status TEXT NOT NULL,
    error_code TEXT,
    query_text TEXT,
    query_normalized TEXT,
    collection TEXT,
    top_k INTEGER CHECK (top_k IS NULL OR top_k > 0),
    result_count INTEGER CHECK (result_count IS NULL OR result_count >= 0),
    zero_result INTEGER CHECK (zero_result IS NULL OR zero_result IN (0, 1)),
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trace_type_started
ON trace_event(trace_type, started_at DESC, trace_id DESC);

CREATE INDEX IF NOT EXISTS idx_trace_started
ON trace_event(started_at);

CREATE INDEX IF NOT EXISTS idx_trace_actor_started
ON trace_event(actor_key, started_at DESC, trace_id DESC);

CREATE INDEX IF NOT EXISTS idx_trace_query_started
ON trace_event(query_normalized, started_at DESC, trace_id DESC);
```

数据库约束：

- `PRAGMA journal_mode=WAL`。
- `PRAGMA busy_timeout=5000`。
- 每个操作建立独立连接并关闭，不在线程间共享 connection。
- Schema 使用 `CREATE ... IF NOT EXISTS` 和 `PRAGMA user_version=1` 自举。
- 本期不引入迁移框架；后续 schema 变化必须先增加显式 user_version migration。

### 6.5 Store 接口

`TraceCollector` 保持入口依赖的最小 Protocol：

```python
class TraceCollector(Protocol):
    def collect(self, trace: TraceContext) -> None: ...
```

新增 `SQLiteTraceStore`：

```python
class SQLiteTraceStore:
    def collect(self, trace: TraceContext) -> None: ...
    def list_recent(self, trace_type: str, limit: int = 200) -> list[JsonDict]: ...
    def purge(self, *, before: datetime | None, actor_key: str | None) -> int: ...
    def purge_expired(self, now: datetime | None = None) -> int: ...
    def check_writable(self) -> bool: ...
```

`collect()` 在序列化和字段验证全部完成后才打开写事务。相同 `trace_id` 重复写入必须失败并记录
warning，不得静默覆盖审计记录。

### 6.6 保留与删除

- 默认 `retention_days=90`，允许配置 `1..3650`。
- Store 启动时清理一次；长进程中每天最多再次清理一次。
- 自动清理条件为 `started_at < now - retention_days`。
- 人工 CLI 要求至少提供 `--before` 或 `--actor-key`，两者同时提供时使用 AND。
- 删除后不在请求路径执行 `VACUUM`；SQLite 后续复用空闲页。
- CLI 只输出条件、匹配数和删除数，不输出 query 内容。

### 6.7 隐私与数据治理

允许记录：匿名 actor/session、原始问题、Collection、最终结果标识、阶段统计。

禁止记录：

- Authorization/Cookie/API Key；
- 姓名、邮箱、手机号和 IP；
- 完整生成答案和 Chunk 正文；
- 本地绝对路径；
- claim token、Provider 密钥和完整异常堆栈。

上层系统必须在传入 `actor_key` 前完成匿名化。组件无法判断一个字符串是否真实身份，因此该责任
属于部署契约和接入评审，而不是在线猜测或正则脱敏。

### 6.8 与现有 ADR 的关系

- ADR-0015 中“不记录原始查询”的结论被本规格部分替代。原因是客户问题偏好已成为明确需求；
  新控制措施是匿名关联、字段最小化、私有访问和 90 天保留。
- ADR-0018 的 Trace 生命周期和 best-effort 原则继续有效；JSONL 存储和全文件读取被 SQLite
  Store 和最近 200 条查询替代。
- 新增 ADR 记录上述变更，不直接删除历史 ADR。

---

## 7. 配置规格

`config/settings.yaml`：

```yaml
observability:
  enabled: true
  db_path: ./data/db/traces.db
  retention_days: 90
  detail: compact
```

校验规则：

| 配置 | 允许值 | 默认 |
|---|---|---|
| `enabled` | boolean | `true` |
| `db_path` | 非空路径 | `./data/db/traces.db` |
| `retention_days` | integer `1..3650` | `90` |
| `detail` | `compact/debug` | `compact` |

旧 `observability.log_file` 在本次切换后删除。现有 JSONL 文件由管理员上线前按需归档，不双写、
不自动导入。`dashboard.traces_dir` 等只服务旧页面的配置同步删除或改为使用 `db_path`。Trace
最近读取上限使用代码常量 `RECENT_TRACE_LIMIT=200`，不增加无实际变化需求的配置项。

`enabled=false` 只允许测试和显式关闭观测的本地入口使用。生产 MCP 以 Trace 为产品能力，关闭后
`/health/ready` 必须返回 `200 + degraded`。

---

## 8. 容量与一致性策略

### 8.1 单进程决策

当前 `ProtocolHandler` 通过 `asyncio.to_thread()` 运行同步 Tool。本机默认线程池为 14；100 个
在线连接不会等于 100 个同时执行的查询。请求高峰由现有线程池排队，本期先验证而不优化。

本期不增加：

- `asyncio.Semaphore`；
- 自定义 `ThreadPoolExecutor`；
- 应用内有界队列；
- 多 Uvicorn worker；
- 多实例和共享 Trace Store。

### 8.2 验收画像

自动化测试：

- FakeKnowledgeService 下 100 个并发 Tool 调用全部完成。
- 健康 Trace DB 下产生 100 个唯一 trace_id，无 `database is locked`。
- request/actor/session 上下文一一匹配，不跨请求污染。

发布前受控验证：

- 100 个 MCP 会话完成 initialize/tools/list。
- 20 个查询并发运行 5 分钟。
- 记录成功率、超时率、P50/P95、Provider 429 和进程 CPU。

本期不设置脱离目标机器和 Provider 配额的绝对延迟承诺。出现以下事实再建立并发优化规格：

- 超时率超过 1%；
- Provider 持续 429；
- P95 超过上层业务目标；
- CPU 长期超过 80%；
- 线程池排队成为已测量瓶颈。

### 8.3 读写一致性

沿用现有 generation fence，不新增全局读写锁。验收必须证明：

- 未发布 generation 不参与 Dense、Sparse 或 Catalog 结果；
- 发布动作完成前查询仍返回旧版；
- 发布完成后新查询返回新版；
- 单次响应不混合同一 doc_key 的多个 generation。

---

## 9. 实施排期

### 9.1 进度表

| 编号 | 任务 | 状态 | 提交 | 前置 |
|---|---|---|---|---|
| C0 | 删除 Streamlit，固定组件边界 | [x] | c70c4e8 | 无 |
| C1 | MCP 输入、输出和只读契约 | [x] | ad85c8a | C0 |
| C2.1 | HTTP 请求上下文 | [x] | 8802a16 | C1 |
| C2.2 | SQLite Trace Store | [x] | 2f80b4e | C2.1 |
| C2.3 | Compact Query Trace 集成 | [x] | 4c98e0a | C2.2 |
| C2.4 | Dashboard 最近 Trace 与清理 CLI | [x] | 8838d9e | C2.3 |
| C3 | Trace 分页与偏好洞察 | [延后] | — | C2.4 |
| C4 | MCP Health | [x] | 0abe524 | C2.2 |
| C5 | 容量、一致性、文档和全量回归 | [部分] | 8a20728 / bd084ca / f09c498 | C2.4、C4 |

C5 落地范围：评估语料 + recall 测试 + 数据集 README 已完成（commit
8a20728 / bd084ca / f09c498）；`scripts/smoke_mcp_load.py`、README /
DEV_SPEC 同步、ADR 0027 仍待办，本地无部署目标故跳过受控负载脚本。

### C0：删除 Streamlit，固定组件边界

**目标**：移除重复 UI 和后台入口，保留只读 MCP 与私有 React 管理台。

**删除文件**：

- `src/observability/dashboard/app.py`
- `src/observability/dashboard/pages/`
- `scripts/start_dashboard.py`
- `tests/e2e/test_dashboard_smoke.py`
- Streamlit 页面专属 integration tests

**修改文件**：

- `pyproject.toml`：删除 Streamlit 依赖和 Mypy override。
- `src/observability/dashboard/api.py`：使用 FastAPI lifespan 正常关闭当前 Job Service。
- `src/observability/dashboard/services/ingestion_job_service.py`
- `src/observability/dashboard/services/evaluation_service.py`
- `src/observability/dashboard/_ingestion_helpers.py`：删除 Streamlit 专属说明。
- `README.md`、`DEV_SPEC.md`、ADR 0017/0023/0026。

**实现要点**：

- 不删除 Dashboard API、共享 Service、React `web/` 和摄取 helper。
- 旧 AppTest 中仍属于 API/Service 契约的断言迁入现有 pytest，不做页面一比一翻译。
- ADR 只增加 `Superseded` 标记。

**验收标准**：

- `rg -n "streamlit|Streamlit" src scripts tests pyproject.toml` 无运行时命中。
- `scripts/start_dashboard_api.py` 和 MCP HTTP 入口可独立启动。
- React build/lint 与 Dashboard API 测试通过。

**测试方法**：

```bash
pytest -q tests/unit tests/integration/test_dashboard_api_integration.py
ruff check src scripts tests
python -m mypy src scripts
cd web && npm run build && npm run lint
```

### C1：MCP 输入、输出和只读契约

**目标**：固定上层业务系统可依赖的 Tool、参数、公开字段和安全错误。

**修改文件**：

- `src/mcp_server/tools/base.py`
- `src/mcp_server/tools/query_knowledge_hub.py`
- `src/mcp_server/tools/get_document_summary.py`
- `src/mcp_server/protocol_handler.py`
- `src/mcp_server/server.py`
- `src/core/response/response_builder.py`
- 对应 Tool、ResponseBuilder、MCP 集成测试

**实现类/函数**：

- `MAX_QUERY_CHARS/MAX_TOP_K/MAX_IDENTIFIER_CHARS`。
- 参数校验 helper，供 Schema 常量和运行时使用。
- `ToolExecutionError(component_code)`，只携带安全公开码。
- `public_source_label()` 和公开 citation metadata 白名单。
- `ProtocolHandler.handle_tools_call()` 的稳定错误映射。

**验收标准**：

- `tools/list` 精确等于三个只读 Tool。
- 4000/20/128 边界值成功，超一单位稳定失败。
- whitespace-only、boolean top_k、未知字段和超长 Unicode 输入均被拒绝。
- 使用 `/Users/private/secret.pdf` fixture 时，wire payload 只出现 `secret.pdf`。
- 任意异常响应不包含 `MINIMAX_API_KEY`、绝对路径或 traceback。

**测试方法**：

```bash
pytest -q tests/unit/test_query_knowledge_hub_tool.py \
  tests/unit/test_get_document_summary.py \
  tests/unit/test_response_builder.py \
  tests/integration/test_mcp_server.py
```

### C2.1：HTTP 请求上下文

**目标**：把上层匿名关联信息安全传到查询线程，不污染 MCP Tool Schema。

**新增文件**：

- `src/mcp_server/request_context.py`
- `tests/unit/test_mcp_request_context.py`
- `tests/integration/test_mcp_http_server.py`

**修改文件**：

- `src/mcp_server/server.py`
- `src/mcp_server/protocol_handler.py`
- `src/mcp_server/tools/query_knowledge_hub.py`

**实现类/函数**：

- `RequestContext(request_id, actor_key, session_id)` frozen dataclass。
- `current_request_context: ContextVar[RequestContext | None]`。
- `request_context_from_headers()`：从 MCP SDK 当前 Request 的 Header 构造上下文。
- `ProtocolHandler.handle_tools_call(..., request_context=...)`：set、to_thread、finally reset。
- `get_request_context()`：HTTP 返回当前上下文，Stdio 返回生成的默认上下文。

**验收标准**：

- 缺少 Header 时 request_id 非空且每次调用不同。
- 128 字符通过，129 字符、控制字符和空白 Header 返回 MCP `INVALID_PARAMS`。
- 两个并发请求在 `asyncio.to_thread()` 中读取各自 actor/session，不串值。
- Tool 成功和异常路径都 reset ContextVar。

**测试方法**：

```bash
pytest -q tests/unit/test_mcp_request_context.py \
  tests/integration/test_mcp_http_server.py
```

### C2.2：SQLite Trace Store

**目标**：建立并发可写、重启可读、可清理的 Trace 单一事实源。

**新增文件**：

- `src/core/trace/sqlite_trace_store.py`
- `tests/unit/test_sqlite_trace_store.py`

**修改文件**：

- `src/core/trace/trace_collector.py`：改为最小 Protocol。
- `src/core/trace/__init__.py`
- `src/observability/ingestion_trace.py`：工厂根据 settings 构建 SQLite Store。
- `src/core/settings.py`、`config/settings.yaml`
- TraceContext/Collector 相关单元测试

**实现类/函数**：

- `SQLiteTraceStore.__init__()`：路径、retention、detail、Schema 自举。
- `collect()`：验证、序列化、单事务 insert。
- `list_recent()`：类型过滤、稳定倒序、limit 上限。
- `purge()/purge_expired()`：参数化 DELETE。
- `check_writable()`：BEGIN IMMEDIATE、无副作用 UPDATE、ROLLBACK。

**验收标准**：

- Store 重建后 Trace 完整可读。
- 100 个线程写 100 条唯一 Trace，无锁错误和丢行。
- 负耗时、非法 JSON 字段和重复 trace_id 被拒绝。
- `retention_days` 边界校验生效；过期与未过期记录区分正确。
- Trace DB 与 ingestion integrity DB 使用不同路径。

**测试方法**：

```bash
pytest -q tests/unit/test_sqlite_trace_store.py \
  tests/unit/test_trace_context.py \
  tests/unit/test_ingestion_trace.py
```

### C2.3：Compact Query Trace 集成

**目标**：在真实 Query Tool 生命周期中写入匿名上下文、原始问题、最终结果和阶段统计。

**修改文件**：

- `src/core/trace/trace_context.py`
- `src/core/query_engine/hybrid_search.py`
- `src/mcp_server/tools/query_knowledge_hub.py`
- `src/core/response/response_builder.py`
- Query Trace、Hybrid Search、Tool 测试

**实现要点**：

- TraceContext 增加 `detail=compact/debug`。
- compact 阶段删除候选数组，只保留 method/provider/status/count/elapsed。
- Tool 在最终响应后写入最多 20 个 sanitized result summary。
- 使用 `dataclasses.replace()` 把 trace_id 放入 QueryResponse metadata，不修改 frozen 对象。
- 查询异常只写 `error_code`；原始异常交给 stderr logger。
- Trace collect 继续 best-effort，不修改业务结果。

**验收标准**：

- success、zero result、单路降级、全部路线失败各产生正确 Trace。
- QueryResponse 的 request_id/trace_id 与 SQLite 行一致。
- compact payload 不含候选正文、完整回答、本地路径和异常文本。
- debug 最多保留每阶段 20 个候选，且仍不保存正文和秘密。
- Collector 故障时成功查询仍成功，warning 可观察。

**测试方法**：

```bash
pytest -q tests/unit/test_query_trace.py \
  tests/unit/test_query_knowledge_hub_tool.py \
  tests/integration/test_hybrid_search.py \
  tests/integration/test_mcp_server.py
```

### C2.4：Dashboard 最近 Trace 与清理 CLI

**目标**：替换 JSONL 读取，同时保持现有 React Trace 页面可用。

**新增文件**：

- `scripts/purge_traces.py`
- `tests/unit/test_purge_traces.py`

**修改文件**：

- `src/observability/dashboard/services/trace_service.py`
- `src/observability/dashboard/api.py`
- `src/observability/logger.py`：保留 stderr logger，删除未使用的 JSONL Trace writer。
- `tests/unit/test_dashboard_trace_service.py`
- `tests/unit/test_jsonl_logger.py`：删除 JSONL Trace writer 专属用例。
- `tests/integration/test_dashboard_api_integration.py`

**实现类/函数**：

- `TraceService.read_traces(trace_type, limit=200)` 从 SQLite payload 构建只读模型。
- API 只接受 `query/ingestion`，limit 固定使用配置上限。
- `purge_traces.py --before/--actor-key/--settings`。

**验收标准**：

- Dashboard API 返回最近 200 条且顺序稳定。
- `malformed_line_count=0`，React 现有类型和页面无需新增功能。
- 非法 trace_type 返回 400。
- purge 缺少条件时退出码非 0；按 actor、时间和组合条件删除正确。
- CLI 输出不包含原始 query。

**测试方法**：

```bash
pytest -q tests/unit/test_dashboard_trace_service.py \
  tests/unit/test_purge_traces.py \
  tests/integration/test_dashboard_api_integration.py
cd web && npm run build && npm run lint
```

### C3：Trace 分页与偏好洞察

> 状态：延后，不计入本期完成条件。

未来范围：

- `GET /api/traces/{trace_type}?limit=&cursor=`；
- `GET /api/traces/query/insights?days=`；
- 热门问题、Collection、来源、zero-result、P50/P95；
- React 聚合视图。

本期 Schema 已保留 `query_normalized/collection/result_count/payload_json`，C3 不得要求重新采集
历史 Query。

### C4：MCP Health

**目标**：让上层或运维系统区分进程存活、核心可用和 Trace 降级。

**新增文件**：

- `src/mcp_server/readiness.py`
- `tests/unit/test_mcp_readiness.py`

**修改文件**：

- `src/mcp_server/http_server.py`
- `src/mcp_server/server.py`：复用默认 KnowledgeService 装配，不触发外部请求。
- `tests/integration/test_mcp_http_server.py`

**实现类/函数**：

- `ReadinessService.check() -> ReadinessResult`。
- `live()` 和 `ready()` Starlette route。
- `build_app(knowledge_service=None, trace_store=None)` 测试注入点。

**验收标准**：

- live 不构建 KnowledgeService。
- ready 验证本地配置和存储，但 mock 证明没有调用 LLM/Embedding 网络方法。
- 核心 Store 错误返回 503 和稳定检查码。
- Trace Store 不可写返回 200 degraded。
- 健康响应不包含路径和异常消息。

**测试方法**：

```bash
pytest -q tests/unit/test_mcp_readiness.py \
  tests/integration/test_mcp_http_server.py
```

### C5：容量、一致性、文档和全量回归

**目标**：以目标运行画像完成发布级验收，并更新事实文档。

**新增文件**：

- `tests/e2e/test_readonly_component.py`
- `scripts/smoke_mcp_load.py`
- `docs/decisions/0027-readonly-component-and-preference-trace.md`

**修改文件**：

- `README.md`
- `DEV_SPEC.md`
- 本规格进度表
- 相关 ADR 的 Superseded 标记

**自动化场景**：

1. 100 个并发 fake MCP 查询返回 100 个响应和 100 条正确 Trace。
2. Header 上下文在并发下不串值。
3. 查询期间摄取 V2，响应 generation 一致。
4. Trace DB 不可写时查询成功、ready degraded。
5. 核心索引损坏时 ready 503 且 wire 不泄漏异常。

**受控发布验证**：

```bash
python scripts/smoke_mcp_load.py \
  --url http://127.0.0.1:8766/mcp \
  --sessions 100 \
  --concurrency 20 \
  --duration 300
```

脚本输出请求数、成功率、超时率、P50/P95 和错误码分布，不修改服务端并发配置。

**全量测试方法**：

```bash
pytest -q
ruff check src scripts tests
python -m mypy src scripts
cd web && npm run build && npm run lint
```

---

## 10. 测试与需求追踪

### 10.1 测试层次

| 层次 | 重点 |
|---|---|
| 单元测试 | Header、输入边界、compact payload、SQLite CRUD、purge、health 判定 |
| 集成测试 | MCP SDK、ContextVar 到线程、Dashboard API、SQLite 重建 |
| E2E | HTTP MCP、100 并发 fake、摄取发布期间查询 |
| 发布验证 | 目标机器、真实 Provider、100 会话和 20 并发运行画像 |
| 静态检查 | Ruff、Mypy、TypeScript、ESLint |

### 10.2 需求到测试映射

| 需求 | 主要测试 |
|---|---|
| FR-01/02/03 | Tool、ResponseBuilder、MCP contract tests |
| FR-04/13 | RequestContext unit + MCP HTTP integration |
| FR-05/12 | Query Trace success/failure/collector failure tests |
| FR-06/08 | SQLite Store concurrency/retention/purge tests |
| FR-07 | Dashboard TraceService/API tests |
| FR-09 | Readiness unit + HTTP integration |
| FR-10/11 | Dashboard API、React build、CLI tests |
| NFR-01/02 | E2E fake concurrency + controlled load smoke |
| NFR-03 | Query while generation publish integration test |
| NFR-04/05 | Payload whitelist + purge/retention tests |
| NFR-06/07 | Trace degraded/core unavailable/provider-not-called tests |

### 10.3 必测边界

- `3999/4000/4001` 字符 query。
- `top_k=0/1/20/21/True`。
- identifier `127/128/129` 字符和 whitespace-only。
- Header 缺失、空白、控制字符、Unicode、并发隔离。
- SQLite 空库、重启、重复 ID、锁竞争、过期边界和不可写目录。
- 查询 success、zero result、单路降级、全路失败、Trace 写失败。
- active generation 在 publish 前后切换。
- readiness 的 ready/degraded/not_ready 三种状态。

---

## 11. 数据切换与回滚

### 11.1 切换步骤

1. 停止 MCP HTTP 和 Dashboard API，避免 JSONL 继续写入。
2. 按需归档 `logs/traces.jsonl`；本期不导入历史数据。
3. 部署 C2 Schema 和 `observability.backend=sqlite` 配置。
4. 启动 MCP，调用 `/health/ready`，确认 Trace Store 为 ok。
5. 执行一条测试查询，确认 response trace_id 与 `traces.db` 一致。
6. 启动私有 Dashboard，确认最近 Trace 可见。
7. 执行 C5 smoke 和全量回归。

### 11.2 回滚

- 回滚应用和配置到旧 JSONL 版本；新 `traces.db` 保留但不读取。
- 不把 SQLite 数据反向导出到 JSONL。
- 回滚不影响 Chroma、BM25、FileIntegrity 和 ingestion 数据。
- C0 删除 Streamlit 后不提供 Streamlit 回滚；React + FastAPI 是正式管理入口。

---

## 12. 风险与控制

| 风险 | 影响 | 控制 |
|---|---|---|
| 上层误传真实身份 | Trace 隐私风险 | 接入契约、匿名 actor_key、私有 Dashboard、删除 CLI |
| 原始 query 包含敏感业务内容 | 数据泄露 | 最小字段、90 天保留、严格管理网访问 |
| SQLite 并发写锁 | Trace 丢失 | WAL、5 秒 busy timeout、短事务、100 并发测试 |
| Trace DB 不可写 | 偏好数据缺失 | warning、ready degraded、查询不受影响 |
| Trace 体积增长 | 磁盘占用 | compact 模式、90 天清理、不存正文 |
| ContextVar 串请求 | 错误归因 | token reset、线程复制和 100 并发隔离测试 |
| Source path 泄漏 | 内部拓扑暴露 | wire 白名单、source label、回归 fixture |
| Health 调用 Provider | 费用和错误摘流 | 只构建本地组件，测试断言网络方法未调用 |
| 单进程突发排队 | 延迟上升 | 先测量；达到升级阈值再加背压 |
| C3 延后后缺少聚合视图 | 暂时只能看最近明细 | 保证字段完整，后续无需重采历史数据 |

---

## 13. 完成定义

本期只有满足以下全部条件才可标记完成：

1. Streamlit 运行时代码、依赖、启动入口和专属测试已删除。
2. 业务部署只暴露三个只读 MCP Tool，Dashboard 保持私有。
3. Tool 输入边界、公开字段白名单和稳定错误契约有自动化证据。
4. HTTP request/actor/session 上下文在并发和线程切换下不串值。
5. 每个健康 Query 都写入一条 compact SQLite Trace，并返回对应 trace_id。
6. Trace 有 90 天保留、按时间/actor 删除和 degraded 可观察性。
7. 私有 React 页面能查看最近 200 条 Query/Ingestion Trace。
8. MCP live/ready 符合 ready、degraded、not_ready 契约。
9. 100 个并发 fake 查询通过；目标环境 100 会话、20 并发 smoke 有记录结果。
10. 摄取发布期间查询不出现同一文档 generation 混合。
11. `pytest`、Ruff、Mypy、React build/lint 全部通过。
12. README、DEV_SPEC、ADR 和配置示例与实际实现一致。

C3 的游标分页、聚合 API 和偏好洞察页面不属于上述完成条件。

---

## 14. 后续升级触发器

| 观测事实 | 后续能力 |
|---|---|
| P95 不达标、超时率 >1%、持续 Provider 429 | Semaphore、有界队列、显式 Executor |
| CPU 长期 >80% 或单实例故障不可接受 | 多只读实例和索引分发 |
| 多实例需要共享 Trace | 外部日志或分析存储，不继续扩展本地 SQLite |
| 产品需要趋势和问题偏好视图 | 实施 C3，不修改在线采集链路 |
| 需要语义主题 | 离线聚类或 LLM 批处理，不放进查询链路 |
| 需要点击、点赞和满意度 | 上层记录反馈，通过 request_id/trace_id 离线关联 |
| 管理员写入频繁且必须恢复 | 单独启动持久化摄取 Job 规格 |
