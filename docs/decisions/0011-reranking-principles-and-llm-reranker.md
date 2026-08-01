# ADR-0011：Rerank 原理与 LLM 精排边界

- **状态**：当前采用
- **日期**：2026-08-01
- **决策范围**：为什么检索链路需要二阶段精排，以及 LLM Reranker 在当前系统中负责什么
- **决策关系**：补充 ADR-0006 的 RRF 融合和 ADR-0008 的超时回退机制
- **说明**：本文记录当前实现知识、示例和限制，不修改 `DEV_SPEC.md`

## 先用一句话理解

Rerank 是二阶段检索的第二阶段：先用便宜、快速的方法从全量知识库中召回候选，再用更
精细但更昂贵的模型重新判断候选与查询的相关性。

```text
全量知识库
    │
    ├─ Dense：语义召回
    └─ BM25：关键词召回
            │
            ▼
         RRF 粗排
            │ Top-M candidates
            ▼
         Reranker
            │
            ▼
       最终 Top-K 结果
```

前一阶段追求高召回率，尽量不要漏掉正确内容；后一阶段追求高排序精度，让真正能回答问题
的内容排在前面。

## 为什么粗排还不够

当前粗排来自 Dense、BM25 和 RRF，它们解决的问题不同：

| 模块 | 主要能力 | 局限 |
|------|----------|------|
| Dense Retriever | 找到语义相似内容 | 可能把主题相近但没有回答问题的内容排在前面 |
| BM25 Retriever | 找到关键词高度匹配内容 | 不理解同义表达、因果关系和上下文 |
| RRF Fusion | 根据多张榜单的名次生成统一排序 | 不重新阅读 Chunk 正文，不判断哪个内容真正回答问题 |

例如查询：

```text
为什么旧 worker 在租约过期后不能发布结果？
```

RRF 可能给出：

```text
1. Chunk A：租约过期后，新 worker 可以接管任务
2. Chunk B：SQLite WAL 可以保护写入
3. Chunk C：旧 worker 发布时必须校验 claim_token
```

Chunk A 与查询主题最相似，但 Chunk C 才直接回答发布限制。Reranker 重新阅读查询和候选
正文后，可以调整为：

```text
1. Chunk C
2. Chunk A
3. Chunk B
```

Reranker 不会重新搜索知识库。它只能调整已经召回的候选，因此上游召回仍然必须保证正确
Chunk 进入候选集合。

## 为什么采用两阶段检索

更精细的模型无法经济地扫描整个知识库：

```text
知识库：         100,000 个 Chunk
Dense/BM25：     快速召回 100 个
RRF：            合并并粗排这 100 个
Reranker top_m：只精排前 30 个
最终 top_k：     返回前 5 个
```

如果让 LLM 直接读取十万个 Chunk，会同时遇到：

- 上下文窗口无法容纳；
- Prompt 构造和传输成本过高；
- 单次查询延迟不可接受；
- 输出更难保持稳定和可验证。

因此系统明确分工：

```text
Retriever：快速缩小搜索范围，优化 Recall
Reranker：仔细比较少量候选，优化 Precision 和前排质量
```

## 当前 LLM Reranker 使用 Listwise 排序

当前实现采用 Listwise Reranking，即把查询和一组候选同时交给 LLM。输入语义类似：

```json
{
  "query": "为什么旧 worker 不能发布结果？",
  "candidates": [
    {"id": "a", "text": "租约过期后，新 worker 可以接管任务"},
    {"id": "b", "text": "SQLite WAL 可以保护写入"},
    {"id": "c", "text": "旧 worker 发布时必须校验 claim_token"}
  ]
}
```

LLM 比较候选时可以考虑：

- 是否真正回答查询，而不只是主题相似；
- 是否包含关键约束、条件和因果关系；
- 回答是否直接、完整；
- 与其他候选相比，哪个更适合作为回答依据。

LLM 不生成最终答案，也不返回自由文本解释，只返回严格排名：

```json
{"ranked_ids": ["c", "a", "b"]}
```

代码根据 ID 找回原始 `RetrievalCandidate`，更新其来源和排名，然后截取 `top_k`。

## 为什么要求返回全部候选 ID

当前结构化输出必须满足：

1. JSON 顶层只能包含 `ranked_ids`；
2. `ranked_ids` 必须是字符串列表；
3. 每个候选 ID 必须且只能出现一次；
4. 不能虚构未知 ID；
5. 不能遗漏候选 ID。

要求完整排列，而不是让模型只返回 Top-K，可以避免以下问题：

- 模型静默丢失候选项；
- 模型返回不足 `top_k` 的结果；
- 模型把选择数量和排序职责混在一起；
- 输出异常被误认为一次成功精排。

模型负责相对排序，代码负责验证和截断。Markdown 代码块、额外字段、重复 ID、未知 ID 和
不完整列表都会被视为后端失败。

## top_m 与 top_k 的区别

两个参数控制不同边界：

