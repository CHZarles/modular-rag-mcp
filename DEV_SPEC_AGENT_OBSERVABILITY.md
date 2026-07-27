# Developer Specification (DEV_SPEC): MCP/RAG 可观测、评测与训练数据沉淀

> 版本：0.2 — 补充性开发规范
> 适用范围：在现有 Modular RAG MCP Server 上补充本地优先的可观测、评测、缓存统计、人工反馈与训练数据沉淀能力。

---

## 目录

- 项目概述
- 核心特点
- 技术选型
- 测试方案
- 系统架构与模块设计
- 项目排期
- 可扩展性与未来展望

---

## 1. 项目概述

本补充规范面向现有 Modular RAG MCP Server 的“评测与观测闭环”能力建设。原项目已经规划了 RAG Pipeline、MCP Server、Dashboard、Ragas/Custom Evaluator 等能力；本规范进一步补齐运行数据采集、缓存命中统计、token/cost 统计、轻量人工反馈、坏 case 沉淀和训练数据导出流程。

本模块的核心目标不是复刻某个外部观测平台，而是在本地环境中建立一套可解释、可复盘、可扩展的数据闭环：

```text
MCP Tool 调用 / RAG Query / Ingestion / Evaluation
    -> Trace SDK 采集结构化运行轨迹
    -> SQLite 持久化为本地事实源
    -> Dashboard 查看调用链路、耗时、token、cost、cache、反馈
    -> Evaluation 关联 golden set 与失败归因
    -> Exporter 导出 SFT / Bad Case / Reward Features 数据
    -> 反哺 RAG 策略、Prompt、评测集与后续 Agent 扩展
```

### 设计理念 (Design Philosophy)

> **核心定位：本地优先的数据闭环，而不是外部 SaaS 依赖。**

本项目当前是一个 MCP Server。MCP Server 天然只感知调用者发来的请求、工具参数、本服务内部执行过程和最终返回结果；它不感知外部 Agent 的隐藏上下文、规划过程、其他工具调用或完整任务轨迹。因此，本规范明确区分：

- **当前范围**：完整记录 MCP Server 内部可见的 RAG/MCP 执行轨迹。
- **后续扩展**：如果项目未来内置 Agent Runtime，再把同一套 Trace 模型扩展为完整 Agent Run / Tool Trajectory 采集。

同时需要和主 DEV_SPEC 中的 `KnowledgeService` 边界对齐：当 `knowledge_service.mode=local` 时，当前进程负责记录完整 RAG trace/cache/eval；当 `knowledge_service.mode=http` 时，当前 MCP Server 只记录 tool envelope、`request_id`、总耗时和错误，真正执行检索的共享检索服务负责记录完整 RAG 细节。

本补充模块遵循以下原则：

- **Server-side 可观测优先**：先把本服务内部发生了什么记录清楚。
- **SQLite 单一事实源**：所有核心数据先写本地 SQLite，不依赖外部平台。
- **可插拔输出**：允许通过 TraceSink 接入 LangSmith、Langfuse、OpenTelemetry 等现有观测工具，但它们只是可选输出端。
- **应用级缓存优先**：只缓存本项目可稳定控制的组件调用结果，如 embedding、vision caption、retrieval、rerank；不设计模型内部推理状态观测。
- **轻量 Human-in-the-loop**：只做 good/bad、原因、修正答案、修正来源，不引入复杂标注系统。
- **训练数据预留**：当前不实现训练流程，但数据结构应能导出 SFT、Bad Case、Reward Features，并为后续 DPO / Agentic RL 预留空间。

### 项目定位

本补充模块是：

- 一个本地优先的 MCP/RAG 运行数据采集层。
- 一个类 LangSmith 的本地 trace 与指标复盘工具。
- 一个 RAG 评测结果、坏 case、人工反馈的沉淀层。
- 一个后续训练数据导出的准备层。

本补充模块不是：

- 不是 LangSmith 的完整复刻。
- 不是分布式 APM 系统。
- 不是多用户标注平台。
- 不是强化学习训练框架。
- 不是外部 Agent 的黑盒监控器。

它要回答的问题：

1. 一次 MCP/RAG 调用内部经历了哪些阶段？
2. 每个阶段花了多少时间、用了多少 token、产生了多少成本？
3. 哪些组件命中了缓存，节省了多少调用？
4. 检索、重排、生成分别在哪里失败？
5. 用户认为结果好不好，正确答案和正确来源是什么？
6. 哪些样本可以沉淀为后续训练或调优数据？

---

## 2. 核心特点

### 2.1 Server-side Trace 白盒化

系统以 `Run / Span / Event / Artifact` 作为统一追踪模型。

| 层级 | 含义 | 示例 |
|---|---|---|
| `Run` | 一次本服务可见的完整任务 | 一次 `query_knowledge_hub` 调用、一次 ingestion、一次 evaluation |
| `Span` | Run 内的执行阶段 | dense retrieval、sparse retrieval、fusion、rerank、LLM call |
| `Event` | Span 内发生的离散事件 | cache_hit、cache_miss、retry、fallback、error |
| `Artifact` | 可复盘的中间产物 | user_query、prompt、retrieved_chunks、reranked_chunks、answer |

边界说明：

- 当前 `Run` 表示一次 MCP tool call 或本服务内部任务，不等价于外部 Agent 的完整 run。
- 外部 Agent 的完整规划过程只有在后续内置 Agent Runtime 后才进入采集范围。
- 当前模块不通过额外 MCP tool 要求外部调用者主动上报 trace，避免把边界做虚。

### 2.2 SQLite 本地事实源

SQLite 作为本模块的主存储：

- 本地运行，无需部署数据库服务。
- 支持结构化查询，适合 Dashboard 聚合。
- 支持事务，便于保证 run/span/event/artifact 一致性。
- 便于导出 JSONL、Parquet、类 LangSmith JSON、训练样本等格式。

### 2.3 可插拔 TraceSink

业务代码只依赖本项目的 `Tracer` 接口，不直接依赖外部观测工具 SDK。

Trace 数据写入由 `TraceSink` 决定：

| Sink | 当前状态 | 说明 |
|---|---|---|
| `SQLiteTraceSink` | 初期实现 | 写入本地 SQLite，作为事实源 |
| `CompositeTraceSink` | 初期实现 | 同时广播到多个 sink |
| `NoopExternalTraceSink` | 初期实现 | 外部 sink 占位，保证无外部服务时正常运行 |
| `LangSmithTraceSink` | 后续扩展 | 对接 LangSmith |
| `LangfuseTraceSink` | 后续扩展 | 对接 Langfuse |
| `OpenTelemetryTraceSink` | 后续扩展 | 对接 OTel / Jaeger / Tempo |
| `PhoenixTraceSink` | 后续扩展 | 对接 Arize Phoenix |

设计约束：

- SQLite sink 默认启用。
- 外部 sink 默认关闭。
- 外部 sink 失败不得中断 MCP/RAG 主流程。
- 外部凭据只从环境变量读取，不写入 SQLite。

### 2.4 应用级缓存与缓存收益统计

缓存只做应用层缓存，缓存本项目能稳定控制的组件输出。

当前优先实现：

- **Embedding Cache**：相同文本、embedding provider、model、config 时复用向量。
- **Vision Caption Cache**：相同 image hash、vision provider、model、prompt version 时复用 caption。

