# ADR-0024：端口契约测试与高风险边界

- **状态**：当前采用
- **日期**：2026-08-02
- **决策范围**：VectorStore、Reranker、Evaluator 和 DocumentManager 的契约如何防止实现漂移
- **说明**：本文记录实现原理和面试知识，不修改 `DEV_SPEC.md`

## 为什么仅测试具体实现不够

模块化系统通过 `Protocol` 和 Factory 替换后端。若测试只验证 Chroma、Cross-Encoder 或某个
Evaluator 的内部算法，新 Provider 可能“方法名相同”，却在返回形状、空输入、计数语义或
失败行为上与调用方预期不同。

契约测试关注的是所有实现都必须遵守的可观察行为：

```text
调用方
  │  稳定输入 / 输出 / 错误语义
  ▼
Port Contract
  ├─ Fake 实现：快速验证调用方假设
  ├─ 本地实现：Chroma / BM25 / Custom
  └─ 可插拔实现：未来 Provider
```

这与普通单元测试的差别不在测试框架，而在断言对象。单元测试可以关心算法中间步骤；契约
测试只关心替换实现后仍必须成立的边界。

## VectorStore 删除契约

`delete_by_metadata(filters) -> int` 同时承担数据安全和可观测性：

1. 空条件必须拒绝，不能把 `{}` 解释为“删除整个 Collection”；
2. 多字段条件使用 AND 语义，只删除完全匹配的记录；
3. 返回值是实际删除数量，不是布尔值或请求数量；
4. 没有匹配项返回 `0`；
5. 重复执行相同删除仍返回 `0`，即删除操作对重试幂等；
6. 不匹配的 generation 和 collection 必须保持不变。

其中空条件是 fail-closed 设计。DocumentManager 会组合 `source_path + collection` 删除文档，
若调用链中的路径或 Collection 丢失，宁可暴露错误，也不能扩大删除范围。

## Reranker Factory 契约

Factory 不只是字符串到构造函数的字典，它还负责统一装配策略：

```text
settings.rerank
  ├─ backend=none -> NoneReranker（不创建线程池包装）
  └─ 其他 backend -> 具体实现 -> FallbackReranker
                                  ├─ top_m
                                  ├─ timeout_seconds
                                  └─ 失败保留 Fusion 顺序
```

因此契约测试需要验证：

- backend 缺失、未知或注册名为空时 fail-fast；
- `top_m` 必须是正整数，布尔值不能利用 Python 的 `bool <: int` 关系蒙混过关；
- `timeout_seconds` 必须是正数，字符串形式也不隐式接受；
- 非 `none` Provider 统一获得 Fallback 包装；
- LLM Reranker 能收到顶层 `llm` 配置，但 Factory 不能反向修改原 settings。

最后一点是配置所有权边界：Factory 可以为具体后端创建合并后的配置副本，不能把派生字段
写回全局配置，否则后续组件会观察到与文件内容不一致的运行时状态。

## Evaluator 契约

Custom Evaluator 的输出是 `dict[str, float]`。Chunk 级指标始终提供：

```text
hit_rate = 是否至少命中一个期望 Chunk
mrr      = 1 / 第一个相关 Chunk 的排名
```

只有 Golden Case 提供有效 `expected_sources` 时，才增加 `source_hit_rate` 和
`source_mrr`。这避免用缺失标注计算一个看似合法的 `0.0`，让报表误以为该 Case 已做来源
评估。

Source Golden Set 使用稳定文件名而不是 Chunk ID，但路径分隔符取决于数据生成平台。
`Path.name` 会按**当前运行系统**解释路径，Linux 不会把反斜杠当作 Windows 分隔符。因此
来源规范化必须先把 `\\` 转成 `/`，再取 basename 和 `casefold()`：

```text
C:\\golden\\GUIDE.PDF
/runtime/guide.pdf
            │
            └─ guide.pdf -> match
```

这保证 Windows 生成的 Golden Set 可以在 Linux CI 上评估，反之亦然。

## DocumentManager 为什么保留控制面记录

DocumentManager 的现有契约测试覆盖四个存储的协调删除。Vector、BM25 或 Image 任一路失败
时，SQLite integrity record 不删除，因为它是下一次重试定位文档的控制面锚点：

```text
外部数据全部删除成功 -> 删除 integrity record
任一外部存储失败     -> 保留 integrity record + 返回 errors
```

这不是数据库事务意义上的原子删除，而是可恢复的 best-effort saga。返回的 `DeleteResult`
明确记录各存储的删除数量和错误，调用方不能只依赖异常判断成功。

## 面试时如何解释

可以从三层回答：

1. **类型层**：`Protocol` 约束方法签名和主要输入输出类型；
2. **行为层**：契约测试约束幂等、计数、排序、空输入和失败语义；
3. **装配层**：Factory 测试约束配置路由、默认实现、包装策略和配置不可变性。

静态类型只能证明“看起来能调用”，不能证明两个实现对空过滤、无命中或超时具有相同语义。
真正可插拔需要类型契约和行为契约同时成立。

## 关联实现

- `src/ports/ingestion.py`
- `src/ports/query.py`
- `src/ports/evaluation.py`
- `src/libs/evaluator/custom_evaluator.py`
- `tests/unit/test_vector_store_contract.py`
- `tests/unit/test_reranker_factory.py`
- `tests/unit/test_custom_evaluator.py`
- `tests/unit/test_document_manager.py`
