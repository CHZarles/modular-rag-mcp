# Developer Specification: Agent/RAG Observability, Evaluation and Training Data Layer

> 版本：0.1  
> 类型：补充性 DEV_SPEC  
> 适用范围：在现有 Modular RAG MCP Server 上新增本地优先的 Agent/RAG 可观测、评测、缓存统计与训练数据沉淀流程。  
> 核心约束：Python、SQLite、pytest、本地优先、轻量级、零外部服务依赖。

---

## 目录

- 1. 项目概述
- 2. 核心特点
- 3. 技术选型
- 4. 测试方案
- 5. 系统架构与模块设计
- 6. 项目排期
- 7. 可扩展性与未来展望

---

## 1. 项目概述

### 1.1 设计理念

现有项目已经定义了模块化 RAG、MCP Server、Dashboard 和 RAG 质量评估能力，但评测体系目前更偏向“离线 golden set + 指标计算”。这份补充规范的目标，是为项目增加一层完整的数据闭环：

```text
Agent/RAG 调用
  -> 本地 Trace 采集
  -> Token / Cost / Cache / Latency 统计
  -> Dashboard 复盘
  -> 人工轻量反馈
  -> Bad Case 与训练样本导出
  -> 反哺 RAG / Prompt / Agent 策略优化
```

本系统不依赖 LangSmith，但借鉴 LangSmith / OpenTelemetry 的核心思想：一次任务应能被拆解为可追踪、可评估、可复盘的结构化运行轨迹。

关键取舍：

- **本地优先**：SQLite 是唯一事实源，不依赖外部观测平台。
- **先收对数据，再谈训练**：V1 不实现强化学习训练流程，但从数据结构上为 SFT、DPO、Agentic RL 预留空间。
- **轻量 HITL**：只做用户反馈、原因、修正答案、修正来源，不做复杂标注平台。
- **双层缓存视角**：应用级缓存由本项目管理；模型运行时 KV/prefix cache 只做指标采集，不在应用层直接管理。
- **最小侵入**：通过 Trace SDK、装饰器、context manager 接入现有 RAG/MCP 流程，不把业务代码改成观测代码。

### 1.2 项目定位

本补充模块定位为：

> 一个本地优先的 Agent/RAG 可观测、评测与训练数据沉淀层，用于记录完整 Agent Run / RAG Query 轨迹，统计 token、cost、latency、cache 命中情况，收集轻量人工反馈，并导出可用于后续训练和分析的数据集。

它不是：

- 不是 LangSmith 的完整复刻。
- 不是分布式 APM 系统。
- 不是多用户标注平台。
- 不是 RL 训练框架。
- 不是外部 SaaS 观测平台。

它要解决的核心问题是：

1. 每次 Agent/RAG 为什么这么回答？
2. 哪些步骤慢、贵、失败、缓存没命中？
3. 检索、重排、生成分别出了什么问题？
4. 用户认为答案好不好？正确答案和正确来源是什么？
5. 这些数据能否导出为未来训练或调优可用的样本？

---

## 2. 核心特点

### 2.1 Run / Span / Event / Artifact 追踪模型

系统采用四层观测模型：

| 层级 | 含义 | 示例 |
|---|---|---|
| `Run` | 一次完整任务 | 用户通过 MCP 发起一次知识库查询 |
| `Span` | Run 内的一个执行步骤 | LLM 调用、Embedding、Dense Retrieval、Rerank |
| `Event` | Span 内的离散事件 | cache_hit、retry、error、fallback |
| `Artifact` | 可复用产物 | prompt、answer、retrieved_chunks、tool_output |

所有评估指标、缓存事件、成本统计、人工反馈、训练导出都围绕 `run_id` 与 `span_id` 关联。

### 2.2 SQLite 单一事实源

运行时只写 SQLite：

- 便于本地开发和教学演示。
- 易于 Dashboard 查询、过滤、聚合。
- 可支持事务，保证 run/span/event/cache/feedback 的一致性。
- 后续 JSONL、Parquet、训练样本、LangSmith-like 格式都通过 Exporter 从 SQLite 导出。

