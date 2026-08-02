# ADR-0022：检索 Recall 质量门禁与稳定相关性身份

- **状态**：当前采用
- **日期**：2026-08-02
- **决策范围**：如何用真实摄取和混合检索构建可重复的 Recall 回归测试，以及何时使用 Chunk 或 Source 作为相关性身份
- **说明**：本文记录当前实现和面试相关原理，不修改 `DEV_SPEC.md`

## 一句话结论

Recall E2E 用本地确定性 Embedding、真实 PDF 摄取、Chroma、BM25、RRF 和 EvalRunner 跑完整闭环；当前
Golden Set 以稳定文件名作为相关性身份，并用固定最小 source hit rate 阻止检索质量静默退化。

## 为什么普通单元测试不够

DenseRetriever、BM25 和 RRF 各自通过单测，只能证明局部公式正确，不能证明组合后的相关文档进入 Top-K。
真实退化可能来自：

- PDF 文本解析变化；
- Chunk 切分或 metadata 丢失；
- Embedding/向量库查询过滤错误；
- BM25 分词或持久化变化；
- RRF 候选数量、顺序或过滤配置变化。

H5 测试从生成 PDF 开始，经过正式 Ingestion CLI 和 QueryEngine，最终由 EvalRunner 对 Golden Set 评分，
因此覆盖的是用户实际依赖的检索闭环。

## 为什么测试使用确定性 Embedding

在线 Embedding 会引入网络、费用、模型升级和速率限制，导致 CI 结果不稳定。测试注册一个本地确定性
Provider，使同一文本始终产生同一向量；BM25 仍使用真实实现，Dense/Sparse/Fusion 的编排也没有替换。

这不是为了证明该简单向量模型质量高，而是固定外部变量，让代码和配置变化成为回归结果的主要原因。
生产模型质量应另有离线基准。

## Chunk ID 与 Source 身份的取舍

Chunk ID 适合精确判断“是否召回指定证据块”，但它通常受以下因素影响：

- source path；
- generation；
- Chunk 边界；
- 内容哈希或索引版本。

调整 Chunk Size 后，即使仍召回正确文档，旧 Chunk ID 也可能全部变化。若所有 Golden Case 都绑定 Chunk
ID，测试会把合理的重新切分误报为语义 Recall 归零。

因此支持两层相关性身份：

```text
expected_chunk_ids -> 精确证据级 Hit Rate / MRR
expected_sources   -> 稳定文档级 Source Hit Rate / Source MRR
```

Source 匹配只比较规范化文件名，不依赖临时测试目录。当前 H5 使用 source 级门禁；未来稳定测试索引能够
固定 Chunk 身份后，可同时提高 chunk 级约束。

## Hit Rate 与 MRR 的区别

- Source Hit Rate@K：正确来源是否出现在 Top-K，适合硬性召回门禁；
- Source MRR：第一个正确来源的倒数排名，能发现“虽然命中但排名不断下滑”。

命中第 1 名时 MRR=1，命中第 2 名时 MRR=0.5，完全未命中时为 0。Hit Rate 判断有没有，MRR 判断有多靠前。

## 为什么阈值写在测试里

`MIN_SOURCE_HIT_RATE` 是显式质量契约。CI 低于阈值直接失败，使检索配置调整必须解释质量变化，而不是
只保证代码不抛异常。

阈值不能随当前结果自动更新，否则门禁会失去意义。提高阈值需要更强的 Golden Set 证据；降低阈值应
作为有理由、可审查的产品决策。

## 当前边界

- 当前 Golden Set 只有少量工程语义 Case，只是最小回归门禁，不代表生产分布；
- Source 命中不能证明具体 Chunk 内容足以回答问题；
- 确定性测试 Embedding 不代表生产 Embedding 的语义能力；
- 测试覆盖本地 Chroma/BM25 路径，不覆盖远程向量库；
- 后续应增加困难负样本、同名文档、metadata filter 和多语言 Case，并同时监控 MRR。

## 面试表达建议

1. “单组件正确不等于端到端 Recall 正确，所以门禁从真实摄取一直跑到混合检索。”
2. “CI 使用确定性 Embedding 隔离网络和模型漂移，但 Dense/Sparse/RRF 编排仍走真实代码。”
3. “Chunk ID 精确但会随重新切分变化，Source 身份更稳定，我把两层指标分开。”
4. “Hit Rate 看是否命中，MRR 看正确结果是否持续掉到后排。”
5. “阈值是版本库中的显式质量契约，不能根据当次结果自动调低。”
