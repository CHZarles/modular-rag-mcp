# ADR-0019：Ragas 评估边界与指标前置条件

- **状态**：当前采用
- **日期**：2026-08-02
- **决策范围**：如何把可选 Ragas 框架适配到稳定的 BaseEvaluator 契约，并正确处理各指标的数据前置条件
- **说明**：本文记录当前实现和面试相关原理，不修改 `DEV_SPEC.md`

## 一句话结论

RagasEvaluator 把项目的 `EvaluationCase + QueryResponse` 转换成 Ragas 单样本数据集，只返回通过范围校验
的指标；Ragas 是可选依赖，未安装时在选择该后端时给出明确 ImportError，不影响 custom evaluator。

## 三个指标分别回答什么

- **Faithfulness**：答案中的陈述能否由检索上下文支持，关注“有没有脱离证据胡说”；
- **Answer Relevancy**：答案是否真正回应问题，关注“答得是否切题”；
- **Context Precision**：排在前面的上下文是否与参考答案相关，关注“检索结果里有多少有效证据”。

它们不是同一个“正确率”。Faithfulness 高但 Answer Relevancy 低，可能是答案忠于上下文却答非所问；
Answer Relevancy 高但 Faithfulness 低，可能是回答切题却产生幻觉。

## 领域对象如何映射到 Ragas

```text
EvaluationCase.query            -> question
QueryResponse.answer            -> answer
Citation.text                   -> contexts
EvaluationCase.expected_answer  -> ground_truth
```

上下文优先使用 citations，因为它们是最终答案真实引用的证据；没有 citation 时才回退到检索 items 正文。
这样评估的是响应实际暴露的证据边界，而不是把所有内部候选都当成答案依据。

## Context Precision 为什么需要参考答案

当前采用的经典 Ragas `context_precision` 需要 ground truth/reference 来判断每段上下文是否相关。
`EvaluationCase.expected_answer` 是可选字段，因此缺少参考答案时不能伪造一个 reference，也不能用模型
自己的答案充当 ground truth，否则会形成自证循环并虚高指标。

当前策略是：

- 有 expected_answer：运行全部三个指标；
- 无 expected_answer：只运行 faithfulness 和 answer_relevancy；
- 后续报告只聚合评估器实际返回的指标。

“缺失指标”比“填 0”更准确。填 0 会把数据集不具备评估条件误报成模型质量差。

## 为什么使用适配器而不泄漏 Ragas 类型

BaseEvaluator 始终保持：

```python
evaluate(case, response) -> dict[str, float]
```

Ragas Dataset、Metric 对象和 EvaluationResult 都停留在适配器内部。这样 CompositeEvaluator、EvalRunner
和 Dashboard 不依赖 Ragas 版本，也可以同时组合 custom 指标。

测试通过注入 `RagasRunner` 验证输入映射与结果处理，不需要在线 LLM。生产默认 Runner 才延迟导入
`ragas` 和 `datasets` 并调用真实框架。

## 可选依赖为什么延迟导入

Ragas 会带来 Dataset、LLM 集成等较重依赖。若在模块顶层导入，项目即使只使用 custom evaluator，启动
时也会失败。当前只有工厂实际创建 `backend=ragas` 时才加载可选包。

因此：

- 未启用 Ragas：主应用、CLI 和 custom evaluator 正常工作；
- 启用但未安装：立即抛出包含安装方向的 ImportError；
- 已安装：Ragas 后端遵循相同 BaseEvaluator 契约。

这是 Plugin/optional dependency 常见边界：注册实现不等于强制加载实现的第三方运行时。

## 为什么校验分数

LLM-as-Judge 或聚合过程可能返回 NaN、Infinity、字符串或越界值。直接进入 EvalRunner 会污染平均值，
最终报告可能整体变成 NaN。

适配器只接受有限的 `[0, 1]` 数值，其他值视为该指标本次缺失。校验放在外部框架边界，而不是让下游
每个调用方重复防御。

## 当前边界

- Ragas 指标本身依赖 Judge LLM，存在成本、延迟、模型偏差和非确定性；
- 单元测试验证适配契约，不等于验证某个 Ragas/LLM 组合的评判质量；
- 真实运行仍需配置 Ragas 能识别的 LLM/Embedding，当前不把项目自有 Provider 对象强行泄漏过去；
- 不同 Ragas 大版本 API 可能变化，变化应收敛在默认 Runner 内；
- 评估结果必须结合 Hit Rate、MRR 和固定 golden set，不能只看 LLM Judge 指标。

## 面试表达建议

1. “Faithfulness、Answer Relevancy 和 Context Precision 分别衡量证据一致性、问题相关性和上下文质量。”
2. “Context Precision 缺 reference 时我选择不返回，而不是造 ground truth 或填零。”
3. “Ragas 类型被封装在适配器后面，系统内部只依赖 BaseEvaluator 的指标字典。”
4. “可选依赖延迟导入，只有配置 ragas 后端时才要求安装，custom 路径不受影响。”
5. “框架边界会过滤 NaN 和越界分数，避免一次异常污染整份聚合报告。”