### 2.3 LangSmith-like 本地 Dashboard

Dashboard 提供本地可视化能力：

- Run 列表与详情。
- Span 树与耗时瀑布。
- LLM / Tool / Retrieval 输入输出查看。
- token、cost、latency 聚合。
- cache hit/miss 统计。
- 用户反馈与坏 case 查看。
- 训练数据导出入口。

### 2.4 双层缓存设计

缓存体系分为两类：

1. **Application Cache**
   - 由本项目在应用层管理。
   - SQLite 持久化。
   - V1 优先实现 Embedding Cache 与 Vision Caption Cache。
   - LLM Response / Retrieval / Rerank Cache 先预留接口。

2. **Model Runtime Cache Observability**
   - 不直接管理模型内部 KV cache。
   - 通过 `RuntimeMetricsAdapter` 采集本地推理服务暴露的指标。
   - V1 提供 `NoopRuntimeMetricsAdapter`。
   - 未来适配 vLLM、SGLang、llama.cpp、Ollama 等。

### 2.5 Token、Cost、Latency 统一统计

每个 LLM/Embedding/Vision/Rerank span 记录：

- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `cached_tokens`
- `reasoning_tokens`
- `cost_usd`
- `latency_ms`
- `tokens_per_second`
- `provider`
- `model`

其中 cost 由本地配置的 pricing table 计算，若 provider 返回 usage，则优先使用 provider usage。

### 2.6 轻量 Human-in-the-loop

V1 只实现轻量反馈：

- `good / bad` 整体评价。
- 可选 `reason`。
- 可选 `correct_answer`。
- 可选 `correct_sources` / `correct_chunk_ids`。

不做复杂审核队列、多人标注、偏好对比 UI、轨迹评分工作台。

### 2.7 面向训练数据沉淀

V1 支持导出：

- SFT 修正样本：来自 `correct_answer`。
- Bad case 数据集：来自 bad feedback 与失败 trace。
- Reward features：质量、成本、延迟、缓存、人工反馈等特征。

预留：

- Preference pair exporter。
- Tool-use trajectory exporter。
- Agentic RL rollout exporter。

### 2.8 与现有 RAG/MCP 模块渐进集成

新增 `src/agent_observability/` 作为核心子系统：

- 不直接替换现有 `observability`。
- Dashboard 增加页面读取 SQLite。
- 原有 JSONL trace 可逐步迁移或双写。
- 现有 RAG Pipeline 通过 tracer 接口埋点。

---

## 3. 技术选型

### 3.1 总体技术栈

| 类别 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.11+ | 与现有项目一致 |
| 主存储 | SQLite | 本地优先，零外部服务依赖 |
| 测试 | pytest | 单元、集成、E2E 统一测试入口 |
| Dashboard | Streamlit | 与现有 Dashboard 方向一致 |
| Schema | dataclasses / TypedDict / Pydantic 可选 | V1 优先轻量，若现有项目已使用 Pydantic 可复用 |
| 配置 | YAML | 复用现有 `settings.yaml` 风格 |
| 导出 | JSONL | V1 默认导出格式 |
| Parquet | 可选预留 | 后续用于批量分析和训练数据管线 |
| Token 估算 | provider usage 优先，tiktoken 可选 | 不强依赖 tiktoken |

### 3.2 可插拔架构设计

#### 3.2.1 核心接口

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

class BaseCacheStore:
    def get(self, namespace: str, cache_key: str) -> dict | None: ...
    def set(self, namespace: str, cache_key: str, value: dict, metadata: dict) -> None: ...
    def record_cache_event(self, event: dict) -> None: ...

class BaseRuntimeMetricsAdapter:
    def collect(self) -> dict: ...

class BaseExporter:
    def export(self, output_path: str, filters: dict | None = None) -> "ExportResult": ...
