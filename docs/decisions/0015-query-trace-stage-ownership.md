# ADR-0015：Query Trace 阶段语义、计时与编排层所有权

- **状态**：当前采用
- **日期**：2026-08-01
- **决策范围**：一次混合检索如何记录阶段、耗时、实现来源和降级信息，以及并行召回下如何保持 Trace 稳定
- **决策关系**：建立在 ADR-0006 的 RRF、ADR-0007 的混合检索、ADR-0008 的 Reranker 回退、ADR-0011 的重排原理，以及 F1/F2 已实现的 TraceContext 与 JSON Lines 基础能力之上
- **说明**：本文记录当前实现和约束，不修改 `DEV_SPEC.md`

## 先用一句话理解

Query Trace 由查询编排层维护高层阶段语义：每个阶段都记录 `method`、`provider`、结构化
`details` 和单调时钟耗时；Dense 与 Sparse 可以并行执行，但 Trace 按固定路线顺序落盘；单路
故障记录为可观察的降级，而不是伪装成空结果。

```text
query_processing
    ├── dense_retrieval  ┐
    └── sparse_retrieval ┘ 并行
            ↓
          fusion
            ↓
      metadata_filter
            ↓
          rerank
            ↓
       query_engine
```

## 为什么需要统一的阶段结构

早期组件已经有少量 `record_stage()` 调用，但记录形状并不统一：有的只写 `count`，有的写
`method`，都没有稳定的耗时和失败状态。这样的日志可以辅助临时调试，却无法可靠支持：

- 对比 Dense、Sparse、Fusion 和 Rerank 的延迟；
- 判断结果为空还是某一路后端失败；
- 按 Provider 聚合性能和错误率；
- 在 Dashboard 中用同一方式展示不同阶段；
- 为一次查询解释是否发生了降级。

当前统一阶段格式为：

```json
{
  "stage": "dense_retrieval",
  "timestamp": "2026-08-01T12:00:00+00:00",
  "elapsed_ms": 12.35,
  "data": {
    "method": "dense",
    "provider": "DenseRetriever",
    "details": {
      "status": "success",
      "requested_top_k": 20,
      "result_count": 8
    }
  }
}
```

字段职责如下：

| 字段 | 含义 |
|------|------|
| `stage` | 查询链路中稳定的逻辑阶段名 |
| `timestamp` | 阶段记录完成时的 UTC ISO 时间，便于跨 Trace 排序 |
| `elapsed_ms` | 阶段自身耗时，使用单调时钟计算 |
| `method` | 本阶段采用的策略，例如 `dense`、`sparse`、`fuse`、`none` |
| `provider` | 实际执行组件类型，例如 `DenseRetriever`、`RRFFusion` |
| `details` | 输入输出数量、状态、限制参数和错误原因 |

`method` 是相对稳定的行为分类，`provider` 是当前实现来源。二者不能合并：不同 Provider 可以
实现同一种方法，同一个 Provider 也可能根据配置选择不同策略。

## 为什么用单调时钟统计耗时

墙上时间用于回答“什么时候发生”，单调时钟用于回答“持续了多久”。系统时间可能因为 NTP、
容器时间同步或人工调整发生跳变，直接用两个 UTC 时间戳相减可能得到不可靠的耗时。

当前计时遵循：

```python
started = time.monotonic()
result = operation()
elapsed_ms = (time.monotonic() - started) * 1000.0
```

Trace 同时保留两类时间：

- `timestamp`、`started_at`、`finished_at` 使用 UTC 墙上时间；
- `elapsed_ms`、`total_elapsed_ms` 使用单调时钟。

这样既能在日志中定位事件，又能得到不受系统时钟校准影响的耗时。

## Query Processing 记录什么

`query_processing` 包围 `QueryProcessor.process()`，记录：

```json
{
  "method": "process",
  "provider": "QueryProcessor",
  "details": {
    "status": "success",
    "keyword_count": 2,
    "filter_count": 0
  }
}
```

当前不把完整用户查询复制进 Trace。查询文本可能包含敏感信息，也可能非常长；性能 Trace 只需
记录处理规模和使用的方法。如果将来确实需要请求回放，应定义单独的脱敏、采样和保留策略，
不能顺手把原始输入塞入所有可观测日志。

预处理异常时，阶段仍记录 `status=error` 和错误字符串，然后重新抛出原异常。Trace 是观察
业务行为，不应吞掉或改写业务错误。

## 并行召回如何同时保证真实性与确定性

Dense 和 Sparse 互不依赖，通过线程池并行执行。若按照 Future 的完成顺序直接追加 Trace，
网络和机器负载会导致阶段顺序在不同运行间随机变化：

