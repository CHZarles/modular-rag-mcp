# ADR-0010：轻量检索指标与 Evaluator 工厂

- **状态**：当前采用
- **日期**：2026-08-01
- **决策范围**：如何在不依赖外部评估服务的情况下计算基础检索指标，以及如何按配置装配评估器
- **说明**：本文记录当前实现知识、示例和扩展边界，不修改 `DEV_SPEC.md`

## 先用一句话理解

`CustomEvaluator` 比较黄金 Chunk ID 与实际检索结果，输出 `hit_rate` 和 `mrr`；
`EvaluatorFactory` 根据 `evaluation.backends` 创建一个或多个评估器。

```text
EvaluationCase.expected_chunk_ids
                +
QueryResponse.items[].chunk_id
                │
                ▼
        CustomEvaluator
                │
                ▼
  {"hit_rate": 1.0, "mrr": 0.5}
```

整个过程只使用本地领域对象，不调用 LLM、Ragas、DeepEval 或其他网络服务，适合作为快速
回归测试和评估链路的最小可运行后端。

## 为什么沿用 EvaluationCase 和 QueryResponse

评估器遵循已有的 `BaseEvaluator` 端口：

```python
def evaluate(
    case: EvaluationCase,
    response: QueryResponse,
    trace: Any | None = None,
) -> dict[str, float]:
    ...
```

规格语义中的三个输入映射为：

| 评估输入 | 当前领域字段 |
|----------|--------------|
| query | `case.query` |
| golden_ids | `case.expected_chunk_ids` |
| retrieved_ids | `response.items[].chunk_id` |

不再新增 `evaluate(query, retrieved_ids, golden_ids)` 形式的第二套接口。这样
`CustomEvaluator` 可以直接交给现有 `EvalRunner`，以后增加 Ragas 或组合评估器时也不需要
修改运行器契约。

评估层消费领域响应，不依赖 MCP Tool、JSON-RPC 或传输格式。

## Hit Rate 的定义

当前按单条评估用例计算二值 Hit Rate：

```text
只要 retrieved_ids 与 golden_ids 存在交集：hit_rate = 1.0
否则：                                      hit_rate = 0.0
```

例如：

```text
golden_ids:    [chunk-b]
retrieved_ids: [chunk-a, chunk-b, chunk-c]
hit_rate:      1.0
```

它回答的是“Top-K 结果中是否至少命中一个正确 Chunk”，不表示命中了多少个黄金 Chunk，也
不是 precision 或 recall。跨多个评估用例的整体 Hit Rate 由 `EvalRunner` 对单用例结果取
算术平均值得到。

## MRR 的定义

MRR 使用第一个黄金 Chunk 在检索列表中的一基排名：

```text
mrr = 1 / first_relevant_rank
```

示例：

| 检索顺序 | 第一个正确结果排名 | mrr |
|----------|--------------------|-----|
| `[golden, a, b]` | 1 | `1.0` |
| `[a, golden, b]` | 2 | `0.5` |
| `[a, b, golden]` | 3 | `0.333...` |
| `[a, b, c]` | 未命中 | `0.0` |

如果有多个黄金 ID，只取最早出现的那个。列表中后续黄金 ID 不再改变该用例的 MRR。

## 空输入和未命中

以下情况统一返回稳定的零值：

```python
{"hit_rate": 0.0, "mrr": 0.0}
```

- `expected_chunk_ids` 为空；
- `response.items` 为空；
- 检索结果与黄金 ID 完全没有交集。

这里选择零值而不是省略指标或返回 `NaN`，是为了让报告聚合、JSON 序列化和回归阈值判断
保持确定性。调用方仍应区分“没有黄金标注”和“有标注但未命中”，需要这种区分时可从
原始 `EvaluationCase` 判断。

## Factory 如何装配

单后端配置：

```yaml
evaluation:
  backends: [custom]
```

创建单个评估器：

```python
evaluator = EvaluatorFactory.create(settings)
```

如果配置多个后端，应显式创建列表：

```yaml
evaluation:
  backends: [custom, ragas]
```

```python
evaluators = EvaluatorFactory.create_all(settings)
```

`create_all()` 保留配置中的声明顺序，便于上层按确定顺序运行和展示评估器。函数式入口
`create_evaluator()` 和 `create_evaluators()` 分别对应上述两个方法。

当前内置两个等价名称：

| backend | 实现 | 用途 |
|---------|------|------|
| `custom` | `CustomEvaluator` | 当前项目配置使用的名称 |
| `custom_metrics` | `CustomEvaluator` | 兼容文档和外部配置中的名称 |

## 插件式扩展

新评估器通过注册表接入，不需要修改工厂分支：

```python
EvaluatorFactory.register(
    "my_evaluator",
    lambda config: MyEvaluator(config),
)
```

构造函数接收整个 `evaluation` 配置区段，因此后端可以读取自己的模型、阈值或服务地址。
实现对象只要满足 `BaseEvaluator` 的结构化协议即可，不要求继承特定基类。

测试或插件卸载时可以移除注册项：

```python
EvaluatorFactory.unregister("my_evaluator")
```

## 配置错误何时暴露

工厂在创建阶段拒绝以下配置：

- `evaluation.backends` 缺失；
- `backends` 为空列表；
- 把 `backends` 写成单个字符串；
- 列表中包含空字符串或非字符串；
- 请求了未注册的 backend；
- 配置多个 backend 却调用只返回单实例的 `create()`。

这些错误不会推迟到第一次评估运行才出现。未知后端错误会同时列出已注册名称，便于定位
拼写或插件装载问题。

## 与 EvalRunner 的关系

职责边界如下：

```text
EvalRunner
   │ 遍历 EvaluationCase
   │ 获取 QueryResponse
   │ 调用一个或多个 BaseEvaluator
   ▼
CustomEvaluator
   │ 只计算单条用例的局部指标
   ▼
EvalRunner
   │ 加上 evaluator.name 前缀
   │ 聚合每项指标的算术平均值
   ▼
EvaluationReport
```

`CustomEvaluator` 不负责读取 golden test set、不执行检索、不生成报告，也不决定指标是否
达到发布门槛。它只把一条用例和一条响应转换为确定的指标字典。

## 当前限制

当前轻量实现有意只覆盖基础检索质量：

- Hit Rate 是二值命中，不衡量命中数量；
- MRR 只关注第一个正确结果；
- 不计算 Precision、Recall、NDCG；
- 不评估生成答案的忠实性或相关性；
- 不使用 `case.query` 和 `response.answer` 计算语义指标；
- `trace` 参数为端口兼容保留，当前实现不写入追踪事件。

这些能力应由后续独立评估器补充，而不是让 `CustomEvaluator` 同时承担远程模型调用、报告
编排和所有指标计算。

## 验证范围

单元测试覆盖：

- 正确结果位于第二名时得到 `hit_rate=1.0`、`mrr=0.5`；
- 无黄金 ID、空检索结果和完全未命中；
- 从完整 `Settings` 创建 `CustomEvaluator`；
- 动态注册自定义后端；
- 多后端按配置顺序创建；
- 未知后端及非法 `backends` 配置报错。

实现完成时，目标测试 `12/12`、全量单元测试 `234/234` 通过，Ruff 与 mypy 检查通过。

## 相关实现

- `src/ports/evaluation.py`
- `src/libs/evaluator/base_evaluator.py`
- `src/libs/evaluator/custom_evaluator.py`
- `src/libs/evaluator/evaluator_factory.py`
- `src/observability/evaluation/eval_runner.py`
- `tests/unit/test_custom_evaluator.py`