后续可扩展：

- **LLM Response Cache**：相同 messages/model/config 复用完整响应，默认关闭。
- **Retrieval Cache**：相同 query/collection/top_k/filters/index_version 复用检索结果，默认关闭。
- **Rerank Cache**：相同 query/candidate ids/reranker config 复用排序结果，默认关闭。

不做：

- 不缓存模型内部推理状态。
- 不记录无法从第三方 API 真实获得的模型内部缓存指标。
- 不引入 Redis 等外部缓存服务作为初期依赖。

### 2.5 Token、Cost、Latency 统一统计

模型相关 span 记录统一 usage：

- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `cached_tokens`
- `reasoning_tokens`
- `cost_usd`
- `latency_ms`
- `provider`
- `model`

说明：

- usage 优先采用 provider 返回值。
- `cached_tokens` 只记录 provider 明确返回的缓存 token，不推断、不伪造。
- cost 由本地 pricing table 计算。
- 未知模型价格按 0 记录，并产生 warning event，避免阻断主流程。

### 2.6 轻量 Human-in-the-loop

当前只做轻量反馈：

- `good / bad` 整体反馈。
- 可选 `reason`。
- 可选 `correct_answer`。
- 可选 `correct_sources`。
- 可选 `correct_chunk_ids`。

不做复杂 review queue、多人审核、偏好对比 UI、轨迹评分工作台。反馈数据以 `run_id` 为核心关联到 trace、retrieved chunks、answer、token/cost 和评测指标。

### 2.7 训练数据沉淀

当前阶段支持导出：

- **SFT 修正样本**：来自用户填写的 `correct_answer`。
- **Bad Case 数据集**：来自 bad feedback、失败 run、评测未达标样本。
- **Reward Features**：质量、延迟、成本、缓存命中、人工反馈等可解释特征。

后续预留：

- Preference pair exporter。
- 内置 Agent Runtime 的 tool trajectory exporter。
- Agentic RL rollout exporter。

---

## 3. 技术选型

### 3.1 总体技术栈

| 类别 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.11+ | 与现有项目一致 |
| 主存储 | SQLite | 本地优先，零外部服务依赖 |
| 测试框架 | pytest | 单元、集成、E2E 统一入口 |
| Dashboard | Streamlit | 复用现有 Dashboard 技术方向 |
| 配置 | YAML | 复用 `settings.yaml` 风格 |
| 观测输出 | TraceSink | SQLite 默认，可选外部观测工具 |
| 导出 | JSONL | 当前默认训练数据导出格式 |
| 数据结构 | dataclasses / TypedDict / Pydantic 可选 | 以现有项目风格为准 |

### 3.2 可插拔架构设计

#### 3.2.1 设计原则

- **接口隔离**：业务代码只依赖 `Tracer`、`CacheStore`、`Exporter` 等抽象。
- **配置驱动**：sink、cache、exporter、pricing 均由配置决定。
- **默认本地化**：无外部配置时仅写 SQLite。
- **失败隔离**：观测、缓存、导出失败不得影响 MCP/RAG 主链路。

#### 3.2.2 核心接口

```python
class BaseTracer:
    def start_run(self, name: str, run_type: str, inputs: dict) -> "RunContext": ...
    def start_span(self, run_id: str, name: str, span_type: str, inputs: dict) -> "SpanContext": ...
    def record_event(self, run_id: str, span_id: str | None, event_type: str, payload: dict) -> None: ...
    def record_artifact(self, run_id: str, span_id: str | None, artifact_type: str, content: dict | str) -> None: ...

class BaseTraceStore:
    def create_run(self, run: dict) -> None: ...
    def finish_run(self, run_id: str, status: str, outputs: dict | None, error: str | None) -> None: ...
    def create_span(self, span: dict) -> None: ...
    def finish_span(self, span_id: str, status: str, outputs: dict | None, error: str | None) -> None: ...
    def append_event(self, event: dict) -> None: ...
    def append_artifact(self, artifact: dict) -> None: ...

class BaseTraceSink:
    def on_run_started(self, run: dict) -> None: ...
    def on_run_finished(self, run: dict) -> None: ...
    def on_span_started(self, span: dict) -> None: ...
    def on_span_finished(self, span: dict) -> None: ...
    def on_event(self, event: dict) -> None: ...
    def on_artifact(self, artifact: dict) -> None: ...

class BaseCacheStore:
    def get(self, namespace: str, key: str) -> dict | None: ...
    def set(self, namespace: str, key: str, value: dict, ttl_seconds: int | None = None) -> None: ...
    def delete(self, namespace: str, key: str) -> None: ...

class BaseExporter:
    def export(self, output_path: str, filters: dict | None = None) -> "ExportResult": ...
```

接口关系：

- `BaseTraceStore` 负责 SQLite 事实数据 CRUD。
- `BaseTraceSink` 负责接收 Trace SDK 生命周期事件。
- `SQLiteTraceSink` 通过 `BaseTraceStore` 写入本地事实源。
- 外部观测工具只实现 `BaseTraceSink`，不要求支持本地查询。
- `BaseTraceSink` 的方法命名（`on_*_started` / `on_*_finished`）和 `BaseCacheStore` 的方法签名（`get/set/delete`，含 `ttl_seconds?`）与 `DEV_SPEC_INTERFACES §8.1 / §8.2` 保持一致，是同一接口的两份 spec 描述。
- 缓存命中事件 (`cache_hit` / `cache_miss`) 通过调用方持有的 `BaseTracer.record_event(...)` 上报，`BaseCacheStore` 接口不再承担事件记录职责，避免与 `BaseTraceSink.on_event(...)` 的职责重复。

#### 3.2.3 配置示例

```yaml
mcp_rag_observability:
  enabled: true

  storage:
    backend: sqlite
    sqlite_path: ./data/db/mcp_rag_observability.db

  tracing:
    sinks:
      - sqlite
    external_sinks:
      langsmith:
        enabled: false
      langfuse:
        enabled: false
      opentelemetry:
        enabled: false
    store_content: true
    capture_llm_messages: true
    capture_tool_io: true
    capture_retrieved_chunks: true
    capture_errors: true
    never_store_secrets: true

  cache:
    enabled: true
    embedding:
      enabled: true
      namespace: embedding
    vision_caption:
      enabled: true
      namespace: vision_caption
    llm_response:
      enabled: false
    retrieval:
      enabled: false
    rerank:
      enabled: false

  pricing:
    currency: USD
    models:
      gpt-4o:
        input_per_1m: 2.50
        output_per_1m: 10.00
      text-embedding-3-small:
        input_per_1m: 0.02

  dashboard:
    enabled: true
    pages:
      - runs
      - cache_metrics
      - feedback
      - training_data

  export:
    default_output_dir: ./exports/mcp_rag_observability
    formats:
      - jsonl
```

### 3.3 SQLite 持久化设计

当前阶段使用以下表作为核心事实源：

| 表 | 用途 |
|---|---|
| `runs` | 一次 MCP tool call、RAG query、ingestion 或 evaluation |
| `spans` | Run 内部阶段 |
| `events` | cache、retry、fallback、error、feedback 等事件 |
| `artifacts` | prompt、answer、retrieved chunks、tool IO 等产物 |
| `token_usage` | token 与成本统计 |
| `cache_records` | 应用级缓存内容 |
| `cache_events` | 缓存访问事件 |
| `feedback_events` | 人工轻量反馈 |
| `eval_runs` | 一次离线评测任务 |
| `eval_results` | 每条评测样本结果 |