```

#### 3.2.2 配置管理

通过 `settings.yaml` 增加 `agent_observability` 配置段：

```yaml
agent_observability:
  enabled: true

  storage:
    backend: sqlite
    sqlite_path: ./data/db/agent_observability.db

  tracing:
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

  runtime_metrics:
    adapter: noop  # noop | vllm | sglang | llama_cpp | ollama

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
      - agent_runs
      - cache_metrics
      - feedback
      - training_data

  export:
    default_output_dir: ./exports/agent_observability
    formats:
      - jsonl
```

#### 3.2.3 插件化扩展点

| 扩展点 | V1 实现 | 未来实现 |
|---|---|---|
| TraceStore | SQLiteTraceStore | PostgresTraceStore |
| CacheStore | SQLiteCacheStore | RedisCacheStore |
| RuntimeMetricsAdapter | NoopRuntimeMetricsAdapter | VLLMAdapter、SGLangAdapter、OllamaAdapter |
| Exporter | JSONLExporter、SFTExporter、BadCaseExporter | ParquetExporter、LangSmithExporter、OpenTelemetryExporter |
| FeedbackCollector | DashboardFeedbackCollector | CLI、MCP Tool、浏览器插件 |
| FailureClassifier | RuleBasedFailureClassifier | LLM-as-judge FailureClassifier |

### 3.3 SQLite 数据模型

V1 使用以下表作为核心事实源。

#### 3.3.1 runs

记录一次完整任务。

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
- `agent_run`
- `ingestion`
- `evaluation`

#### 3.3.2 spans

记录 Run 内的步骤。

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

- `llm`
- `embedding`
- `vision_caption`
- `tool_call`
- `retrieval_dense`
- `retrieval_sparse`
- `fusion`
- `rerank`
- `cache`
- `storage`
- `evaluation`

#### 3.3.3 events

记录离散事件。

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
- `runtime_metrics`

#### 3.3.4 artifacts

保存 prompt、answer、retrieved chunks、tool outputs 等内容。

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

记录模型调用用量。

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

#### 3.3.6 cache_records

保存应用级缓存内容。

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
```

缓存 key 规则：

```text
cache_key = sha256(namespace + input_hash + provider + model + config_hash + prompt_version + index_version?)
```

#### 3.3.7 cache_events

记录每次缓存访问，用于命中率和节省成本分析。

