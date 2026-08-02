# ADR-0021：Golden Set 评估流水线与可复现指标聚合

- **状态**：当前采用
- **日期**：2026-08-02
- **决策范围**：如何把版本化 Golden Set 转换成真实检索请求、逐用例指标和可复现汇总报告
- **说明**：本文记录当前实现和面试相关原理，不修改 `DEV_SPEC.md`

## 一句话结论

EvalRunner 把 JSON Golden Set 当作稳定测试输入，对每个 Case 执行真实 QueryEngine，再把同一响应交给配置
出的 Evaluator；报告同时保留逐例证据和按“实际返回次数”计算的聚合指标。

## 为什么需要 Golden Set

人工挑几个问题观察答案容易产生确认偏差，也无法判断一次 Chunk、Embedding 或 Rerank 修改是否退化。
Golden Set 把问题和期望证据提交到版本库，使同一输入能在不同实现和不同提交上重复运行。

当前每条用例支持：

```json
{
  "case_id": "hybrid-retrieval",
  "query": "How are dense and sparse results combined?",
  "expected_chunk_ids": ["chunk-a"],
  "expected_sources": ["retrieval.md"],
  "expected_answer": "...",
  "metadata": {"collection": "docs", "top_k": 5}
}
```

Chunk ID 用于精确 Hit Rate/MRR；Source 可用于跨重新切分版本的粗粒度断言；expected_answer 为 Ragas 等
生成质量指标提供 reference。不同目标不应混在一个模糊的 `expected` 字段里。

## 为什么 Runner 必须走真实 QueryEngine

直接把预制候选传给 Evaluator 只能测试指标公式，不能发现查询预处理、Dense/Sparse、Fusion、过滤或 Rerank
的回归。EvalRunner 根据每条 Case 构造 QueryRequest，并调用与本地查询入口相同的 QueryEngine。

```text
Golden Case
    │
    ▼
QueryRequest(collection, top_k, filters)
    │
    ▼
Hybrid QueryEngine
    │
    ▼
QueryResponse(items)
    │
    ▼
Evaluator -> per-case metrics
```

Runner 当前聚焦 Retrieval，因此响应 answer 为空；生成式答案评估需要未来接入完整 KnowledgeService，而
不能用 expected_answer 冒充模型实际输出。

## 为什么同时保存逐例结果和平均值

单个平均 Hit Rate 只能说明“整体变好或变坏”，不能定位哪个问题退化。报告为每个 Case 保存：

- query 与 case_id；
- expected_chunk_ids；
- retrieved_chunk_ids 与 retrieved_sources；
- 本用例 metrics。

顶层 metrics 用于质量门禁，逐例数据用于定位 bad case。只有二者同时存在，评估才既可自动化又可诊断。

## 缺失指标如何聚合

不同 Case 可能不具备某项指标的前置条件，例如没有 expected_answer 时 RagasEvaluator 不返回
context_precision。聚合分母不能固定为 Case 总数，否则“无法计算”会被隐式当成 0。

当前为每个指标分别维护：

```text
totals[metric] += value
counts[metric] += 1
aggregate[metric] = totals[metric] / counts[metric]
```

这与数据库 `AVG(nullable_column)` 的语义类似：只平均真实观测值。报告的 Case 明细仍能看出哪些指标缺失。

## Golden 文件为什么需要严格校验

评估数据也是代码的一部分。空 query、重复 case_id、错误字段类型或空字符串 ID 如果被静默接受，会让
质量报告失去可信度。Loader 在发起任何查询前完成全文件校验，并为没有 case_id 的用例生成稳定的
`case-001` 编号。

失败采用 fail-fast，不在运行一半后才发现坏数据，避免产出只有部分 Case 的报告。

## CLI 的职责

`scripts/evaluate.py` 只负责：

1. 加载统一 Settings；
2. 装配 QueryEngine 和 Evaluator/CompositeEvaluator；
3. 选择配置或参数指定的 Golden Set；
4. 输出稳定 JSON EvaluationReport；
5. 用退出码区分成功和失败。

指标计算、数据校验和查询编排都留在 EvalRunner，Dashboard 后续可以复用同一服务，不复制 CLI 逻辑。

## 当前边界

- 当前 Runner 按 Case 串行查询，避免在线 Provider 突发并发；Evaluator 内部仍可并行；
- Golden Set 中的真实 chunk IDs 需要在 H5 随稳定测试语料补齐；
- Retrieval Runner 不生成答案，Ragas 生成质量指标需要完整响应服务；
- 报告目前只输出 stdout，历史持久化与趋势对比属于 H4；
- 外部模型存在非确定性时，应固定模型版本、温度和测试索引快照。

## 面试表达建议

1. “Golden Set 把主观调试转成版本化、可重复的质量回归输入。”
2. “Runner 走真实 QueryEngine，指标单测和端到端检索回归是两层不同证据。”
3. “报告既保留聚合指标，也保留逐 Case 的 expected/retrieved 证据用于定位退化。”
4. “缺失指标按各自实际观测次数聚合，不把无法计算偷换成零分。”
5. “Golden 数据先全量 fail-fast 校验，避免运行一半后产出不完整报告。”
