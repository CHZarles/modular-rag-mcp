# ADR-0020：并行评估器组合与指标所有权

- **状态**：当前采用
- **日期**：2026-08-02
- **决策范围**：多个独立 Evaluator 如何并发执行、合并指标并处理失败或重名
- **说明**：本文记录当前实现和面试相关原理，不修改 `DEV_SPEC.md`

## 一句话结论

CompositeEvaluator 对同一 Case/Response 并行 fan-out 到各评估后端，再按配置顺序确定性 fan-in；任何后端
失败都明确标出来源，指标重名直接拒绝，避免静默覆盖评估结论。

## 为什么适合并行

Custom 指标主要消耗 CPU，而 Ragas 等 Judge 指标大部分时间等待外部 LLM。各后端只读取同一份不可变
输入，彼此没有数据依赖，串行执行只会把等待时间相加。

```text
                    ┌─ CustomEvaluator ── hit_rate, mrr
Case + Response ────┼─ RagasEvaluator  ── faithfulness, relevancy
                    └─ Future backend  ── ...
                              │
                              ▼
                    deterministic merge
```

ThreadPoolExecutor 适合这里的 I/O 型并发，且不要求把 Evaluator 改成 async 接口。最大 worker 数等于后端
数量，不创建无界线程。

## 为什么结果按配置顺序收集

Future 的完成顺序受网络波动影响。若使用 `as_completed()` 直接合并，错误报告和重名冲突的先后会变得不
稳定。当前并行提交所有任务，但按 `evaluation.backends` 的声明顺序读取 Future，因此运行时间仍并行，
合并语义与报错却是确定的。

## 指标重名为什么不能后写覆盖

两个后端都返回 `score` 时，简单 `dict.update()` 会让后一个静默覆盖前一个。报告看起来合法，却无法知道
最后的值属于哪个算法。

当前维护 metric owner：首次出现时记录所有者，再次出现同名指标立即抛出包含两个 evaluator 名称的
ValueError。未来如果确实需要相同指标，可在后端契约中显式命名空间化，而不是依赖配置顺序覆盖。

## 后端失败为什么不返回部分报告

评估报告用于质量门禁。某个 Judge 后端失败时只返回 Custom 指标，调用方可能误以为配置的全部检查已
通过。当前把失败包装为：

```text
Evaluator 'ragas' failed: <root cause>
```

并让整次组合评估失败。这样失败是可观察的，调用方可以决定重试、移除后端或明确采用降级策略。若未来
需要 best-effort，报告契约必须同时携带 `failed_backends`，不能只偷偷丢指标。

## 工厂如何自动组合

- 一个 backend：工厂直接返回具体 Evaluator，保持最短调用链；
- 多个 backend：工厂按声明顺序创建组件并返回 CompositeEvaluator；
- `create_all()`：仍保留给需要自行编排组件的调用方。

入口只调用 `create_evaluator(settings)`，无需知道组合模式，也不需要根据列表长度写条件分支。

## 当前边界

- TraceContext 可能被多个评估线程共享，后续若大量写阶段事件需增加显式同步或子 Trace；
- Python 线程适合 I/O 型 Judge，不适合重 CPU 评估；
- 一个 Future 报错后 Context Manager 会等待其他已启动任务结束，当前不做强制取消；
- 指标名称目前是全局唯一，尚未引入 evaluator 命名空间；
- CompositeEvaluator 不负责跨 Case 并发，Case 级并发属于 EvalRunner 的职责边界。

## 面试表达建议

1. “多个评估器只读同一输入、互不依赖，适合 fan-out/fan-in 并行。”
2. “任务并行提交，但按配置顺序收集，兼顾延迟和确定性。”
3. “指标冲突不能 dict.update 静默覆盖，我会追踪 owner 并明确报错。”
4. “质量门禁默认 fail closed，某后端失败不会伪装成部分成功。”
5. “工厂根据 backend 数量自动返回单实现或 Composite，调用方只依赖 BaseEvaluator。”