#### 3.3.1 runs

```sql
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  parent_run_id TEXT,
  session_id TEXT,
  name TEXT NOT NULL,
  run_type TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  latency_ms INTEGER,
  inputs_json TEXT,
  outputs_json TEXT,
  error TEXT,
  metadata_json TEXT
);
```

典型 `run_type`：

- `mcp_tool_call`
- `rag_query`
- `ingestion`
- `evaluation`
- `agent_run`，后续内置 Agent Runtime 使用

#### 3.3.2 spans

```sql
CREATE TABLE spans (
  span_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  parent_span_id TEXT,
  name TEXT NOT NULL,
  span_type TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  latency_ms INTEGER,
  provider TEXT,
  model TEXT,
  inputs_json TEXT,
  outputs_json TEXT,
  error TEXT,
  metadata_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
```

典型 `span_type`：

- `tool_call`
- `query_processing`
- `retrieval_dense`
- `retrieval_sparse`
- `fusion`
- `rerank`
- `llm`
- `embedding`
- `vision_caption`
- `cache`
- `evaluation`

#### 3.3.3 events

```sql
CREATE TABLE events (
  event_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  span_id TEXT,
  event_type TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  payload_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(span_id) REFERENCES spans(span_id)
);
```

典型 `event_type`：

- `cache_hit`
- `cache_miss`
- `retry`
- `fallback`
- `error`
- `human_feedback`
- `warning`

#### 3.3.4 artifacts

```sql
CREATE TABLE artifacts (
  artifact_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  span_id TEXT,
  artifact_type TEXT NOT NULL,
  name TEXT,
  mime_type TEXT,
  content_text TEXT,
  content_json TEXT,
  created_at TEXT NOT NULL,
  metadata_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(span_id) REFERENCES spans(span_id)
);
```

典型 `artifact_type`：

- `user_query`
- `llm_messages`
- `prompt`
- `answer`
- `retrieved_chunks`
- `reranked_chunks`
- `tool_input`
- `tool_output`
- `golden_case`
- `corrected_answer`

#### 3.3.5 token_usage

```sql
CREATE TABLE token_usage (
  usage_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  span_id TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  prompt_tokens INTEGER DEFAULT 0,
  completion_tokens INTEGER DEFAULT 0,
  total_tokens INTEGER DEFAULT 0,
  cached_tokens INTEGER DEFAULT 0,
  reasoning_tokens INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(span_id) REFERENCES spans(span_id)
);
```

#### 3.3.6 cache_records / cache_events

```sql
CREATE TABLE cache_records (
  cache_key TEXT PRIMARY KEY,
  namespace TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  value_json TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  config_hash TEXT,
  prompt_version TEXT,
  index_version TEXT,
  created_at TEXT NOT NULL,
  last_hit_at TEXT,
  hit_count INTEGER DEFAULT 0,
  metadata_json TEXT
);

CREATE TABLE cache_events (
  cache_event_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  span_id TEXT,
  namespace TEXT NOT NULL,
  cache_key TEXT NOT NULL,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  saved_tokens INTEGER DEFAULT 0,
  saved_cost_usd REAL DEFAULT 0,
  created_at TEXT NOT NULL,
  metadata_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(span_id) REFERENCES spans(span_id)
);
```

缓存 key 规则：

```text
cache_key = sha256(namespace + input_hash + provider + model + config_hash + prompt_version + index_version?)
```

#### 3.3.7 feedback_events

```sql
CREATE TABLE feedback_events (
  feedback_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  rating TEXT NOT NULL,
  reason TEXT,
  correct_answer TEXT,
  correct_sources_json TEXT,
  correct_chunk_ids_json TEXT,
  created_at TEXT NOT NULL,
  metadata_json TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
```

`rating`：

- `good`
- `bad`

#### 3.3.8 eval_runs / eval_results

```sql
CREATE TABLE eval_runs (
  eval_run_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  dataset_path TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL,
  summary_metrics_json TEXT,
  metadata_json TEXT
);

CREATE TABLE eval_results (
  eval_result_id TEXT PRIMARY KEY,
  eval_run_id TEXT NOT NULL,
  run_id TEXT,
  query TEXT NOT NULL,
  expected_json TEXT,
  actual_json TEXT,
  metrics_json TEXT,
  failure_type TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(eval_run_id) REFERENCES eval_runs(eval_run_id),
  FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
```

### 3.4 可观测性与 Dashboard 设计

Dashboard 新增四个页面：

| 页面 | 功能 |
|---|---|
| Runs | 查看 run 列表、span 树、artifact、token/cost、错误 |
| Cache Metrics | 查看各 namespace 的 hit/miss、命中率、saved cost |
| Feedback | 追加 good/bad、reason、correct answer、correct sources |
| Training Data | 导出 SFT、bad case、reward features JSONL |

### 3.5 评估与数据导出设计

#### Failure Classification

初期采用规则分类，不引入 LLM-as-judge：

```text
no_retrieval_hit
wrong_ranking
insufficient_context
hallucination
wrong_citation
tool_error
too_expensive
too_slow
user_intent_missed
unknown
```

#### Reward Features

当前不计算单一 reward 分数，只导出可解释特征：

```json
{
  "run_id": "...",
  "rating": "good",
  "hit_rate_at_k": 1.0,
  "mrr": 1.0,
  "latency_ms": 812,
  "total_tokens": 1532,
  "cost_usd": 0.0042,
  "cache_hit_rate": 0.5,
  "had_error": false,
  "failure_type": null
}
```

#### Export Formats

当前支持：

- `runs.jsonl`
- `sft_corrections.jsonl`
- `bad_cases.jsonl`
- `reward_features.jsonl`

后续可扩展：

- `preference_pairs.jsonl`
- `tool_trajectories.jsonl`
- `parquet/`
- `opentelemetry.json`
- `langsmith_like.jsonl`

---

## 4. 测试方案

### 4.1 设计理念：测试驱动开发 (TDD)

本补充模块采用 TDD：

- 每个存储表先写 repository contract test。
- 每个 tracer 行为先写 run/span 生命周期测试。
- 每个 cache 行为先写 hit/miss/idempotency 测试。
- 每个 exporter 先写固定输入对应的 golden output 测试。
- Dashboard 页面只做轻量 smoke test，不追求复杂 UI 自动化。

### 4.2 测试分层策略

#### 4.2.1 单元测试 (Unit Tests)

| 模块 | 测试重点 |
|---|---|
| `tracing` | run/span 生命周期、异常状态、嵌套 span |
| `sinks` | SQLite sink、composite sink、外部 sink 失败隔离 |
| `storage` | SQLite schema 初始化、CRUD、事务一致性 |
| `cache` | cache key 稳定性、hit/miss/set、命中统计 |
| `metrics` | token usage 汇总、cost 计算 |
| `feedback` | good/bad 反馈写入与查询 |
| `evaluation` | hit_rate、MRR、NDCG、failure type |
| `export` | SFT、bad case、reward features JSONL 输出 |

#### 4.2.2 集成测试 (Integration Tests)

