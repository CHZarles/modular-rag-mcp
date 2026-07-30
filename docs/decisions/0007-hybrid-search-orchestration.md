# ADR-0007：HybridSearch 如何编排混合检索

- **状态**：当前采用
- **日期**：2026-07-30
- **决策范围**：D1 至 D4 如何组成一次可执行的查询链路
- **说明**：本文记录当前实现知识、示例和顾虑，不修改 `DEV_SPEC.md`

## 先用一句话理解

`HybridQueryEngine` 是查询链路的编排器，不是新的检索算法。

它负责把查询预处理、Dense 召回、Sparse 召回、RRF 融合、元数据过滤和重排按正确顺序
连接起来，并决定其中一个环节失败时应该继续还是报错。

```text
用户查询
   │
   ▼
D1 QueryProcessor：整理查询、提取关键词和过滤条件
   │
   ├──────────────────────┐
   ▼                      ▼
D2 DenseRetriever      D3 SparseRetriever
完整语义查询              关键词查询
   │                      │
   └──────────┬───────────┘
              ▼
        D4 RRFFusion：合并两张榜单
              │
              ▼
        MetadataFilter：再次检查元数据范围
              │
              ▼
        Reranker：当前默认不改变顺序
              │
              ▼
        返回 request.top_k 个候选项
```

## 完整例子

用户发起请求：

```python
QueryRequest(
    query="  租约过期后，旧 worker 为什么不能发布？  ",
    collection="docs",
    filters={"kind": "guide"},
    top_k=2,
)
```

### 第一步：预处理

`QueryProcessor` 生成两种输入：

```text
standalone_query：租约过期后，旧 worker 为什么不能发布？
keywords：        [租约过期后, 旧, worker, 为什么不能发布]
filters：         {collection: docs, kind: guide}
```

完整句子交给 Dense，关键词交给 Sparse。两路使用不同形式的输入，但查询范围相同。

### 第二步：并行召回

```text
Dense 榜单                 Sparse 榜单
1. Chunk A                1. Chunk B
2. Chunk B                2. Chunk C
```

两路互不依赖，因此用线程池同时执行。假设：

```text
Dense 用时：800 ms
Sparse 用时：100 ms
```

顺序执行大约需要 `800 + 100 = 900 ms`；并行执行大约取决于较慢的一路，即 `800 ms`，
再加少量线程调度和融合开销。

并行只改变等待方式，不改变 Dense、BM25 或 RRF 的算法。

### 第三步：按固定通道顺序交给 RRF

线程完成顺序是不稳定的，Sparse 可能先完成，Dense 也可能先完成。当前实现不会按“谁先
完成”决定榜单顺序，而是始终按照：

```text
1. Dense
2. Sparse
```

把成功结果交给 RRF。这样同样的输入不会因为网络和线程时序变化而产生不同的同分排序。

RRF 的具体公式和分数示例见 `docs/decisions/0006-rrf-rank-fusion.md`。

### 第四步：过滤、重排和截断

RRF 会多保留一批候选项，再执行元数据过滤，为后续重排留出余量。最后只返回请求中的
`top_k=2` 个结果。

当前默认 `NoneReranker` 只是保持 RRF 顺序，不会重新打分。真正的重排实现属于 D6，
不是本次 D5 的内容。

## 为什么要区分“没有命中”和“系统故障”

以下两种返回看起来都可能是空列表，但含义完全不同：

```text
情况 A：Dense 和 Sparse 都正常完成，只是没有找到相关 Chunk
结论：这是正常的“没有命中”，返回 []

情况 B：Embedding 服务和 BM25 存储都发生异常
结论：这是系统故障，不能返回 []，必须抛出错误
```

如果情况 B 也返回空列表，上层会错误地告诉用户“知识库没有答案”，掩盖真正的服务故障。

当前降级规则如下：

| Dense | Sparse | 当前行为 |
|-------|--------|----------|
| 成功 | 成功 | 融合两路结果 |
| 失败 | 成功 | 使用 Sparse 结果继续查询 |
| 成功 | 失败 | 使用 Dense 结果继续查询 |
| 失败 | 失败 | 抛出 `RuntimeError` |
| 成功但为空 | 成功但为空 | 正常返回空结果 |