```text
运行 A：dense → sparse
运行 B：sparse → dense
```

这会增加日志比较、快照测试和 Dashboard 展示的噪声。当前采用两步策略：

1. 每个工作线程独立返回候选、真实耗时和错误；
2. 主线程按配置中的固定 Dense、Sparse 路线顺序写入 Trace。

线程返回的内部结果类似：

```python
_RouteOutcome(
    candidates=[...],
    elapsed_ms=12.35,
    error=None,
)
```

因此执行仍是并行的，耗时仍是每条路线的真实耗时，但序列化后的阶段顺序是确定的。稳定顺序
不表示 Dense 一定先执行完，只表示 Trace 使用固定的表达顺序。

## 召回成功、空结果和错误必须区分

Dense 或 Sparse 成功时记录：

```json
{
  "status": "success",
  "requested_top_k": 20,
  "result_count": 0
}
```

`result_count=0` 仍然是成功：后端完成查询，只是没有命中。

后端异常时记录：

```json
{
  "status": "error",
  "requested_top_k": 20,
  "result_count": 0,
  "error": "embedding unavailable"
}
```

如果 Dense 失败而 Sparse 成功，查询继续使用 Sparse 结果。反之亦然。这是 ADR-0007 中的单路
降级语义，现在有了可观测表达。

只有所有已启用召回路线都失败时，查询才抛出 `all retrieval routes failed`。不能把它返回成
普通空数组，否则上层会把系统故障误判为知识库没有相关内容。

```text
success + result_count=0  → 查询成功但没有命中
一条 error + 一条 success → 单路降级，仍可返回结果
所有路线 error           → 系统无法完成召回，抛出异常
```

## Fusion 记录的规模语义

Fusion 阶段记录：

```json
{
  "method": "fuse",
  "provider": "RRFFusion",
  "details": {
    "status": "success",
    "input_list_count": 2,
    "input_count": 30,
    "output_count": 10,
    "top_k": 10
  }
}
```

`input_list_count` 表示有多少条非空召回路线参与融合，`input_count` 表示各路线候选数量之和。
二者必须分开，因为 30 个候选可能来自两路各 15 个，也可能只来自一路降级结果。

Fusion 异常时记录错误阶段并继续抛出。RRF 是召回结果变成统一排名的必要步骤，不存在一个不
改变语义的通用回退，因此不能像单路召回或 Reranker 那样随意跳过。

## Metadata Filter 为什么也单独计时

F3 的核心验收阶段不包含过滤，但实际查询在 Fusion 与 Rerank 之间执行 metadata filter。
如果不记录它，`query_engine` 总耗时可能明显大于已知阶段之和，却无法解释差值。

过滤阶段记录输入、输出和过滤字段数量：

```json
{
  "method": "filter",
  "provider": "ExactMetadataFilter",
  "details": {
    "status": "success",
    "input_count": 10,
    "output_count": 6,
    "filter_count": 2
  }
}
```

Trace 记录过滤条件数量，不默认复制所有过滤值。具体值仍可能包含租户、用户或业务敏感信息；
只有诊断确有需要并完成脱敏设计后，才应扩大记录范围。

## Rerank 的三种可观察状态

Reranker 是可选增强，不能只记录“调用过”。当前区分三种状态。

### 正常成功

```json
{
  "method": "cross_encoder",
  "provider": "CrossEncoderReranker",
  "details": {
    "status": "success",
    "input_count": 10,
    "backend_input_count": 5,
    "output_count": 3,
    "top_k": 3,
    "fallback": false
  }
}
```

`backend_input_count` 专门反映 `top_m` 限制。它与 `input_count` 的差值说明有多少 Fusion 候选
没有送入昂贵精排后端。

### 显式禁用

```json
{
  "method": "none",
  "provider": "NoneReranker",
  "details": {
    "status": "skipped",
    "fallback": false
  }
}
```

`skipped` 不是错误，也不是回退。它表示配置明确要求保持 Fusion 顺序。

### 异常或超时回退

```json
{
  "method": "llm",
  "provider": "LLMReranker",
  "details": {
    "status": "fallback",
    "fallback": true,
    "reason": "timeout after 10 seconds"
  }
}
```

回退时仍返回 Fusion 顺序，候选 debug metadata 继续保留回退原因；Trace 则提供面向整次查询的
阶段级观察。二者用途不同，不能只保留其中一个。

## 为什么高层阶段由编排层拥有

`dense_retrieval`、`sparse_retrieval` 和 `fusion` 描述的是完整查询中的高层步骤。只有
HybridQueryEngine 同时知道：