| 测试场景 | 验证要点 |
|---|---|
| MCP tool trace | 一次 tool call 生成 run/span/artifact |
| RAG query trace | retrieval、fusion、rerank span 被正确记录 |
| Ingestion cache | 相同 chunk 第二次 embedding 命中缓存 |
| Vision caption cache | 相同 image hash 第二次 caption 命中缓存 |
| Feedback + Export | bad feedback 能导出 bad case |
| EvalRunner + Trace | golden case 评测结果能关联 run_id |

#### 4.2.3 端到端测试 (End-to-End Tests)

核心 E2E：

1. 启动临时 SQLite。
2. 摄取测试文档。
3. 发起一次 query。
4. 验证 run/span/event/artifact/token/cache 均有记录。
5. 提交 bad feedback 和 correct_answer。
6. 执行 export。
7. 验证生成 `sft_corrections.jsonl` 与 `bad_cases.jsonl`。

### 4.3 RAG 质量评估

评估指标：

| 指标 | 来源 | 说明 |
|---|---|---|
| `hit_rate_at_k` | golden set + retrieved chunks | Top-K 是否命中期望来源 |
| `mrr` | golden set + ranking | 正确来源排名质量 |
| `ndcg_at_k` | golden set + relevance | 排序整体质量 |
| `faithfulness` | Ragas/后续 evaluator | 答案是否被上下文支持 |
| `answer_relevancy` | Ragas/后续 evaluator | 答案是否回应问题 |
| `latency_ms` | trace | 端到端耗时 |
| `total_tokens` | token_usage | token 成本 |
| `cost_usd` | pricing calculator | 金钱成本 |
| `cache_hit_rate` | cache_events | 缓存效果 |
| `failure_type` | failure classifier | 失败归因 |

---

## 5. 系统架构与模块设计

### 5.1 整体架构图

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                         MCP Client / 后续 Local Agent                      │
│                         Copilot / Claude / CLI / Demo                      │
└────────────────────────────────────┬──────────────────────────────────────┘
                                     │
                                     ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                              MCP Server / App                             │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ Tool Entrypoints                                                     │  │
│  │ query_knowledge_hub / ingestion / evaluation / 后续 local agent      │  │
│  └──────────────────────────────┬──────────────────────────────────────┘  │
│                                 │                                         │
│                                 ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ Trace SDK                                                            │  │
│  │ start_run / span / event / artifact / token_usage / cache_event      │  │
│  └──────────────┬───────────────────────────────────────┬──────────────┘  │
│                 │                                       │                 │
│                 ▼                                       ▼                 │
│  ┌──────────────────────────────────────┐     ┌───────────────────────┐  │
│  │ RAG Pipeline                          │     │ TraceSink Router       │  │
│  │ Query -> Dense/Sparse -> Fusion ->    │     │ SQLite + optional      │  │
│  │ Rerank -> Answer                      │     │ external sinks         │  │
│  └──────────────┬───────────────────────┘     └───────────┬───────────┘  │
│                 │                                         │              │
│                 ▼                                         │              │
│  ┌─────────────────────────────────────────────────────┐   │              │
│  │ Application Cache / Evaluation / Feedback             │   │              │
│  └─────────────────────────────────────────────────────┘   │              │
└────────────────────────────────────────────────────────────┼──────────────┘
                                                             │
                    ┌────────────────────────────────────────┴─────────────┐
                    ▼                                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                            SQLite Fact Store                              │
│ runs / spans / events / artifacts / token_usage / cache / feedback / eval  │
└────────────────────────────────────┬──────────────────────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    ▼                                 ▼
┌───────────────────────────────┐     ┌───────────────────────────────┐
│      Streamlit Dashboard      │     │            Exporters           │
│ Runs / Cache / Feedback /     │     │ JSONL / SFT / Bad Cases /      │
│ Evaluation / Training Data    │     │ Reward Features / 后续扩展     │
└───────────────────────────────┘     └───────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────┐
│ Optional External Observability Sink                                      │
│ LangSmith / Langfuse / OpenTelemetry / Phoenix                            │
│ Disabled by default; failure must not break the MCP/RAG flow               │
└───────────────────────────────────────────────────────────────────────────┘
```

### 5.2 完整目录结构树

```text
MODULAR-RAG-MCP-SERVER/
├── DEV_SPEC.md
├── DEV_SPEC_AGENT_OBSERVABILITY.md
├── config/
│   ├── settings.yaml
│   └── pricing.yaml
├── data/
│   └── db/
│       └── mcp_rag_observability.db
├── exports/
│   └── mcp_rag_observability/
│       ├── runs.jsonl
│       ├── sft_corrections.jsonl
│       ├── bad_cases.jsonl
│       └── reward_features.jsonl
├── scripts/
│   ├── export_training_data.py
│   ├── inspect_runs.py
│   └── migrate_observability_db.py
├── src/
│   ├── mcp_rag_observability/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── tracing/
│   │   │   ├── __init__.py
│   │   │   ├── models.py
│   │   │   ├── tracer.py
│   │   │   ├── context.py
│   │   │   └── decorators.py
│   │   ├── sinks/
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── sqlite_sink.py
│   │   │   ├── composite_sink.py
│   │   │   └── noop_external_sink.py
│   │   ├── storage/
│   │   │   ├── __init__.py
│   │   │   ├── schema.sql
│   │   │   ├── sqlite_store.py
│   │   │   ├── repositories.py
│   │   │   └── migrations.py
│   │   ├── cache/
│   │   │   ├── __init__.py
│   │   │   ├── cache_key.py
│   │   │   ├── cache_store.py
│   │   │   ├── embedding_cache.py
│   │   │   ├── vision_caption_cache.py
│   │   │   └── cache_metrics.py
│   │   ├── metrics/
│   │   │   ├── __init__.py
│   │   │   ├── token_usage.py
│   │   │   ├── cost_calculator.py
│   │   │   └── aggregations.py
│   │   ├── feedback/
│   │   │   ├── __init__.py
│   │   │   ├── feedback_service.py
│   │   │   └── models.py
│   │   ├── evaluation/
│   │   │   ├── __init__.py
│   │   │   ├── eval_runner.py
│   │   │   ├── failure_classifier.py
│   │   │   └── metrics.py
│   │   └── export/
│   │       ├── __init__.py
│   │       ├── jsonl_exporter.py
│   │       ├── sft_exporter.py
│   │       ├── bad_case_exporter.py
│   │       └── reward_exporter.py
│   └── observability/
│       └── dashboard/
│           ├── pages/
│           │   ├── runs.py
│           │   ├── cache_metrics.py
│           │   ├── feedback.py
│           │   └── training_data.py
│           └── services/
│               └── mcp_rag_observability_service.py
└── tests/
    ├── unit/
    │   ├── test_trace_models.py
    │   ├── test_sqlite_trace_store.py
    │   ├── test_tracer_lifecycle.py
    │   ├── test_trace_sinks.py
    │   ├── test_cache_key.py
    │   ├── test_sqlite_cache_store.py
    │   ├── test_embedding_cache.py
    │   ├── test_vision_caption_cache.py
    │   ├── test_cost_calculator.py
    │   ├── test_feedback_service.py
    │   ├── test_failure_classifier.py
    │   └── test_exporters.py
    ├── integration/
    │   ├── test_trace_sqlite_integration.py
    │   ├── test_rag_trace_integration.py
    │   ├── test_cache_integration.py
    │   └── test_feedback_export_integration.py
    ├── e2e/
    │   └── test_mcp_rag_observability_flow.py
    └── fixtures/
        ├── golden_test_set.json
        ├── sample_runs.json
        └── expected_exports/