“成功但为空”仍然算通道成功，因为它证明检索器正常工作，只是当前查询没有命中。

## 配置规则

`HybridSearchConfig` 当前控制：

```text
dense_top_k：  Dense 最多召回多少候选项
sparse_top_k： Sparse 最多召回多少候选项
fusion_top_k： RRF 后至少保留多少候选项
enable_dense： 是否启用 Dense
enable_sparse：是否启用 Sparse
```

启动时会拒绝以下配置：

- 任意候选数量小于等于零；
- Dense 和 Sparse 全部关闭；
- 启用了 Dense，却没有注入 DenseRetriever；
- 启用了 Sparse，却没有注入 SparseRetriever。

因此系统可以明确运行三种模式：

```text
混合模式：Dense 开，Sparse 开
纯 Dense： Dense 开，Sparse 关
纯 Sparse：Dense 关，Sparse 开
```

## 为什么 collection 优先于通用 filters

`collection` 是 `QueryRequest` 的专用检索范围字段。假设请求中出现冲突：

```python
QueryRequest(
    query="lease",
    collection="docs",
    filters={"collection": "other"},
)
```

当前实现使用 `docs`。否则一个通用过滤字典就能悄悄改变查询所属知识库，既难理解，也
容易造成跨集合查询。其他过滤字段仍然正常合并。

## 当前实现边界与顾虑

### 1. 单路失败可以降级，单路卡住还不能超时

当前只在检索器抛出异常后降级。如果 Dense 请求长时间不返回，即使 Sparse 已经完成，
整个查询仍会等待 Dense。后续需要结合真实延迟数据设计通道超时、取消和超时后的降级，
不能简单把超时时间写死。

### 2. 每次查询都会创建一个小型线程池

当前最多只有 Dense、Sparse 两个任务，实现简单且足以支撑 MVP。高并发下如果线程创建
开销或总线程数成为瓶颈，再考虑共享执行器或异步 I/O；在有性能证据前不增加复杂度。

### 3. 并行阶段共享同一个 trace

Dense 和 Sparse 都会收到相同的 `trace` 对象，因此两个阶段记录的先后顺序可能反映实际
完成时序，而不是固定的 Dense、Sparse 顺序。当前结果排序不依赖 trace；线程安全和更
严格的并行追踪语义留到可观测性阶段处理。

### 4. 纯单路模式仍经过 RRF

纯 Dense 或纯 Sparse 模式也会通过统一的 Fusion 端口，因此最终 `source` 会变成
`fusion`，最终 `score` 会变成单路 RRF 分数。原始通道和分数仍保存在调试信息中。
如果未来业务要求纯单路模式完整保留原始分数语义，需要单独评估是否绕过 Fusion。

### 5. 当前全部失败使用通用 RuntimeError

这能正确区分系统故障与空结果，但还不是细粒度的领域错误。等上层服务需要映射稳定的
MCP 或 HTTP 错误码时，再引入专用异常类型，避免提前建立没有调用方的异常体系。

## 验证证据

- `tests/integration/test_hybrid_search.py` 使用 `Barrier` 验证 Dense 与 Sparse 确实并行；
- 覆盖双路融合、单路失败降级、全部失败报错和纯 Dense 模式；
- 覆盖候选数量、通道开关、依赖完整性和 collection 优先级；
- 当前完整回归结果：`217 passed, 5 skipped`；
- D5 相关代码已通过 Ruff、全量源码 Mypy、compileall 和 `git diff --check`。

## 关联实现

- `src/core/query_engine/hybrid_search.py`
- `src/core/query_engine/query_processor.py`
- `src/core/query_engine/dense_retriever.py`
- `src/core/query_engine/sparse_retriever.py`
- `src/core/query_engine/fusion.py`
- `src/core/query_engine/filter.py`
- `src/core/query_engine/reranker.py`
- `tests/integration/test_hybrid_search.py`