- 当前启用了哪些路线；
- 两路是否并行；
- 配置的 top-k；
- 哪一路失败后是否继续；
- Fusion 的总输入规模；
- 最终是否发生查询级降级。

因此这些阶段的统一语义和计时由编排层维护。具体 Retriever 和 Fusion 实现仍接收同一个
`trace` 参数，并继续向 Embedding、向量库、BM25 或模型 Provider 传递；未来的底层事件应使用
更具体的名称，例如 `embedding_request` 或 `vector_store_query`，而不是再次写一个同名
`dense_retrieval`。

这避免同一次查询出现两条含义模糊的同名阶段：一条只有 `count`，另一条包含完整计时和状态。

Rerank 的成功、跳过和内部回退则由 Reranker 编排器拥有，因为只有它知道 `top_m`、超时策略、
后端名称以及是否使用 Fusion 顺序回退。HybridQueryEngine 只在一个未遵守内部回退契约的自定义
Reranker 直接抛出异常时，执行最后一道查询级保护。

## Query Engine 总耗时是什么

`query_engine` 阶段覆盖从预处理开始，到最终 Rerank 结果产生为止：

```json
{
  "method": "hybrid_search",
  "provider": "HybridQueryEngine",
  "details": {
    "result_count": 3,
    "errors": {}
  }
}
```

它不是整个用户请求的总耗时。之后还可能发生响应构建、图片编码、MCP 序列化和 transport 写出。
整条请求的时间由最外层 `TraceContext.total_elapsed_ms` 表示。

## 为什么 HybridQueryEngine 不调用 finish

TraceContext 的所有权属于创建它的最外层调用方，而不是某个中间组件。HybridQueryEngine 结束
时，后续可能还要记录：

```text
response_build
multimodal_assembly
MCP serialization
```

因此查询引擎只追加阶段，不调用 `finish()`，也不直接写 JSON Lines。正确生命周期是：

```python
trace = TraceContext(trace_type="query")
results = engine.search(request, trace=trace)
response = response_builder.build(request, results, trace=trace)
trace.finish()
write_trace(trace.to_dict())
```

如果中间层提前结束 Trace，总耗时会被冻结，后续阶段虽然仍可能被追加，却不再落入正确的总耗时
窗口。

## 当前限制

- 当前阶段详情记录 Provider 的类名，不是跨版本稳定的供应商 ID；需要长期聚合时应由组件公开稳定名称。
- 错误字符串适合本地诊断，但远程、多租户部署前应增加脱敏和长度限制。
- TraceContext 的阶段列表是进程内结构；线程池中的底层组件若自行并发写入大量事件，需要进一步定义顺序或 Span 模型。
- 当前 F3 负责产生阶段数据，Trace 的创建、结束、采样和持久化仍由最外层调用方负责。
- `elapsed_ms` 是阶段墙钟耗时；并行 Dense 与 Sparse 的耗时不能简单相加来推导 Query 总耗时。

最后一点尤其重要。并行阶段的正确关系是：

```text
retrieval wall time ≈ max(dense_elapsed, sparse_elapsed)
```

而不是：

```text
retrieval wall time = dense_elapsed + sparse_elapsed
```

## 验证重点

集成测试验证：

- 一次 Query Trace 包含 `query_processing`、`dense_retrieval`、`sparse_retrieval`、`fusion` 和 `rerank`；
- 每个核心阶段都有非负 `elapsed_ms`；
- 每个核心阶段都有非空 `method`、`provider` 和字典类型 `details`；
- Dense 和 Sparse 收到的是调用方提供的同一个 TraceContext；
- `trace.to_dict()` 保留 `trace_type=query`；
- 单路失败会分别记录错误和成功状态，并继续返回另一条路线的结果；
- Reranker 后端失败会记录 `fallback=true`、Provider 和原因，同时保持 Fusion 顺序。

验证的重点不是精确毫秒数。CI 调度和机器负载不可控，测试只断言耗时存在且非负，避免把性能
测试错误地写成脆弱的固定时间断言。

## 决策结果

当前采用以下规则：

- Query 高层 Trace 使用统一的 `method/provider/details/elapsed_ms` 结构；
- 阶段耗时使用单调时钟，UTC 时间只负责事件定位；
- Dense 与 Sparse 并行执行，但按固定路线顺序记录；
- 成功空结果、单路错误和全路错误使用不同语义；
- 高层召回与 Fusion 阶段由 HybridQueryEngine 统一拥有，避免重复打点；
- Reranker 自己记录成功、禁用和内部回退，HybridQueryEngine 保留最终异常保护；
- Trace 不默认复制原始查询和完整过滤值；
- 查询引擎不结束或持久化 Trace，生命周期继续由最外层调用方管理。