```

### 5.3 模块职责说明表

| 模块 | 职责 | 不负责 |
|---|---|---|
| `tracing` | 创建 run/span/event/artifact，记录生命周期 | 具体业务逻辑 |
| `sinks` | 将 trace 事件写入 SQLite 或可选外部工具 | 定义业务指标 |
| `storage` | SQLite schema、迁移、repository 查询 | Dashboard 展示 |
| `cache` | 应用级缓存、cache key、命中事件 | 模型内部推理状态 |
| `metrics` | token/cost/latency 汇总 | 调用真实模型 |
| `feedback` | 轻量人工反馈写入与查询 | 多人审核流 |
| `evaluation` | 评测运行、指标计算、失败归因 | 训练模型 |
| `export` | 导出 JSONL、SFT、bad case、reward features | 外部上传 |
| `dashboard/services` | 为 Dashboard 提供查询聚合 | 直接写数据库 |
| `dashboard/pages` | 本地可视化页面 | 核心业务计算 |

### 5.4 数据流说明

#### 5.4.1 Ingestion Flow

```text
用户触发摄取
  -> tracer.start_run(run_type="ingestion")
  -> Load span
  -> Split span
  -> Transform span
       -> Vision Caption span
       -> vision_caption cache lookup
       -> cache_hit/cache_miss event
  -> Embedding span
       -> embedding cache lookup
       -> cache_hit/cache_miss event
       -> token_usage/cost
  -> Upsert span
  -> artifact: chunks / captions / embedding metadata
  -> finish_run(status="success" | "failed")
```

#### 5.4.2 Query Flow

```text
MCP Client 调用 query_knowledge_hub
  -> tracer.start_run(run_type="mcp_tool_call")
  -> artifact: user_query
  -> KnowledgeService span
       -> mode=local: Query Processing / Dense / Sparse / Fusion / Rerank / Optional Answer Generation
       -> mode=http: shared retrieval request_id / latency / status
  -> artifact: retrieved_chunks / reranked_chunks / answer（local 模式完整记录；http 模式按响应契约记录摘要）
  -> token_usage/cost/cache_events（由实际执行模型或检索的进程负责记录）
  -> finish_run
```

#### 5.4.3 Feedback Flow

```text
用户在 Dashboard 查看 Run
  -> 点击 good/bad
  -> 可选填写 reason
  -> 可选填写 correct_answer
  -> 可选填写 correct_sources / correct_chunk_ids
  -> 写入 feedback_events
  -> 该 run 可被 SFT / bad case / reward exporter 使用
```

#### 5.4.4 Export Flow

```text
SQLite Fact Store
  -> exporter 读取 runs/spans/artifacts/feedback/eval/cache
  -> 过滤可用样本
  -> 生成 JSONL
  -> 写入 exports/mcp_rag_observability/
```

### 5.5 配置驱动设计示例

#### 开启观测

```yaml
mcp_rag_observability:
  enabled: true
  storage:
    backend: sqlite
    sqlite_path: ./data/db/mcp_rag_observability.db
```

#### 只开启 embedding cache

```yaml
mcp_rag_observability:
  cache:
    enabled: true
    embedding:
      enabled: true
    vision_caption:
      enabled: false
```

#### 可选接入外部观测工具

```yaml
mcp_rag_observability:
  tracing:
    sinks:
      - sqlite
      - langfuse
    external_sinks:
      langfuse:
        enabled: true
        public_key_env: LANGFUSE_PUBLIC_KEY
        secret_key_env: LANGFUSE_SECRET_KEY
        host: http://localhost:3000