```sql
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

`status`：

- `hit`
- `miss`
- `set`
- `bypass`
- `error`

#### 3.3.8 runtime_metrics

记录模型运行时指标。V1 只定义 schema，默认 adapter 返回空指标。

```sql
CREATE TABLE runtime_metrics (
  metric_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  span_id TEXT,
  runtime_name TEXT,
  kv_cache_hit_rate REAL,
  prefix_cache_hit_tokens INTEGER,
  prefill_tokens INTEGER,
  decode_tokens INTEGER,
  tokens_per_second REAL,
  gpu_memory_used_mb REAL,
  raw_metrics_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(span_id) REFERENCES spans(span_id)
);
```

#### 3.3.9 feedback_events

保存轻量人工反馈。

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

#### 3.3.10 eval_runs / eval_results

保存离线评测运行和结果。

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

Dashboard 新增四个页面。

#### 页面 1：Agent Runs

功能：

- 展示 run 列表。
- 按时间、状态、run_type、模型、反馈过滤。
- 显示总耗时、token、cost、cache 命中率、错误状态。
- 点击进入 run 详情。

Run 详情：

- Span 树。
- 每个 span 的耗时、输入、输出、错误。
- LLM messages / retrieved chunks / final answer。
- token usage 明细。
- cache events 明细。

#### 页面 2：Cache Metrics

功能：

- 按 namespace 统计 hit/miss。
- 展示 embedding cache、vision caption cache 的命中率。
- 展示 saved_tokens、saved_cost、latency_saved_ms。
- 列出最常命中的 cache key 与最近 miss case。

#### 页面 3：Feedback

功能：

- 对 run 追加 `good / bad` 反馈。
- 填写 reason。
- 填写 correct_answer。
- 填写 correct_sources / correct_chunk_ids。
- 查看 bad feedback 列表。

#### 页面 4：Training Data

功能：

- 导出 SFT correction JSONL。
- 导出 bad case JSONL。
- 导出 reward features JSONL。
- 显示导出数量与路径。

### 3.5 其余推荐技术点

#### 3.5.1 Failure Classification

V1 采用规则分类，不引入 LLM-as-judge：

```text
no_retrieval_hit       Top-K 未包含期望来源
wrong_ranking          期望来源存在但排名过低
insufficient_context   召回内容不足以回答
hallucination          用户反馈 bad 且提供 correct_answer
wrong_citation         用户提供 correct_sources，且与系统引用不一致
tool_error             tool span 出错
too_expensive          cost 超过阈值
too_slow               latency 超过阈值
user_intent_missed     用户反馈 bad 但检索命中正常
unknown                无法归因
```

#### 3.5.2 Reward Features

V1 不计算单一 reward 分数，只导出可解释特征：

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

未来训练时再根据任务定义组合 reward。

#### 3.5.3 Export Formats

V1 支持 JSONL：

- `sft_corrections.jsonl`
- `bad_cases.jsonl`
- `reward_features.jsonl`
- `runs.jsonl`

未来预留：

- `preference_pairs.jsonl`
- `tool_trajectories.jsonl`
- `parquet/`
- `opentelemetry.json`
- `langsmith_like.jsonl`

---

## 4. 测试方案

### 4.1 TDD 理念

本补充模块采用 TDD：

- 每个存储表先写 repository contract test。
- 每个 tracer 行为先写 span 生命周期测试。
- 每个 cache 行为先写 hit/miss/idempotency 测试。
- 每个 exporter 先写 golden output 测试。
- Dashboard 页面做轻量 smoke test，不追求复杂 UI 自动化。

### 4.2 分层测试

#### 4.2.1 单元测试

| 模块 | 测试重点 |
|---|---|
| `tracing` | run/span 生命周期、异常状态、嵌套 span |
| `storage` | SQLite schema 初始化、CRUD、事务一致性 |
| `cache` | cache key 稳定性、hit/miss/set、命中统计 |
| `metrics` | token usage 汇总、cost 计算 |
| `feedback` | good/bad 反馈写入与查询 |
| `export` | SFT、bad case、reward features JSONL 输出 |
| `runtime` | Noop adapter 返回稳定空指标 |

#### 4.2.2 集成测试

| 场景 | 验证点 |
|---|---|
| MCP tool trace | 一次 tool call 生成 run/span/artifact |
| RAG query trace | retrieval、fusion、rerank span 被正确记录 |
| Ingestion cache | 相同 chunk 第二次 embedding 命中缓存 |
| Vision caption cache | 相同 image hash 第二次 caption 命中缓存 |
| Feedback + Export | bad feedback 能导出 bad case |
| EvalRunner + Trace | golden case 评测结果能关联 run_id |

#### 4.2.3 E2E 测试

核心 E2E：

1. 启动临时 SQLite。
2. 摄取测试文档。
3. 发起一次 query。
4. 验证 run/span/event/artifact/token/cache 均有记录。
5. 提交 bad feedback 和 correct_answer。
6. 执行 export。
7. 验证生成 `sft_corrections.jsonl` 与 `bad_cases.jsonl`。

### 4.3 RAG 质量评估

保留原有 RAG 评估思路，并补充观测字段：

| 指标 | 来源 | 说明 |
|---|---|---|
| `hit_rate_at_k` | golden set + retrieved chunks | Top-K 是否命中期望来源 |
| `mrr` | golden set + ranking | 正确来源排名质量 |
| `ndcg_at_k` | golden set + relevance | 排序整体质量 |
| `faithfulness` | Ragas/未来 evaluator | 答案是否被上下文支持 |
| `answer_relevancy` | Ragas/未来 evaluator | 答案是否回应问题 |
| `latency_ms` | trace | 端到端耗时 |
| `total_tokens` | token_usage | token 成本 |
| `cost_usd` | pricing calculator | 金钱成本 |
| `cache_hit_rate` | cache_events | 缓存效果 |
| `failure_type` | failure classifier | 失败归因 |

V1 的重点不是追求复杂 judge，而是确保每次评测都能回答：

- 哪个策略更准？
- 哪个策略更快？
- 哪个策略更省？
- 哪类 query 最容易失败？
- 哪些坏 case 可以沉淀为训练数据？

---

## 5. 系统架构与模块设计

### 5.1 整体架构图

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                              MCP / Agent Client                           │
│                    Copilot / Claude / Local Agent / CLI                   │
└────────────────────────────────────┬──────────────────────────────────────┘
                                     │
                                     ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                              MCP Server / App                             │
│                                                                           │
│  ┌──────────────────────┐      ┌───────────────────────────────────────┐  │
│  │ query_knowledge_hub  │      │ ingestion / eval / future agent runs  │  │
│  └──────────┬───────────┘      └──────────────────┬────────────────────┘  │
│             │                                     │                       │
│             ▼                                     ▼                       │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                         Trace SDK                                    │  │
│  │  start_run / span / event / artifact / token_usage / cache_event     │  │
│  └──────────────────────────────┬──────────────────────────────────────┘  │
│                                 │                                         │
│                                 ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                         RAG Pipeline                                 │  │
│  │ Query Processing -> Dense -> Sparse -> Fusion -> Rerank -> Answer    │  │
│  └──────────────┬────────────────────┬──────────────────┬──────────────┘  │
│                 │                    │                  │                 │
│                 ▼                    ▼                  ▼                 │
│       ┌────────────────┐   ┌────────────────┐  ┌─────────────────────┐   │
│       │ Application    │   │ Runtime Metrics │  │ Evaluation /         │   │
│       │ Cache          │   │ Adapter         │  │ Feedback             │   │
│       └───────┬────────┘   └────────┬───────┘  └──────────┬──────────┘   │
└───────────────┼─────────────────────┼─────────────────────┼──────────────┘
                │                     │                     │
                ▼                     ▼                     ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                            SQLite Fact Store                              │
│ runs / spans / events / artifacts / token_usage / cache / feedback / eval  │
└────────────────────────────────────┬──────────────────────────────────────┘
                                     │
                ┌────────────────────┴────────────────────┐
                ▼                                         ▼
┌───────────────────────────────┐          ┌───────────────────────────────┐
│      Streamlit Dashboard      │          │            Exporters           │
│ Runs / Cache / Feedback /     │          │ JSONL / SFT / Bad Cases /      │
│ Evaluation / Training Data    │          │ Reward Features / Future       │
└───────────────────────────────┘          └───────────────────────────────┘
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
│       └── agent_observability.db
├── exports/
│   └── agent_observability/
│       ├── runs.jsonl
│       ├── sft_corrections.jsonl
│       ├── bad_cases.jsonl
│       └── reward_features.jsonl
├── scripts/
│   ├── export_training_data.py
│   ├── inspect_runs.py
│   └── migrate_observability_db.py
├── src/
│   ├── agent_observability/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── tracing/
│   │   │   ├── __init__.py
│   │   │   ├── models.py
│   │   │   ├── tracer.py
│   │   │   ├── context.py
│   │   │   └── decorators.py
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
│   │   ├── runtime/
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   └── noop_adapter.py
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
│           │   ├── agent_runs.py
│           │   ├── cache_metrics.py
│           │   ├── feedback.py
│           │   └── training_data.py
│           └── services/
│               └── agent_observability_service.py
└── tests/
    ├── unit/
    │   ├── test_trace_models.py
    │   ├── test_sqlite_trace_store.py
    │   ├── test_tracer_lifecycle.py
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
    │   └── test_agent_observability_flow.py
    └── fixtures/
        ├── golden_test_set.json
        ├── sample_runs.json
        └── expected_exports/
```