```text
top_m：最多把多少个粗排候选交给昂贵的 Reranker
top_k：最终返回多少个结果
```

例如：

```text
RRF 候选：100 个
top_m：    30
top_k：     5
```

运行过程是：

```text
100 个 RRF 候选
      │
      ├─ 取前 30 个交给 LLM
      ▼
LLM 返回 30 个 ID 的完整新顺序
      │
      └─ 代码截取前 5 个
```

通常 `top_m` 大于 `top_k`，否则 Reranker 没有空间把粗排靠后的正确结果提升到最终结果中。
现有编排器还保证送入后端的候选数量至少覆盖调用方要求的 `top_k`。

## 排名与 score 的语义

当前 LLM 只返回候选的相对顺序，不返回经过校准的相关性分数。因此成功精排后：

- `rank` 更新为 LLM 给出的新名次；
- `source` 更新为 `rerank`；
- 原始正文和 metadata 保留；
- 原来的 RRF `score` 保留；
- 原来的 debug 信息保留。

这意味着精排结果中的 `score` 仍是上游粗排分数，不能解释为“LLM 认为有 90% 相关”。
当前 LLM Reranker 的权威输出是顺序，不是概率或绝对分数。

如果未来需要显示 LLM 分数，应先定义分数范围、校准方法和跨查询可比性，不能直接把模型
自由生成的数字当作稳定相关性指标。

## Factory 与 Provider 的分工

启用配置示例：

```yaml
llm:
  provider: openai
  model: your-model
  base_url: https://example.com/v1
  api_key: ${API_KEY}

rerank:
  backend: llm
  top_m: 30
  prompt_path: ./config/prompts/rerank.txt
  timeout_seconds: 10
```

`RerankerFactory` 把顶层 LLM 配置传给 `LLMReranker`。`LLMReranker` 再通过现有
`LLMFactory` 创建 OpenAI、Azure、DeepSeek 或 Ollama 客户端，不重复实现 HTTP Provider。

Factory 对 `backend=llm` 的实际装配是：

```text
FallbackReranker
    ├─ backend: LLMReranker
    │              └─ BaseLLM implementation
    ├─ top_m
    └─ timeout_seconds
```

当前配置仍是 `backend: none`，所以代码虽然已具备 LLM 精排能力，默认运行时不会产生额外
LLM 请求、延迟或费用。

## 失败为什么必须回退

LLM 精排是质量增强步骤，不应成为知识查询的单点故障。以下情况都可能发生：

- Provider 连接失败或限流；
- 调用超过精排时间预算；
- LLM 返回非 JSON 文本；
- LLM 输出重复、未知或缺失 ID；
- Prompt 文件缺失或配置非法。

具体后端抛出可读异常，外层 `FallbackReranker` 统一处理：

```text
LLM 成功
    └─ 使用 LLM 新顺序

LLM 异常或超时
    └─ 使用原始 RRF 顺序
       并写入 debug.rerank.fallback=true
```

回退结果示例：

```python
{
    "backend": "llm",
    "fallback": True,
    "reason": "model unavailable",
}
```

回退不表示 RRF 失败，只表示本次没有获得可靠的精排结果。详细的超时线程语义和回退数据
结构见 ADR-0008。

## 与 Cross-Encoder 的区别

当前和后续可用的排序方式处于不同成本与精度位置：

| 类型 | 工作方式 | 优点 | 局限 |
|------|----------|------|------|
| Dense Retriever | 独立编码查询和文档，再比较向量 | 非常快，可扫描海量向量 | 联合语义判断能力较弱 |
| Cross-Encoder | 联合编码每一对 `query + candidate` 并打分 | 分数稳定，通常快于生成式 LLM | 复杂业务指令和多候选比较能力有限 |
| LLM Listwise Reranker | 同时阅读查询与多个候选，直接输出顺序 | 能理解复杂约束、因果关系和业务 Prompt | 延迟、费用、上下文和输出稳定性成本更高 |

Cross-Encoder 通常为每个候选产生数值分数，再按分数排序；当前 LLM Reranker 直接产生相对
顺序。两者都位于 RRF 之后，并共享 `BaseReranker` 和外层回退契约。

## Rerank 不等于 LLM Rerank

Rerank 描述的是“对召回候选重新排序”这个阶段，不限定必须使用大模型。当前架构允许三种
后端承担同一个 `BaseReranker` 契约：

| backend | 实际行为 | 延迟与成本 | 适用场景 |
|---------|----------|------------|----------|
| `none` | 不做额外精排，保留 RRF 顺序 | 最低 | 基线、开发环境、高吞吐查询 |
| `cross_encoder` | 联合编码每个 `query + candidate` 并打分 | 中等 | 通用生产精排、稳定相关性排序 |
| `llm` | 同时阅读 Query 和 Top-M 候选并返回 ID 顺序 | 最高 | 复杂业务规则、因果问题、低 QPS 高价值查询 |

实现 `backend=llm` 的含义是提供一种可插拔能力，不是把所有查询永久绑定到生成式大模型。
只要配置仍为：

