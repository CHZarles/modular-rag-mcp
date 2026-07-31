# ADR-0008：Reranker 的精排、超时与回退机制

- **状态**：当前采用
- **日期**：2026-07-31
- **决策范围**：可选重排后端如何接入查询主链路，以及失败时如何保留可用结果
- **说明**：本文记录当前实现知识、示例和顾虑，不修改 `DEV_SPEC.md`

## 先用一句话理解

`FallbackReranker` 不负责判断哪个 Chunk 更相关。真正的相关性判断由它内部包装的重排
后端完成。

它负责的是：

> 限制需要精排的候选数量，等待重排后端返回；如果后端异常或超时，就放弃本次精排，
> 继续使用原来的 RRF 排名。

```text
Dense + Sparse
      │
      ▼
RRF 粗排结果
      │
      ▼
FallbackReranker
      │
      ├─ 后端成功 ──> 使用后端给出的精排结果
      │
      └─ 异常/超时 ─> 使用原来的 RRF 结果，并标记 fallback=true
```

## 它和前面模块的关系

| 阶段 | 主要职责 |
|------|----------|
| DenseRetriever | 根据完整查询寻找语义相似的 Chunk |
| SparseRetriever | 根据关键词和 BM25 寻找 Chunk |
| RRFFusion | 根据两张召回榜单的名次生成统一粗排 |
| HybridQueryEngine | 编排查询预处理、两路召回、融合、过滤和重排 |
| FallbackReranker | 调用可选精排后端，并在失败时保住 RRF 粗排结果 |

因此 Reranker 位于 RRF 之后。它不会重新搜索知识库，只会处理已经召回的候选项。

## 完整例子

RRF 已经给出三个候选项：

```text
RRF 排名
1. Chunk A：租约过期后，新 worker 可以接管任务
2. Chunk B：SQLite WAL 可以防止记录写坏
3. Chunk C：旧 worker 发布时必须校验 claim_token
```

查询是：

```text
为什么旧 worker 在租约过期后不能发布？
```

### 重排成功

真实重排后端可能认为 C 最直接回答问题，于是返回：

```text
精排结果
1. Chunk C
2. Chunk A
3. Chunk B
```

此时 `FallbackReranker` 使用后端结果，并按调用方要求截取 `top_k`。

### 重排异常

假设后端调用抛出：

```text
RuntimeError: backend unavailable
```

系统不会让整个知识查询失败，而是返回原来的 RRF 顺序：

```text
1. Chunk A
2. Chunk B
```

回退结果保留原来的：

- Chunk 正文和 metadata；
- RRF 分数；
- `source="fusion"`；
- RRF 排名顺序。

只在 `debug` 中增加回退说明：

```python
{
    "rrf": {"score": 0.03252},
    "rerank": {
        "backend": "llm",
        "fallback": True,
        "reason": "backend unavailable",
    },
}
```

这个标记表示“本次使用了 RRF 结果兜底”，不表示 RRF 自己失败。

## top_m 和 top_k 的区别

两个参数解决不同问题：

```text
top_m：最多拿多少个粗排候选去做昂贵的精排
top_k：最终需要返回多少个结果
```

例如：

```text
输入候选：100 个
top_m：   30
top_k：    5
```

正常流程是：

```text
100 个 RRF 候选
   │
   ├─ 只取前 30 个交给重排后端
   ▼
重排后的前 5 个
```

当前实现还保证重排输入至少覆盖 `top_k`。如果调用方要求 `top_k=50`，即使配置
`top_m=30`，也会把前 50 个候选交给后端，否则后端不可能返回 50 个结果。

实际送入后端的数量还受上游候选数量限制。例如上游只有 10 个候选，`top_m=30` 也只能
重排这 10 个。

## 超时是怎么实现的

配置示例：

```yaml
rerank:
  backend: none
  top_m: 30
  timeout_seconds: 10
```

启用真实后端后，编排器会把调用放到一个工作线程中，并最多等待 10 秒：

```text
主查询线程                         重排工作线程
    │                                  │
    ├─ 提交 rerank() ─────────────────>│
    │                                  │ 调用真实重排后端
    ├─ 最多等待 10 秒                  │
    │                                  │
    ├─ 10 秒内完成：使用精排结果 <─────┤
    │
    └─ 超过 10 秒：立即返回 RRF 回退结果
```