### 5.3 模块职责说明表

| 模块 | 职责 | 不负责 |
|---|---|---|
| `tracing` | 创建 run/span/event/artifact，记录生命周期 | 具体业务逻辑 |
| `storage` | SQLite schema、迁移、repository 查询 | Dashboard 展示逻辑 |
| `cache` | 应用级缓存、cache key、命中事件 | 模型内部 KV cache 管理 |
| `metrics` | token/cost/latency 汇总 | 调用真实模型 |
| `runtime` | 采集本地推理服务运行时指标 | 控制推理引擎 |
| `feedback` | 轻量人工反馈写入与查询 | 多人审核工作流 |
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
  -> 写入 artifacts: chunks / captions / embeddings metadata
  -> finish_run(status="success" | "failed")
```

V1 重点：

- 相同 chunk 文本不重复 embedding。
- 相同图片 hash 不重复 caption。
- 记录 cache 节省的 token/cost/latency。

#### 5.4.2 Query Flow

```text
MCP Client 调用 query_knowledge_hub
  -> tracer.start_run(run_type="mcp_tool_call")
  -> artifact: user_query
  -> Query Processing span
  -> Dense Retrieval span
  -> Sparse Retrieval span
  -> Fusion span
  -> Rerank span
  -> Optional Answer Generation span
  -> artifact: retrieved_chunks / reranked_chunks / answer
  -> token_usage/cost/runtime_metrics
  -> finish_run