```yaml
rerank:
  backend: none
```

运行时就不会发生额外 LLM 调用。实现存在与能力启用是两件不同的事。

## 后端选型建议

工程默认顺序是：

```text
先建立 none + RRF 基线
        │
        ▼
需要提升通用排序质量时，优先评估 Cross-Encoder
        │
        ▼
只有复杂语义或业务规则仍无法解决时，再评估 LLM Reranker
```

### 优先使用 Cross-Encoder

以下场景通常应先选择 Cross-Encoder：

- 查询量较大，需要稳定的延迟和吞吐；
- 目标主要是一般语义相关性，而不是执行复杂业务判断；
- 希望得到可排序的数值分数；
- 成本敏感，不希望每次查询调用生成式模型；
- 希望相同输入尽可能产生一致结果。

Cross-Encoder 仍比 Dense 全库检索昂贵，因此也只处理 Top-M 候选，但通常比生成式 LLM
更适合作为生产环境的默认精排器。

### 考虑使用 LLM Reranker

以下场景才值得评估 LLM Reranker：

- 查询包含复杂条件、因果关系或多步业务规则；
- “主题相似”与“真正回答问题”之间存在明显差异；
- 需要通过 Prompt 表达领域专属排序标准；
- 查询频率较低，但单次结果价值较高；
- 已有可复用的本地模型或成本可控的 Provider；
- Cross-Encoder 在固定 golden test set 上仍无法达到目标。

### 不应启用 LLM Reranker

以下条件下通常不应启用：

- 高并发或严格的 P95/P99 延迟要求；
- Top-M 候选正文过长，Prompt 容易超过 Token 预算；
- 没有可靠的 Provider 超时和回退措施；
- 无法接受额外调用费用；
- 没有离线评估集，无法证明排序质量确实提升；
- 业务只需要简单关键词或通用语义相关性。

## 推荐的渐进启用方式

不应仅因为 LLM Reranker 已经实现就直接全量开启。推荐按以下顺序验证：

1. 使用 `backend=none` 记录 RRF 的 Hit Rate、MRR、延迟和坏 Case；
2. 使用相同 golden test set 评估 Cross-Encoder；
3. 只针对 Cross-Encoder 仍排序错误的复杂 Case 评估 LLM；
4. 同时比较质量增益、P95 延迟、调用费用和回退率；
5. 只有收益覆盖成本时，才决定全量启用或按查询类型路由。

未来可以引入条件路由，而不是全局只选一种后端：

```text
普通查询 ──────────────> Cross-Encoder
复杂规则/因果查询 ─────> LLM Reranker
Provider 不可用 ───────> RRF fallback
```

是否需要这种路由必须由真实评估数据决定，当前实现没有加入查询分类器，也不会自动升级到
LLM。现阶段的保守建议仍是：默认 `none`，需要精排时优先 Cross-Encoder，LLM 作为明确受控
的增强选项。

## 当前风险与约束

LLM Rerank 仍然存在模型层风险：

- **位置偏差**：模型可能偏好输入列表前部候选；
- **非确定性**：同一输入可能出现不同顺序；
- **Prompt 注入**：候选正文可能包含试图影响排序指令的文本；
- **上下文限制**：`top_m` 和 Chunk 长度共同决定 Prompt 大小；
- **延迟和费用**：每次查询增加一次生成式模型调用；
- **不可校准**：纯排序结果不能直接转换为可信概率。

当前缓解措施包括：

- 只处理 RRF 的 Top-M 候选；
- 使用 ID 而不是让模型复制正文；
- 严格校验完整排列；
- 配置精排超时；
- 任何失败都回退到确定的 RRF 顺序；
- 默认关闭真实 Rerank 后端。

未来若启用生产 LLM Rerank，还应评估候选顺序随机化、Prompt 注入隔离、Provider 级网络
超时、Token 预算、缓存和离线排序指标。

## 如何评估 Rerank 是否有效

不能只观察某几个查询的主观结果。应在固定 golden test set 上比较启用前后：

- Hit Rate@K：正确 Chunk 是否进入最终 Top-K；
- MRR：第一个正确 Chunk 是否被提升到更靠前位置；
- NDCG@K：多个相关结果的整体排序质量；
- 延迟：P50、P95 和超时比例；
- 回退率：结构错误、Provider 异常和超时次数；
- 单查询成本：额外 Token 与模型费用。

Rerank 的价值是以可接受的延迟和成本改善前排质量，而不是单纯让链路多调用一次模型。

## 相关实现

- `src/libs/reranker/llm_reranker.py`
- `src/libs/reranker/reranker_factory.py`
- `src/core/query_engine/reranker.py`
- `config/prompts/rerank.txt`
- `config/settings.yaml`
- `tests/unit/test_llm_reranker.py`
- `docs/decisions/0006-rrf-rank-fusion.md`
- `docs/decisions/0008-reranker-fallback-orchestration.md`