超时和后端主动抛出的 `TimeoutError` 会被区分，但二者最终都会进入安全回退。

## 为什么线程超时不等于杀死后端

Python 不能安全地强制终止一个正在运行的普通线程。因此 10 秒超时真正保证的是：

```text
查询调用方不再继续等待，并先拿 RRF 结果完成本次查询
```

它不能保证：

```text
正在运行的后端代码会在第 10 秒立刻停止
```

如果任务尚未开始，可以取消；如果已经开始，它可能继续运行到网络请求自己结束。因此
真实的 OpenAI-compatible 客户端仍然需要配置 HTTP 连接和读取超时。核心层超时是最后
一道响应时间保护，不是 Provider 网络超时的替代品。

## Factory 如何装配

`RerankerFactory` 根据配置选择已注册的实现：

```text
backend: none
    │
    └─> NoneReranker
        不改变 RRF 顺序，也不创建超时线程

backend: 某个已注册的真实后端
    │
    └─> 真实后端
          │
          └─> 外层自动包装 FallbackReranker
```

`top_m` 必须是正整数，`timeout_seconds` 必须是正数；布尔值不会被当作数字接受。配置错误
会在创建 Reranker 时立即报错，而不是等到第一次查询才暴露。

## 当前实际运行状态

当前项目配置为：

```yaml
rerank:
  backend: none
```

所以当前真实行为是：

```text
RRF 排名 -> NoneReranker 截取 Top-K -> 返回
```

查询没有调用 MiniMax，也没有调用 Cross-Encoder，结果顺序和 D5 完成时相同。本次 D6
提供的是重排后端的接入和故障保护机制，不代表检索质量已经因为精排而提高。

由于没有启用 LLM Rerank，本次也没有创建 `config/prompts/rerank.txt`。

## 当前实现边界与顾虑

### 1. 超时后工作线程可能继续运行

查询已经回退并返回，但后端任务可能稍后才结束。高并发下如果大量后端调用同时卡住，
可能临时积累后台线程和未完成请求。需要由 Provider 网络超时、并发限制和压力测试共同
控制。

### 2. 每个带超时的真实重排调用都会创建一个单线程执行器

这对当前 MVP 简单直接，并且默认 `none` 后端完全没有这项开销。如果未来高并发压测证明
线程创建成为瓶颈，再考虑共享执行器或异步 I/O；当前不提前增加全局线程生命周期管理。

### 3. 回退原因会进入候选项 debug

这便于解释为什么本次没有采用精排结果，但真实后端的异常文本不应包含密钥或敏感请求
内容。接入 Provider 时需要检查其异常信息，必要时在适配器边界进行脱敏。

### 4. 成功结果的 source 和分数由真实后端负责

`FallbackReranker` 不会替后端伪造相关性分数。未来的 Cross-Encoder 或 LLM Reranker
需要自行返回正确的 `source="rerank"`、最终排名和可解释的分数或调试信息。

### 5. 当前没有真实重排质量证据

异常回退测试只能证明系统可用性，不能证明精排能提高结果质量。接入真实后端后仍需使用
固定黄金查询集比较 RRF 与 Rerank 的 MRR、NDCG 和 Hit Rate，确认收益大于延迟与成本。

## 验证证据

- `tests/unit/test_reranker_fallback.py` 覆盖成功重排、候选限制、后端异常、超时、空输入、
  参数校验和 Factory 包装；
- 测试确认回退不修改原始候选对象，只为返回副本增加 `fallback=true`；
- 当前完整回归结果：`230 passed, 5 skipped`；
- D6 相关代码已通过 Ruff、全量源码 Mypy、compileall 和 `git diff --check`。

## 关联实现

- `src/core/query_engine/reranker.py`
- `src/libs/reranker/reranker_factory.py`
- `src/libs/reranker/base_reranker.py`
- `src/core/query_engine/hybrid_search.py`
- `src/ports/query.py` 中的 `BaseReranker`
- `config/settings.yaml`
- `tests/unit/test_reranker_fallback.py`
