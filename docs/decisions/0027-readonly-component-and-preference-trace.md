# ADR 0027: Production Read-Only Component, Compact Payload, and SQLite Trace Store

> **状态**：部分被 ADR 0028 承接；查询与可观测性决策继续采用，MCP 只读边界已变更
> **日期**：2026-08-06

## Context

C0 之前的项目以「本地优先的演示与学习工具」为定位。Streamlit Dashboard 持有
单一长会话，RAG 索引本地 Chroma/BM25 自洽，但有四个差距让上层业务系统无法
把它当内网只读组件直接接进来：

1. **MCP 仅支持 stdio** — 远程 Agent 必须本地子进程接入，无法走内网 HTTP。
2. **Trace 落 JSONL 文件** — `logs/traces.jsonl` 单进程追加，并发写不安全；上层
   想按 `X-RAG-Actor-Key` / `X-RAG-Session-ID` 关联自身日志时无法做高效查询。
3. **Query Trace 写完整候选正文与生成文本** — 隐私边界模糊（§6.7 禁止项），且
   磁盘增长不可控。
4. **没有 Health 端点** — 运维系统无法区分进程存活、核心可用、Trace 降级三种
   状态；调用 Provider 评估 readiness 又会把费用 / 错误摘流进来。

plan 0001（docs/plans/0001）按 C0 → C4 顺序把这四个差距合并解决，并补一份
小规模可重复的评估数据集给后续优化提供回归基线。

## Decision

### C0 — Streamlit 退役

- 删除 `src/observability/dashboard/app.py` 与 `src/observability/dashboard/pages/`
  目录、Streamlit 专属 integration / e2e 测试、`scripts/start_dashboard.py`。
- 私有管理控制台改为 React + FastAPI（`web/` + `src/observability/dashboard/api.py`），
  沿用原「六页面拆解」。
- 旧的 background-job / lease-heartbeat 文档（ADR 0026）标记 Superseded，
  新模型走 FastAPI lifespan 关停 JobService。

### C1 — MCP Tool 输入 / 输出 / 错误契约

- 集中 `src/mcp_server/tools/base.py`，定义 `MAX_QUERY_CHARS=4000`、
  `MAX_TOP_K=20`、`MAX_IDENTIFIER_CHARS=128`，Schema 与运行时校验共用同一上限。
- 错误码收敛到 5 个稳定字符串：`invalid_params` / `tool_not_found` /
  `document_not_found` / `query_failed` / `internal_error`。wire envelope
  之外只暴露 component code，原始异常文本走 stderr logger。

### C2.1 — 每请求身份

- 新增 `src/mcp_server/request_context.py`：`RequestContext` 是 frozen
  dataclass，含 `request_id` / `actor_key` / `session_id`。
- `current_request_context()` 是 ContextVar；`ProtocolHandler` 在
  `asyncio.to_thread()` 入口 `bind`、出口 `reset`，Stdio 路径用
  `request_context_from_headers()` 严格校验 Header（§5.5）。

### C2.2 — SQLite Trace Store

- 新增 `src/core/trace/sqlite_trace_store.py`，落地
  `data/db/traces.db`（WAL、5s busy_timeout、单连接 per call）。
- Trace DB 路径必须与 ingestion integrity DB 路径不同（`_enforce_distinct_path`）。
- `collect()` 同一 `trace_id` 重复写入 → `TraceStoreError("already persisted")`，
  禁止静默覆盖审计记录。
- 提供 `list_recent / purge / purge_expired / check_writable`。
- 自动清理：保留窗口 `retention_days` ∈ [1, 3650]，启动清理一次，进程内
  每 24h 最多再清理一次。

### C2.3 — Compact Query Trace 集成

- `TraceContext` 增加 `detail: Literal["compact","debug"]` 字段，
  `to_persisted_dict()` 在序列化时按 detail 过滤：
  - `compact` 模式移除每阶段 `candidates` 数组与 `details.candidates`；
  - `debug` 模式封顶 20 候选供诊断。