```

若后续接入 Agent runtime：

```text
Agent Run
  -> LLM planning span
  -> MCP tool call span
      -> nested RAG query spans
  -> LLM synthesis span
  -> final answer artifact
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
  -> 写入 exports/agent_observability/
```

### 5.5 配置驱动设计示例

#### 5.5.1 开启观测

```yaml
agent_observability:
  enabled: true
  storage:
    backend: sqlite
    sqlite_path: ./data/db/agent_observability.db
```

#### 5.5.2 只开启 embedding cache

```yaml
agent_observability:
  cache:
    enabled: true
    embedding:
      enabled: true
    vision_caption:
      enabled: false
```

#### 5.5.3 接入本地模型运行时观测

```yaml
agent_observability:
  runtime_metrics:
    adapter: vllm
    endpoint: http://localhost:8000/metrics
```

V1 中 adapter 默认为 `noop`，配置存在但不要求真实服务。

---

## 6. 项目排期

### 6.1 阶段总览

| 阶段 | 名称 | 目的 | 状态 |
|---|---|---|---|
| A | 数据模型与工程基座 | 建立包结构、配置、SQLite schema、测试基座 | [ ] |
| B | Trace SDK | 实现 Run/Span/Event/Artifact 生命周期 | [ ] |
| C | Token / Cost / Runtime Metrics | 记录模型用量和运行时指标 | [ ] |
| D | Application Cache | 实现 embedding 与 vision caption 缓存 | [ ] |
| E | RAG/MCP 接入 | 将 tracer 接入现有 ingestion/query/MCP 流程 | [ ] |
| F | Dashboard 页面 | 展示 runs、cache、feedback、training export | [ ] |
| G | Evaluation 增强 | 连接 golden set、trace、失败归因、成本指标 | [ ] |
| H | HITL 与训练导出 | 轻量反馈和 SFT/bad case/reward JSONL 导出 | [ ] |
| I | E2E 验收与文档 | 全链路验收、脚本、文档完善 | [ ] |

### 6.2 阶段 A：数据模型与工程基座

#### A1：新增 agent_observability 包结构

- **目标**：创建补充模块目录，保证可 import。
- **修改文件**：
  - `src/agent_observability/__init__.py`
  - `src/agent_observability/config.py`
  - 各子包 `__init__.py`
  - `tests/unit/test_trace_models.py`
- **实现类/函数**：
  - `AgentObservabilitySettings`
  - `load_agent_observability_settings(settings)`
- **验收标准**：
  - 包可以被 import。
  - 默认配置可加载。
- **测试方法**：
  - `pytest -q tests/unit/test_trace_models.py`

#### A2：SQLite schema 与迁移脚本

- **目标**：建立 SQLite 表结构。
- **修改文件**：
  - `src/agent_observability/storage/schema.sql`
  - `src/agent_observability/storage/migrations.py`
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
  - `src/agent_observability/storage/repositories.py`
  - `src/agent_observability/storage/sqlite_store.py`
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

### 6.3 阶段 B：Trace SDK

#### B1：Trace 数据模型

- **目标**：定义 Run、Span、Event、Artifact 的 Python 数据结构。
- **修改文件**：
  - `src/agent_observability/tracing/models.py`
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
  - `src/agent_observability/tracing/context.py`
  - `src/agent_observability/tracing/tracer.py`
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
  - `src/agent_observability/tracing/decorators.py`
  - `tests/unit/test_tracer_lifecycle.py`
- **实现类/函数**：
  - `trace_span(name: str, span_type: str)`
- **验收标准**：
  - 被装饰函数自动创建 span。
  - 返回值和异常行为不被改变。
- **测试方法**：
  - `pytest -q tests/unit/test_tracer_lifecycle.py -k decorator`

### 6.4 阶段 C：Token / Cost / Runtime Metrics

#### C1：Token usage 记录

- **目标**：记录 provider 返回的 usage。
- **修改文件**：
  - `src/agent_observability/metrics/token_usage.py`
  - `src/agent_observability/storage/repositories.py`
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
  - `src/agent_observability/metrics/cost_calculator.py`
  - `config/pricing.yaml`
  - `tests/unit/test_cost_calculator.py`
- **实现类/函数**：
  - `CostCalculator.calculate(provider, model, usage) -> float`
- **验收标准**：
  - 已知模型能算出稳定 cost。
  - 未知模型返回 0 并记录 warning event。
- **测试方法**：
  - `pytest -q tests/unit/test_cost_calculator.py`

#### C3：RuntimeMetricsAdapter

- **目标**：预留本地模型运行时指标采集。
- **修改文件**：
  - `src/agent_observability/runtime/base.py`
  - `src/agent_observability/runtime/noop_adapter.py`
  - `tests/unit/test_cost_calculator.py`
- **实现类/函数**：
  - `BaseRuntimeMetricsAdapter`
  - `NoopRuntimeMetricsAdapter.collect()`
- **验收标准**：
  - noop adapter 返回空指标但接口稳定。
- **测试方法**：
  - `pytest -q tests/unit/test_cost_calculator.py -k runtime`

### 6.5 阶段 D：Application Cache

#### D1：Cache key 生成

- **目标**：统一 cache key 规则。
- **修改文件**：
  - `src/agent_observability/cache/cache_key.py`
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
  - `src/agent_observability/cache/cache_store.py`
  - `tests/unit/test_sqlite_cache_store.py`
- **实现类/函数**：
  - `SQLiteCacheStore.get()`
  - `SQLiteCacheStore.set()`
  - `SQLiteCacheStore.record_cache_event()`
- **验收标准**：
  - miss/hit/set 都写入 `cache_events`。
  - hit_count 正确递增。
- **测试方法**：
  - `pytest -q tests/unit/test_sqlite_cache_store.py`

#### D3：EmbeddingCache

- **目标**：为 embedding 调用提供缓存包装。
- **修改文件**：
  - `src/agent_observability/cache/embedding_cache.py`
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
  - `src/agent_observability/cache/vision_caption_cache.py`
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
  - `src/agent_observability/cache/cache_metrics.py`
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

#### F1：AgentObservabilityService

- **目标**：封装 Dashboard 查询。
- **修改文件**：
  - `src/observability/dashboard/services/agent_observability_service.py`
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

#### F2：Agent Runs 页面

- **目标**：展示 run 列表与详情。
- **修改文件**：
  - `src/observability/dashboard/pages/agent_runs.py`
- **实现类/函数**：
  - `render_agent_runs_page()`
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
  - `src/agent_observability/feedback/feedback_service.py`
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
  - `src/agent_observability/evaluation/eval_runner.py`
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
  - `src/agent_observability/evaluation/metrics.py`
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
  - `src/agent_observability/evaluation/failure_classifier.py`
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
  - `src/agent_observability/feedback/models.py`
  - `src/agent_observability/feedback/feedback_service.py`
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
  - `src/agent_observability/export/sft_exporter.py`
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
  - `src/agent_observability/export/bad_case_exporter.py`
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
  - `src/agent_observability/export/reward_exporter.py`
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
  - `src/agent_observability/export/jsonl_exporter.py`
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
  - `tests/e2e/test_agent_observability_flow.py`
- **实现类/函数**：
  - E2E 测试用例
- **验收标准**：
  - 发起 query 后有 run/span/artifact。
  - 提交 feedback 后能导出 SFT/bad case/reward。
- **测试方法**：
  - `pytest -q tests/e2e/test_agent_observability_flow.py`

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
  - 无
- **验收标准**：
  - 新用户能根据文档跑通最小流程。
- **测试方法**：
  - 按 README 手动执行。

### 6.11 进度跟踪表

| ID | 任务 | 状态 | 备注 |
|---|---|---|---|
| A1 | 新增 agent_observability 包结构 | [ ] | |
| A2 | SQLite schema 与迁移脚本 | [ ] | |
| A3 | Repository 基础接口 | [ ] | |
| B1 | Trace 数据模型 | [ ] | |
| B2 | RunContext / SpanContext 生命周期 | [ ] | |
| B3 | 装饰器埋点 | [ ] | |
| C1 | Token usage 记录 | [ ] | |
| C2 | CostCalculator | [ ] | |
| C3 | RuntimeMetricsAdapter | [ ] | |
| D1 | Cache key 生成 | [ ] | |
| D2 | SQLiteCacheStore | [ ] | |
| D3 | EmbeddingCache | [ ] | |
| D4 | VisionCaptionCache | [ ] | |
| D5 | CacheMetrics 聚合 | [ ] | |
| E1 | MCP tool 入口创建 Run | [ ] | |
| E2 | Query Pipeline span 埋点 | [ ] | |
| E3 | Ingestion Pipeline span 埋点 | [ ] | |
| E4 | 原 JSONL trace 兼容策略 | [ ] | |
| F1 | AgentObservabilityService | [ ] | |
| F2 | Agent Runs 页面 | [ ] | |
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

### 7.1 本地模型运行时观测

当项目接入 vLLM / SGLang / llama.cpp / Ollama 后，可实现对应 adapter，采集：

- KV cache hit rate。
- Prefix cache hit tokens。
- Prefill / decode tokens。
- Tokens per second。
- GPU memory usage。
- Batch size。
- Queue time。

这些指标进入 `runtime_metrics`，用于分析本地模型部署后的吞吐、成本和缓存收益。

### 7.2 Agentic RL 数据准备

V1 不训练 RL，但保存训练所需的基本材料：

- 用户目标。
- 工具调用轨迹。
- 检索结果。
- 最终答案。
- 成本、延迟、错误。
- 人工反馈。
- 评测指标。

未来可导出：

```json
{
  "task": "...",
  "trajectory": [
    {"type": "llm", "input": "...", "output": "..."},
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

默认不依赖外部服务，但可以通过 exporter 对接：

- LangSmith-like JSON。
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
6. 最后考虑本地模型 runtime adapter 与复杂 agent trajectory。

核心判断标准：

> 每个增量都必须让系统多回答一个具体问题：为什么错、哪里贵、哪里慢、哪里缓存命中、哪些样本值得拿去训练。