```

约束：

- `sqlite` 建议始终保留为本地事实源。
- 外部 sink 默认关闭。
- 外部 sink 初始化失败或写入失败，只记录错误事件，不中断 MCP/RAG 请求。

---

## 6. 项目排期

### 6.1 阶段总览

| 阶段 | 名称 | 目的 | 状态 |
|---|---|---|---|
| A | 数据模型与工程基座 | 建立包结构、配置、SQLite schema、测试基座 | [ ] |
| B | Trace SDK 与 TraceSink | 实现 Run/Span/Event/Artifact 生命周期与可插拔输出 | [ ] |
| C | Token / Cost 统计 | 记录模型用量和成本指标 | [ ] |
| D | Application Cache | 实现 embedding 与 vision caption 缓存 | [ ] |
| E | RAG/MCP 接入 | 将 tracer 接入现有 ingestion/query/MCP 流程 | [ ] |
| F | Dashboard 页面 | 展示 runs、cache、feedback、training export | [ ] |
| G | Evaluation 增强 | 连接 golden set、trace、失败归因、成本指标 | [ ] |
| H | HITL 与训练导出 | 轻量反馈和 SFT/bad case/reward JSONL 导出 | [ ] |
| I | E2E 验收与文档 | 全链路验收、脚本、文档完善 | [ ] |

### 6.2 阶段 A：数据模型与工程基座

#### A1：新增 mcp_rag_observability 包结构

- **目标**：创建补充模块目录，保证可 import。
- **修改文件**：
  - `src/mcp_rag_observability/__init__.py`
  - `src/mcp_rag_observability/config.py`
  - 各子包 `__init__.py`
  - `tests/unit/test_trace_models.py`
- **实现类/函数**：
  - `McpRagObservabilitySettings`
  - `load_mcp_rag_observability_settings(settings)`
- **验收标准**：
  - 包可以被 import。
  - 默认配置可加载。
- **测试方法**：
  - `pytest -q tests/unit/test_trace_models.py`

#### A2：SQLite schema 与迁移脚本

- **目标**：建立 SQLite 表结构。
- **修改文件**：
  - `src/mcp_rag_observability/storage/schema.sql`
  - `src/mcp_rag_observability/storage/migrations.py`
  - `scripts/migrate_observability_db.py`
  - `tests/unit/test_sqlite_trace_store.py`
- **实现类/函数**：
  - `initialize_database(db_path: str) -> None`
  - `get_schema_version(conn) -> int`
- **验收标准**：
  - 空数据库可初始化全部表。
  - 重复执行迁移幂等。
- **测试方法**：
  - `pytest -q tests/unit/test_sqlite_trace_store.py`

#### A3：Repository 基础接口

- **目标**：封装 runs/spans/events/artifacts 基础 CRUD。
- **修改文件**：
  - `src/mcp_rag_observability/storage/repositories.py`
  - `src/mcp_rag_observability/storage/sqlite_store.py`
  - `tests/unit/test_sqlite_trace_store.py`
- **实现类/函数**：
  - `SQLiteTraceStore`
  - `RunRepository`
  - `SpanRepository`
  - `EventRepository`
  - `ArtifactRepository`
- **验收标准**：
  - 可创建、结束、查询 run/span。
  - event/artifact 可追加并按 run_id 查询。
- **测试方法**：
  - `pytest -q tests/unit/test_sqlite_trace_store.py`

### 6.3 阶段 B：Trace SDK 与 TraceSink

#### B1：Trace 数据模型

- **目标**：定义 Run、Span、Event、Artifact 的 Python 数据结构。
- **修改文件**：
  - `src/mcp_rag_observability/tracing/models.py`
  - `tests/unit/test_trace_models.py`
- **实现类/函数**：
  - `RunRecord`
  - `SpanRecord`
  - `EventRecord`
  - `ArtifactRecord`
- **验收标准**：
  - 每个模型可序列化为 dict。
  - 必填字段校验清晰。
- **测试方法**：
  - `pytest -q tests/unit/test_trace_models.py`

#### B2：RunContext / SpanContext 生命周期

- **目标**：通过 context manager 自动记录开始、结束、异常。
- **修改文件**：
  - `src/mcp_rag_observability/tracing/context.py`
  - `src/mcp_rag_observability/tracing/tracer.py`
  - `tests/unit/test_tracer_lifecycle.py`
- **实现类/函数**：
  - `Tracer.start_run()`
  - `RunContext.span()`
  - `SpanContext.record_artifact()`
  - `SpanContext.record_event()`
- **验收标准**：
  - 正常退出标记 `success`。
  - 异常退出标记 `failed` 并记录 error。
  - 嵌套 span 保持 parent-child 关系。
- **测试方法**：
  - `pytest -q tests/unit/test_tracer_lifecycle.py`

#### B3：装饰器埋点

- **目标**：提供低侵入函数装饰器。
- **修改文件**：
  - `src/mcp_rag_observability/tracing/decorators.py`
  - `tests/unit/test_tracer_lifecycle.py`
- **实现类/函数**：
  - `trace_span(name: str, span_type: str)`
- **验收标准**：
  - 被装饰函数自动创建 span。
  - 返回值和异常行为不被改变。
- **测试方法**：
  - `pytest -q tests/unit/test_tracer_lifecycle.py -k decorator`

#### B4：TraceSink Router 与外部观测工具预留

- **目标**：让 Trace SDK 不直接绑定 SQLite，支持后续接入现有观测工具。
- **修改文件**：
  - `src/mcp_rag_observability/sinks/base.py`
  - `src/mcp_rag_observability/sinks/sqlite_sink.py`
  - `src/mcp_rag_observability/sinks/composite_sink.py`
  - `src/mcp_rag_observability/sinks/noop_external_sink.py`
  - `src/mcp_rag_observability/tracing/tracer.py`
  - `tests/unit/test_trace_sinks.py`
- **实现类/函数**：
  - `BaseTraceSink`
  - `SQLiteTraceSink(BaseTraceSink)`
  - `CompositeTraceSink(BaseTraceSink)`
  - `NoopExternalTraceSink(BaseTraceSink)`
  - `build_trace_sink(settings, store) -> BaseTraceSink`
- **验收标准**：
  - 默认配置只写 SQLite。
  - 配置多个 sink 时事件会广播给全部 sink。
  - 外部 sink 抛错时只记录 error event，不影响主流程。
  - 业务代码仍只依赖 `Tracer`。
- **测试方法**：
  - `pytest -q tests/unit/test_trace_sinks.py`

### 6.4 阶段 C：Token / Cost 统计

#### C1：Token usage 记录

- **目标**：记录 provider 返回的 usage。
- **修改文件**：
  - `src/mcp_rag_observability/metrics/token_usage.py`
  - `src/mcp_rag_observability/storage/repositories.py`
  - `tests/unit/test_cost_calculator.py`
- **实现类/函数**：
  - `TokenUsage`
  - `record_token_usage(run_id, span_id, usage)`
- **验收标准**：
  - prompt/completion/total/cached/reasoning tokens 可落库。
- **测试方法**：
  - `pytest -q tests/unit/test_cost_calculator.py`

#### C2：CostCalculator

- **目标**：根据模型价格配置计算成本。
- **修改文件**：
  - `src/mcp_rag_observability/metrics/cost_calculator.py`
  - `config/pricing.yaml`
  - `tests/unit/test_cost_calculator.py`
- **实现类/函数**：
  - `CostCalculator.calculate(provider, model, usage) -> float`
- **验收标准**：
  - 已知模型能算出稳定 cost。
  - 未知模型返回 0 并记录 warning event。
- **测试方法**：
  - `pytest -q tests/unit/test_cost_calculator.py`

### 6.5 阶段 D：Application Cache

#### D1：Cache key 生成

- **目标**：统一 cache key 规则。
- **修改文件**：
  - `src/mcp_rag_observability/cache/cache_key.py`
  - `tests/unit/test_cache_key.py`
- **实现类/函数**：
  - `hash_input(value) -> str`
  - `build_cache_key(namespace, input_hash, provider, model, config_hash, prompt_version=None, index_version=None) -> str`
- **验收标准**：
  - 相同输入生成相同 key。
  - 不同模型/配置生成不同 key。
- **测试方法**：
  - `pytest -q tests/unit/test_cache_key.py`

#### D2：SQLiteCacheStore

- **目标**：实现缓存读写与事件记录。
- **修改文件**：
  - `src/mcp_rag_observability/cache/cache_store.py`
  - `tests/unit/test_sqlite_cache_store.py`
- **实现类/函数**：
  - `SQLiteCacheStore.get()`
  - `SQLiteCacheStore.set()`
  - `SQLiteCacheStore.delete()`
  - 缓存命中事件通过调用方传入的 `BaseTracer.record_event(event_type="cache_hit"|"cache_miss", payload={...})` 上报（`BaseCacheStore` 不再包含 `record_cache_event`）
- **验收标准**：
  - miss/hit/set 都写入 `cache_events`。
  - hit_count 正确递增。
- **测试方法**：
  - `pytest -q tests/unit/test_sqlite_cache_store.py`

#### D3：EmbeddingCache

- **目标**：为 embedding 调用提供缓存包装。
- **修改文件**：
  - `src/mcp_rag_observability/cache/embedding_cache.py`
  - `tests/unit/test_embedding_cache.py`
- **实现类/函数**：
  - `EmbeddingCache.get_or_compute(texts, provider, model, compute_fn)`
- **验收标准**：
  - 第一次调用 compute_fn。
  - 第二次相同文本不调用 compute_fn。
  - 记录 cache hit/miss。
- **测试方法**：
  - `pytest -q tests/unit/test_embedding_cache.py`

#### D4：VisionCaptionCache

- **目标**：为图片 caption 提供缓存包装。
- **修改文件**：
  - `src/mcp_rag_observability/cache/vision_caption_cache.py`
  - `tests/unit/test_vision_caption_cache.py`
- **实现类/函数**：
  - `VisionCaptionCache.get_or_compute(image_hash, prompt_version, provider, model, compute_fn)`
- **验收标准**：
  - 相同 image_hash/prompt/model 命中缓存。
  - prompt_version 变化时不命中旧缓存。
- **测试方法**：
  - `pytest -q tests/unit/test_vision_caption_cache.py`

#### D5：CacheMetrics 聚合

- **目标**：为 Dashboard 提供缓存统计。
- **修改文件**：
  - `src/mcp_rag_observability/cache/cache_metrics.py`
  - `tests/unit/test_sqlite_cache_store.py`
- **实现类/函数**：
  - `get_cache_summary(namespace=None, since=None) -> dict`
- **验收标准**：
  - 返回 hit_count、miss_count、hit_rate、saved_cost。
- **测试方法**：
  - `pytest -q tests/unit/test_sqlite_cache_store.py -k metrics`

### 6.6 阶段 E：RAG/MCP 接入

#### E1：MCP tool 入口创建 Run

- **目标**：一次 MCP tool call 对应一个 run。
- **修改文件**：
  - `src/server.py` 或现有 MCP 入口文件
  - `tests/integration/test_rag_trace_integration.py`
- **实现类/函数**：
  - `with tracer.start_run(name="query_knowledge_hub", run_type="mcp_tool_call", inputs=...)`
- **验收标准**：
  - 调用工具后 SQLite 中出现 run。
  - run artifact 包含 user_query 与 final answer。
- **测试方法**：
  - `pytest -q tests/integration/test_rag_trace_integration.py -k mcp`

#### E2：Query Pipeline span 埋点

- **目标**：记录 query_processing、dense、sparse、fusion、rerank。
- **修改文件**：
  - `src/retrieval/*` 相关文件
  - `tests/integration/test_rag_trace_integration.py`
- **实现类/函数**：
  - 在每个阶段增加 `RunContext.span(...)`
- **验收标准**：
  - 一次 query 至少生成 5 个核心 span。
  - span 输出包含候选数量、top ids、耗时。
- **测试方法**：
  - `pytest -q tests/integration/test_rag_trace_integration.py -k query_spans`

#### E3：Ingestion Pipeline span 埋点

- **目标**：记录 load、split、transform、embedding、upsert。
- **修改文件**：
  - `src/ingestion/*` 相关文件
  - `tests/integration/test_cache_integration.py`
- **实现类/函数**：
  - ingestion run/span 接入 tracer。
- **验收标准**：
  - 摄取一次文档生成完整 ingestion run。
  - embedding 与 caption 缓存事件能关联到 run_id。
- **测试方法**：
  - `pytest -q tests/integration/test_cache_integration.py`

#### E4：原 JSONL trace 兼容策略

- **目标**：避免一次性破坏原观测机制。
- **修改文件**：
  - `src/observability/*` 相关文件
  - `tests/integration/test_trace_sqlite_integration.py`
- **实现类/函数**：
  - `SQLiteTraceSink`
  - 可选 `JsonlTraceExporter`
- **验收标准**：
  - 新 trace 写 SQLite。
  - 原 Dashboard 页面不被破坏。
- **测试方法**：
  - `pytest -q tests/integration/test_trace_sqlite_integration.py`

### 6.7 阶段 F：Dashboard 页面

#### F1：McpRagObservabilityService

- **目标**：封装 Dashboard 查询。
- **修改文件**：
  - `src/observability/dashboard/services/mcp_rag_observability_service.py`
  - `tests/unit/test_sqlite_trace_store.py`
- **实现类/函数**：
  - `list_runs(filters)`
  - `get_run_detail(run_id)`
  - `get_cache_metrics(filters)`
  - `list_feedback(filters)`
- **验收标准**：
  - 服务层不直接暴露 SQL。
- **测试方法**：
  - `pytest -q tests/unit/test_sqlite_trace_store.py -k service`

#### F2：Runs 页面

- **目标**：展示 run 列表与详情。
- **修改文件**：
  - `src/observability/dashboard/pages/runs.py`
- **实现类/函数**：
  - `render_runs_page()`
- **验收标准**：
  - 可查看 run 列表、span 树、artifact、token/cost。
- **测试方法**：
  - 手动验证 Dashboard。

#### F3：Cache Metrics 页面

- **目标**：展示 cache hit/miss 和节省成本。
- **修改文件**：
  - `src/observability/dashboard/pages/cache_metrics.py`
- **实现类/函数**：
  - `render_cache_metrics_page()`
- **验收标准**：
  - 可按 namespace 查看命中率。
- **测试方法**：
  - 手动验证 Dashboard。

#### F4：Feedback 页面

- **目标**：对 run 追加轻量反馈。
- **修改文件**：
  - `src/observability/dashboard/pages/feedback.py`
  - `src/mcp_rag_observability/feedback/feedback_service.py`
  - `tests/unit/test_feedback_service.py`
- **实现类/函数**：
  - `FeedbackService.create_feedback()`
  - `FeedbackService.list_feedback()`
- **验收标准**：
  - good/bad、reason、correct_answer、correct_sources 可保存。
- **测试方法**：
  - `pytest -q tests/unit/test_feedback_service.py`

#### F5：Training Data 页面

- **目标**：从 Dashboard 触发导出。
- **修改文件**：
  - `src/observability/dashboard/pages/training_data.py`
- **实现类/函数**：
  - `render_training_data_page()`
- **验收标准**：
  - 可导出 SFT、bad case、reward features。
- **测试方法**：
  - 手动验证 Dashboard。

### 6.8 阶段 G：Evaluation 增强

#### G1：EvalRunner 写入 SQLite

- **目标**：评测运行结果进入事实源。
- **修改文件**：
  - `src/mcp_rag_observability/evaluation/eval_runner.py`
  - `tests/integration/test_feedback_export_integration.py`
- **实现类/函数**：
  - `EvalRunner.run(dataset_path) -> EvalReport`
- **验收标准**：
  - `eval_runs` 与 `eval_results` 有记录。
- **测试方法**：
  - `pytest -q tests/integration/test_feedback_export_integration.py -k eval`

#### G2：基础检索指标

- **目标**：计算 hit_rate、MRR、NDCG。
- **修改文件**：
  - `src/mcp_rag_observability/evaluation/metrics.py`
  - `tests/unit/test_failure_classifier.py`
- **实现类/函数**：
  - `hit_rate_at_k(results, expected)`
  - `mrr(results, expected)`
  - `ndcg_at_k(results, expected)`
- **验收标准**：
  - 指标在固定样例上输出稳定。
- **测试方法**：
  - `pytest -q tests/unit/test_failure_classifier.py -k metrics`

#### G3：FailureClassifier

- **目标**：为 bad case 自动打初始失败类型。
- **修改文件**：
  - `src/mcp_rag_observability/evaluation/failure_classifier.py`
  - `tests/unit/test_failure_classifier.py`
- **实现类/函数**：
  - `RuleBasedFailureClassifier.classify(trace, eval_result, feedback) -> str`
- **验收标准**：
  - no_retrieval_hit、wrong_ranking、tool_error、too_slow、too_expensive 可识别。
- **测试方法**：
  - `pytest -q tests/unit/test_failure_classifier.py`

### 6.9 阶段 H：HITL 与训练导出

#### H1：FeedbackService

- **目标**：轻量人工反馈服务。
- **修改文件**：
  - `src/mcp_rag_observability/feedback/models.py`
  - `src/mcp_rag_observability/feedback/feedback_service.py`
  - `tests/unit/test_feedback_service.py`
- **实现类/函数**：
  - `FeedbackEvent`
  - `FeedbackService.create_feedback()`
  - `FeedbackService.get_feedback_for_run()`
- **验收标准**：
  - 一个 run 可追加多条 feedback。
  - bad feedback 可被查询。
- **测试方法**：
  - `pytest -q tests/unit/test_feedback_service.py`

#### H2：SFTExporter

- **目标**：导出人工修正答案样本。
- **修改文件**：
  - `src/mcp_rag_observability/export/sft_exporter.py`
  - `scripts/export_training_data.py`
  - `tests/unit/test_exporters.py`
- **实现类/函数**：
  - `SFTExporter.export(output_path)`
- **验收标准**：
  - 仅导出含 `correct_answer` 的反馈。
  - JSONL 包含 instruction/input/output/source_run_id。
- **测试方法**：
  - `pytest -q tests/unit/test_exporters.py -k sft`

#### H3：BadCaseExporter

- **目标**：导出失败样本。
- **修改文件**：
  - `src/mcp_rag_observability/export/bad_case_exporter.py`
  - `tests/unit/test_exporters.py`
- **实现类/函数**：
  - `BadCaseExporter.export(output_path)`
- **验收标准**：
  - bad feedback、failed run、eval failed case 可导出。
  - 输出包含 failure_type、query、answer、retrieved_chunks。
- **测试方法**：
  - `pytest -q tests/unit/test_exporters.py -k bad_case`

#### H4：RewardFeatureExporter

- **目标**：导出可解释 reward 特征。
- **修改文件**：
  - `src/mcp_rag_observability/export/reward_exporter.py`
  - `tests/unit/test_exporters.py`
- **实现类/函数**：
  - `RewardFeatureExporter.export(output_path)`
- **验收标准**：
  - 输出 rating、metrics、token、cost、latency、cache_hit_rate。
- **测试方法**：
  - `pytest -q tests/unit/test_exporters.py -k reward`

#### H5：JSONLExporter 通用导出

- **目标**：导出完整 runs 数据便于离线分析。
- **修改文件**：
  - `src/mcp_rag_observability/export/jsonl_exporter.py`
  - `tests/unit/test_exporters.py`
- **实现类/函数**：
  - `JSONLExporter.export_runs(output_path, filters)`
- **验收标准**：
  - 每行一个 run，包含 spans/events/artifacts 摘要。
- **测试方法**：
  - `pytest -q tests/unit/test_exporters.py -k jsonl`

### 6.10 阶段 I：E2E 验收与文档

#### I1：全链路 E2E

- **目标**：验证从 query 到反馈到导出全流程。
- **修改文件**：
  - `tests/e2e/test_mcp_rag_observability_flow.py`
- **实现类/函数**：
  - E2E 测试用例。
- **验收标准**：
  - 发起 query 后有 run/span/artifact。
  - 提交 feedback 后能导出 SFT/bad case/reward。
- **测试方法**：
  - `pytest -q tests/e2e/test_mcp_rag_observability_flow.py`

#### I2：CLI 辅助脚本

- **目标**：提供本地排查和导出入口。
- **修改文件**：
  - `scripts/inspect_runs.py`
  - `scripts/export_training_data.py`
- **实现类/函数**：
  - `inspect_runs --limit 20`
  - `export_training_data --format sft|bad_cases|reward`
- **验收标准**：
  - 命令行可查看最近 runs。
  - 命令行可导出 JSONL。
- **测试方法**：
  - 手动运行脚本。

#### I3：文档完善

- **目标**：说明如何开启观测、查看 Dashboard、导出数据。
- **修改文件**：
  - `README.md`
  - `DEV_SPEC_AGENT_OBSERVABILITY.md`
- **实现类/函数**：
  - 无。
- **验收标准**：
  - 新用户能根据文档跑通最小流程。
- **测试方法**：
  - 按 README 手动执行。

### 6.11 进度跟踪表

| ID | 任务 | 状态 | 备注 |
|---|---|---|---|
| A1 | 新增 mcp_rag_observability 包结构 | [ ] | |
| A2 | SQLite schema 与迁移脚本 | [ ] | |
| A3 | Repository 基础接口 | [ ] | |
| B1 | Trace 数据模型 | [ ] | |
| B2 | RunContext / SpanContext 生命周期 | [ ] | |
| B3 | 装饰器埋点 | [ ] | |
| B4 | TraceSink Router 与外部观测工具预留 | [ ] | |
| C1 | Token usage 记录 | [ ] | |
| C2 | CostCalculator | [ ] | |
| D1 | Cache key 生成 | [ ] | |
| D2 | SQLiteCacheStore | [ ] | |
| D3 | EmbeddingCache | [ ] | |
| D4 | VisionCaptionCache | [ ] | |
| D5 | CacheMetrics 聚合 | [ ] | |
| E1 | MCP tool 入口创建 Run | [ ] | |
| E2 | Query Pipeline span 埋点 | [ ] | |
| E3 | Ingestion Pipeline span 埋点 | [ ] | |
| E4 | 原 JSONL trace 兼容策略 | [ ] | |
| F1 | McpRagObservabilityService | [ ] | |
| F2 | Runs 页面 | [ ] | |
| F3 | Cache Metrics 页面 | [ ] | |
| F4 | Feedback 页面 | [ ] | |
| F5 | Training Data 页面 | [ ] | |
| G1 | EvalRunner 写入 SQLite | [ ] | |
| G2 | 基础检索指标 | [ ] | |
| G3 | FailureClassifier | [ ] | |
| H1 | FeedbackService | [ ] | |
| H2 | SFTExporter | [ ] | |
| H3 | BadCaseExporter | [ ] | |
| H4 | RewardFeatureExporter | [ ] | |
| H5 | JSONLExporter 通用导出 | [ ] | |
| I1 | 全链路 E2E | [ ] | |
| I2 | CLI 辅助脚本 | [ ] | |
| I3 | 文档完善 | [ ] | |

---

## 7. 可扩展性与未来展望

### 7.1 后续内置 Agent Runtime

当项目后续从 MCP Server 扩展为包含本地 Agent Runtime 的系统时，可以复用当前 `Run / Span / Event / Artifact` 模型，把 `agent_run` 作为新的 run type。

可新增采集内容：

- Agent planning span。
- MCP tool call span。
- Tool observation artifact。
- Final synthesis span。
- Task-level feedback。

当前阶段不采集外部 Agent 的隐藏状态，不要求 MCP Client 主动上报 trace。

### 7.2 Agentic RL 数据准备

当前阶段不训练 RL，但保存训练所需的基本材料：

- 用户问题。
- 检索结果。
- 重排结果。
- 最终答案。
- 成本、延迟、错误。
- 人工反馈。
- 评测指标。

后续可导出：

```json
{
  "task": "...",
  "trajectory": [
    {"type": "tool_call", "tool": "query_knowledge_hub", "input": "...", "output": "..."}
  ],
  "final_answer": "...",
  "reward_features": {
    "human_rating": "good",
    "hit_rate_at_k": 1.0,
    "latency_ms": 820,
    "cost_usd": 0.003
  }
}
```

### 7.3 与外部生态对接

默认不依赖外部服务，但预留两种对接方式。

在线输出：

- `LangSmithTraceSink`
- `LangfuseTraceSink`
- `OpenTelemetryTraceSink`
- `PhoenixTraceSink`

离线导出：

- 类 LangSmith JSON。
- OpenTelemetry trace。
- Parquet 数据湖。
- Hugging Face datasets。
- OpenAI fine-tuning JSONL。
- DPO preference pair 格式。

### 7.4 推荐迭代路线

优先级从高到低：

1. 先让 Run/Span/Artifact 可靠落库。
2. 再把 token/cost/cache 统计做准。
3. 再接入 RAG query 和 ingestion 的关键 span。
4. 再加 Dashboard 复盘页面。
5. 再做 feedback 和训练导出。
6. 最后考虑内置 Agent Runtime 的完整 trajectory 导出。

核心判断标准：

> 每个增量都必须让系统多回答一个具体问题：为什么错、哪里贵、哪里慢、哪里缓存命中、哪些样本值得拿去训练。