- `QueryResponse` 增加 `trace_id`，`dataclasses.replace` 注入。
- `QueryKnowledgeHubTool._run_with_trace` 写入完整 metadata（`request_id` /
  `actor_key` / `session_id`、标准化 query、sanitized results 上限 20）；
  服务异常映射到 `ToolExecutionError("query_failed")` + 稳定
  `error_code`（`retrieval_failed` / `internal_error`），原始异常文本只走
  stderr logger。
- collector 失败仍是 best-effort，不影响 query 响应。

### C2.4 — Dashboard 最近 Trace + 清理 CLI

- `TraceService` 从 SQLite 读取（`SQLiteTraceStore.list_recent`），
  `TraceRecord` / `TraceStage` / `TraceReadResult` dataclass 与 React
  Trace 页面 wire 形状保持不变。
- API `trace_type` 只接受 `query` / `ingestion`，其它返回 HTTP 400。
- 新增 `scripts/purge_traces.py`：`--before` / `--actor-key` /
  `--db` / `--settings` / `--dry-run`。缺过滤条件退出非零；输出
  JSON 摘要，**永不打印原始 query 文本**。
- 删除 `logger.get_trace_logger` / `logger.write_trace` —— SQLite 是
  唯一事实来源（plan §11.1）。

### C4 — MCP Health

- 新增 `src/mcp_server/readiness.py`：`ReadinessService.check()` 跑三
  个本地 probe（settings / knowledge_store / trace_store），永不触
  LLM / Embedding。
- `/health/live` 永远返回 `{"status":"ok"}`，从不构造 KnowledgeService；
  `/health/ready` 按 §5.7 聚合：
  - 全 OK → `status=ready` (200)
  - 仅 trace_store 失败 → `status=degraded` (200)
  - settings / knowledge_store 失败 → `status=unavailable` (503)
- wire 响应不含路径或异常文本。
- probes 全部可注入，便于 mock 测试证明 readiness 不调 Provider。

### 评估基线

- 新增 PostgreSQL 16 教程小语料（21 页，~78K 文本，PostgreSQL License BSD）
  到 `data/corpus/corpus.jsonl`，35 条手写 query 到 `data/eval/golden.jsonl`。
- `tests/integration/test_postgres_recall.py` 走项目自身的
  RecursiveSplitter / BM25 / HashEmbedding + RRF，断言 recall@5 ≥ 80%。
- Ablation 测试证明「关掉 BM25 会掉到 < 80%」，使回归信号可证。
- 当前基线：hybrid 94.29%，dense-only 65.71%，sparse-only 100%。

## Why This Works

| 关注点 | 收益 |
|---|---|
| 上层业务接入 | MCP Streamable HTTP + `X-Request-ID` / `X-RAG-Actor-Key` / `X-RAG-Session-ID` Header 直接对接 |
| 审计与可回溯 | SQLite WAL + `trace_id` 关联上层日志；`/`health/ready` 区分降级与不可用 |
| 隐私边界 | compact 模式剥离候选正文 / 异常文本；purge CLI 缺条件退出非零 |
| 运维友好 | Health 永不调用 Provider；Trace 不可写时仅 `degraded`，查询不受影响 |
| 持续优化 | PostgreSQL 语料 + recall@5 ≥ 80% 是 retrieval 改动的硬门槛 |

## Trade-offs Accepted

- **JSONL 退役**：`logger.py` 不再提供 JSONL Trace writer；外部若仍想
  flat-file 审计，需自己包装 `SQLiteTraceStore.list_recent`。
- **Health 仅本地 probe**：不验证 Chroma 远程 / LLM 远程可达性，符合
  §C4「不让 readiness 触发 Provider 费用」约束。
- **`smoke_mcp_load.py` 留白**：plan §C5 列出的受控负载脚本因为本地
  无部署目标未落地（详见 plan §11.1 进度表 C5 [部分] 注释）。
- **`scripts/import_synology.py` 未触及**：与 plan 0001 无关，保留
  untracked。

## Superseded

无（本 ADR 是 plan 0001 的决策落档；旧 ADR 不受影响）。
