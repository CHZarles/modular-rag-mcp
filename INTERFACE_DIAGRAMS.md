# Modular RAG MCP Server Review 图集

> 目标：让你能从 Spec 快速理解开发意图、模块职责、协作关系、输入输出，以及以后学习、debug、准备面试时该去看哪里。  
> 这套图不替代 [ARCHITECTURE_DIAGRAMS.md](ARCHITECTURE_DIAGRAMS.md)。架构图回答“系统怎么跑”，本图集回答“代码应该怎么分、每块负责什么、边界在哪里”。

---

## 1. 怎么读这套图

先别从“类图细节”开始。第一遍只看 02/03 的粗线，把系统当成两条生产线：

```text
02 先建立数据生命线：文件怎么变成可检索资产，问题怎么变成回答
03 再看摄取生产线：PDF 进来后，每个阶段做什么、产出什么
04 再看查询生产线：用户问题进来后，怎么召回、合并、返回
05 最后看定位问题：效果差或出错时看哪里
```

推荐阅读顺序：

| 顺序 | 源文件 | 可视图 | 解决的问题 | 适合你问自己 |
|---|---|---|---|---|
| 1 | [review-02-core-data-contracts.puml](diagrams/review-02-core-data-contracts.puml) | [SVG](diagrams/review_02_core_data_contracts.svg) | 最少对象的数据生命线 | “我现在看的模块，在处理哪种数据？” |
| 2 | [review-03-ingestion-ports.puml](diagrams/review-03-ingestion-ports.puml) | [SVG](diagrams/review_03_ingestion_ports.svg) | 文档摄取主流程 | “PDF 进来后，每一步产物是什么？” |
| 3 | [review-04-query-response-ports.puml](diagrams/review-04-query-response-ports.puml) | [SVG](diagrams/review_04_query_response_ports.svg) | 查询链路每块干什么 | “用户问题进来后，怎么变成候选、引用和 MCP 响应？” |
| 4 | [review-05-observability-evaluation.puml](diagrams/review-05-observability-evaluation.puml) | [SVG](diagrams/review_05_observability_evaluation.svg) | 怎么 debug、评估和复盘 | “出错或效果不好，我看 trace 还是看 evaluator？” |

02/03/04 现在不是“把所有接口画出来”的图，而是第一遍 review 用的低密度导航图。图内尽量使用接近代码的字段名、类型名和调用名；解释性文字放在正文里。详细方法签名放在 [DEV_SPEC_INTERFACES.md](DEV_SPEC_INTERFACES.md)，不要在入口图里硬塞。

信息不靠一张图塞完，而是分两层：

| 层级 | 你什么时候看 | 文件 |
|---|---|---|
| 主路径图 | 第一次理解、讲架构、定位模块位置 | `review-02/03/04/05-*` |
| 参考图 | 已经知道主线，要查字段、接口、默认实现、替换点 | `reference-02/03/04-*-full` |

PlantUML 源文件比 Draw.io 更适合接口 review：能 diff、能搜方法名、能随着 spec 一起改。Zed 里如果 PlantUML 插件渲染不稳定，直接打开上表的 SVG 文件即可。

本地渲染命令（需要先安装 PlantUML）。这些图已启用 PlantUML 内置 `smetana` 布局，正常情况下不需要额外安装 Graphviz：

```bash
plantuml diagrams/review-*.puml
```

---

## 2. 一句话理解系统

这个系统可以拆成两条主链路：

```text
Ingestion: 原始 PDF -> Document -> Chunk -> ChunkRecord -> Vector/BM25/Image stores
Query: 用户问题 -> QueryRequest -> SearchHit -> RetrievalCandidate -> QueryResponse -> MCP ToolResult
```

你 review 时不用先懂向量检索细节。先看：

- 这个模块在哪条链路上？
- 它接收什么数据？
- 它产出什么数据？
- 它依赖的是接口还是具体实现？
- 它失败时会不会影响主流程？

---

## 3. Draw.io 和 PlantUML 各自负责什么

[ARCHITECTURE_DIAGRAMS.md](ARCHITECTURE_DIAGRAMS.md) 里的 Draw.io 图负责“系统怎么跑”，本图集里的 PlantUML 负责“代码怎么分、接口怎么连、数据怎么流”。

| 图类型 | 适合表达 | 不适合表达 |
|---|---|---|
| Draw.io | 系统全景、运行流程、给人讲架构、面试时口头解释 | 频繁变动的方法签名、类关系、接口继承 |
| PlantUML | 类图、接口、数据契约、依赖边界、可 diff 的 review 图 | 大幅视觉排版、产品化展示、复杂空间布局 |

所以这里不再重复画“分层入口图”。看全景先看 Draw.io；看开发边界直接从数据契约图开始。

---

## 4. 核心数据契约

对应图：[diagrams/review-02-core-data-contracts.puml](diagrams/review-02-core-data-contracts.puml)，可视图：[SVG](diagrams/review_02_core_data_contracts.svg)

这张图只保留最关键的 9 个数据节点，用接近 `dataclass` 的形式画。它的用途不是查全量字段，而是回答：

- Ingestion 产生了什么资产？
- Query 从哪些资产里取回什么？
- 哪个对象属于存储层，哪个对象属于 Query 层？

第一遍只看两条粗线：

```text
绿色：IngestionRequest -> Document -> Chunk -> ChunkRecord -> Stores
橙色：QueryRequest -> Stores -> SearchHit -> RetrievalCandidate -> QueryResponse
```

02 里的 `StoreInterfaces` 不是网络端口，而是存储能力接口的集合。它代表这几类能力：

```text
BaseVectorStore   向量存储/查询能力
BM25IndexStore    关键词索引/查询能力
ImageStore        图片引用保存/读取能力
```

正确性边界：

- `Document -> Chunk -> ChunkRecord` 对应 spec 中 C1/C4/C10-C12 的摄取资产生成路径。
- `Stores -> SearchHit -> RetrievalCandidate` 对应 `DEV_SPEC_INTERFACES.md` 的约束：Store 返回中性 `SearchHit`，Retriever 才映射为 Query 层的 `RetrievalCandidate`。
- `ImageRef -> ImagePayload` 没画成主线，因为图片不是每次 query 必返；它是 `include_images=true` 时从候选 chunk 旁路组装进响应。

查全量字段和对象关系时再看：[reference-02-core-data-contracts-full.puml](diagrams/reference-02-core-data-contracts-full.puml)，可视图：[SVG](diagrams/reference_02_core_data_contracts_full.svg)。

### Ingestion 方向

| 数据对象 | 谁产生 | 给谁用 | 含义 |
|---|---|---|---|
| `IngestionRequest` | CLI / Dashboard | `IngestionService` | 用户要摄取哪个文件、放进哪个 collection |
| `Document` | `BaseLoader` | `DocumentChunker` | 统一后的文档文本和基础 metadata |
| `ImageRef` | Loader / ImageStore | Chunker / Captioner / ImageStore | 图片的本地引用和溯源信息 |
| `Chunk` | `DocumentChunker` / Transform | Embedding / Storage | 可检索的文本块 |
| `ChunkRecord` | Encoding / Upserter | VectorStore / BM25 | 带向量和 sparse 信息的存储记录 |
| `IngestionResult` | `IngestionPipeline` | CLI / Dashboard | 成功、跳过、失败、chunk 数、图片数 |

`IngestionRequest` 是摄取接口的请求对象。它里面的三个核心字段可以理解成“接口参数”，但工程上会把它们包装成一个对象传入：

```python
IngestionService.ingest(request: IngestionRequest) -> IngestionResult
```

| 字段 | 白话含义 | 例子 |
|---|---|---|
| `source_path` | 这次要导入哪个文件 | `/Users/charles/docs/paper.pdf` |
| `collection` | 导入到哪个知识库/分组 | `interview-notes`、`rag-spec`、`default` |
| `force` | 文件以前导入过时，要不要无视缓存重新处理 | `false` 表示相同文件可跳过；`true` 表示强制重新切块、向量化、入库 |

### Query 方向

| 数据对象 | 谁产生 | 给谁用 | 含义 |
|---|---|---|---|
| `QueryRequest` | MCP Tool / CLI | `KnowledgeService` | 用户问题、top_k、collection、filters |
| `ProcessedQuery` | `QueryProcessor` | Dense/Sparse Retriever | 改写后的 query、关键词、过滤条件 |
| `SearchHit` | VectorStore / BM25IndexStore | Retriever | 底层索引命中结果，不知道 dense/sparse 业务语义 |
| `RetrievalCandidate` | Dense/Sparse Retriever | Fusion / Reranker / ResponseBuilder | Query 层候选，带 source、rank、debug |
| `Citation` | `CitationGenerator` | `QueryResponse` | 可展示、可追溯的引用 |
| `ImagePayload` | `MultimodalAssembler` | MCP Tool | 最终响应里的图片，支持 base64/uri/text reference |
| `QueryResponse` | `ResponseBuilder` | MCP Tool / CLI / Evaluation | answer、citations、items、images |

关键边界：

- `SearchHit` 属于存储/索引层。
- `RetrievalCandidate` 属于 Query 层。
- `ImageRef` 是本地/存储引用。
- `ImagePayload` 是响应 payload，不应该强依赖本地路径。

---

## 5. Ingestion 模块怎么协作

对应图：[diagrams/review-03-ingestion-ports.puml](diagrams/review-03-ingestion-ports.puml)，可视图：[SVG](diagrams/review_03_ingestion_ports.svg)

摄取链路的开发意图：

```text
把原始文档变成可检索、可追踪、可删除、可复用的索引资产。
```

03 和 02 的关系：

```text
02 看到的是数据怎么变：
IngestionRequest -> Document -> Chunk -> ChunkRecord -> Stores -> IngestionResult

03 看到的是谁负责这些变化：
IngestionService -> IngestionPipeline -> Loader/Chunker/Transform/Encoder/Store
```

所以 03 是“02 绿色 Ingestion 路径的协作展开”，和 04 对称：03 展开 ingestion，04 展开 query。

`review-03` 现在是 **UML Sequence Diagram**，看一次摄取运行时谁调用谁；`reference-03` 是 **Component Diagram**，看静态依赖结构、能力接口、默认实现、底层 store/adapter。

图里的 `alt` 是 UML Sequence Diagram 的标准写法，意思是 `if / else` 条件分支：

```python
if should_skip and force == false:
    return IngestionResult(status="skipped")
else:
    run ingestion pipeline
```

查接口继承、默认 adapter、替换点时再看：[reference-03-ingestion-ports-full.puml](diagrams/reference-03-ingestion-ports-full.puml)，可视图：[SVG](diagrams/reference_03_ingestion_ports_full.svg)。

主线：

```text
I1  IngestionService.ingest(IngestionRequest)
I1a IngestionPipeline.run(IngestionRequest)
I2  FileIntegrityStore hash / skip
I3  BaseLoader: source_path -> Document
I4  DocumentChunker: Document -> Chunk[]
I5  BaseTransform: Chunk[] -> Chunk[]
I6  BaseEmbedding: Chunk.text[] -> dense vectors
I7  SparseEncoder: Chunk[] -> sparse vectors
I8  build ChunkRecord[]
I9  Vector/BM25/Image stores persist assets
```

对应 02 的简版数据类型：

| 03 步骤 | 标出的数据类型 | 对应 02 的位置 |
|---|---|---|
| I1/I1a | `IngestionRequest` | 摄取入口对象 |
| I3 | `source_path -> Document` | 原始文件被 loader 统一成文档对象 |
| I4 | `Document -> Chunk[]` | 文档被切成可检索块 |
| I5 | `Chunk[] -> Chunk[]` | transform 增强/清洗 chunk |
| I6/I7 | `Chunk[] -> dense/sparse vectors` | 为索引准备向量/稀疏表示 |
| I8 | `Chunk[] + vectors -> ChunkRecord[]` | 写库载体 |
| I9 | `ChunkRecord[] / Chunk[] / ImageRef[] -> Stores` | 持久化成可检索资产 |
| return | `IngestionResult` | 摄取结果 |

模块职责：

| 模块 | 输入 | 输出 | 它负责 | 它不负责 |
|---|---|---|---|---|
| `FileIntegrityStore` | 文件路径 | file hash / skip decision | 判断是否重复处理 | 解析 PDF |
| `BaseLoader` | `source_path` | `Document` | PDF -> canonical markdown、基础 metadata、图片引用 | 切 chunk、生成向量 |
| `BaseSplitter` | text | `list[str]` | 纯文本切分 | 业务 metadata |
| `DocumentChunker` | `Document` | `list[Chunk]` | chunk id、metadata 继承、image_refs 分发 | 调模型、入库 |
| `BaseTransform` | `list[Chunk]` | enriched chunks | 去噪、摘要、tags、图片 caption | 生成 embedding |
| `BaseEmbedding` | chunk texts | dense vectors | 文本向量化 | 存储 |
| `SparseEncoder` | chunks | sparse vectors | BM25 所需稀疏信息 | dense embedding |
| `BaseVectorStore` | `ChunkRecord[]` | upsert result / `SearchHit[]` | 向量存储和查询 | 判断 dense/sparse 业务语义 |
| `BM25IndexStore` | chunks + sparse vectors | BM25 index / `SearchHit[]` | 关键词索引 | 调 embedding |
| `ImageStore` | `ImageRef[]` | image lookup/delete | 图片索引和删除 | 生成 caption |

Review 重点：

- `IngestionPipeline` 只编排，不应该直接 import OpenAI/Chroma/MarkItDown/BM25 这些具体实现细节。它应该依赖 `BaseLoader`、`BaseEmbedding`、`BaseVectorStore` 这类能力接口。
- 每一步的产物都应该能被测试 fake 替代。
- 每个 chunk id 要稳定，否则重复摄取会产生重复索引。
- 图片引用必须在 `Document -> Chunk` 时正确分发，否则后面 caption 和图片返回都会断。

---

## 6. Query 模块怎么协作

对应图：[diagrams/review-04-query-response-ports.puml](diagrams/review-04-query-response-ports.puml)，可视图：[SVG](diagrams/review_04_query_response_ports.svg)

查询链路的开发意图：

```text
把用户问题变成可解释的候选证据，再组装成带引用的 MCP 响应。
```

04 和 02 的关系：

```text
02 看到的是数据怎么变：
QueryRequest -> Stores -> SearchHit -> RetrievalCandidate -> QueryResponse

04 看到的是谁负责这些变化：
KnowledgeService -> QueryEngine -> Retriever/Fusion/Reranker -> ResponseBuilder -> MCP Adapter
```

所以 02 是“数据对象地图”，04 是“Query 这条数据路径的协作展开”。你在 04 里看到的 `Q3 DenseRetriever` / `Q4 SparseRetriever`，对应 02 里的 `Stores -> SearchHit -> RetrievalCandidate` 这一段；`Q8 ResponseBuilder` 对应 02 里的 `RetrievalCandidate -> QueryResponse`。

为了让两张图能对上，04 里已经标出 02 的简版数据类型：

| 04 步骤 | 标出的数据类型 | 对应 02 的位置 |
|---|---|---|
| Q1/Q1a | `QueryRequest` | Query 入口对象 |
| Q2 | `QueryRequest -> ProcessedQuery` | query 标准化后的中间对象 |
| Q3 | `ProcessedQuery -> RetrievalCandidate[]` | dense route 内部先得到 `SearchHit[]`，再映射成候选 |
| Q4 | `ProcessedQuery -> RetrievalCandidate[]` | sparse route 内部先得到 `SearchHit[]`，再映射成候选 |
| Q5-Q7 | `RetrievalCandidate[] -> RetrievalCandidate[]` | fusion/filter/rerank 都只处理候选证据 |
| Q8 | `QueryRequest + RetrievalCandidate[] -> QueryResponse` | 响应组装 |
| Q9 | `QueryResponse -> MCP content/structuredContent` | 协议适配 |

`review-04` 现在是 **UML Sequence Diagram**。它不画接口继承和默认实现类，只回答“一次 query 运行时，谁调用谁”。第一遍按时间顺序从上往下读：

```python
Q1 MCP Tool -> KnowledgeService.query(request)
Q1a KnowledgeService -> QueryEngine.search(request)
Q2 QueryEngine -> QueryProcessor.process(request)
Q3 QueryEngine -> DenseRetriever.retrieve(...)
Q4 QueryEngine -> SparseRetriever.retrieve(...)
Q5 QueryEngine -> FusionStrategy.fuse(...)
Q6 QueryEngine -> MetadataFilter.apply(...)
Q7 QueryEngine -> BaseReranker.rerank(...)
Q8 KnowledgeService -> ResponseBuilder.build(...)
Q9 MCP Tool Adapter converts QueryResponse
```

`dense_retriever` 和 `sparse_retriever` 在图里用 UML 的 `par` 并行块表示：它们都是召回候选证据，最后都回到 `QueryEngine`，再进入 fusion。

两个 04 图的关系：

- `review-04` 是 **Sequence Diagram**：看运行时调用顺序。
- `reference-04` 是 **Component Diagram**：看静态依赖结构、能力接口、默认实现、底层 store/adapter。
- 两张图共享同一套 `Q1~Q9` 编号。

这里的 `port` 不是网络端口。它来自 Ports and Adapters / Hexagonal Architecture，意思是“能力接口”或“依赖边界”。在这套图里可以直接读成 `interface`：

```text
DenseRetriever port = QueryEngine 需要的一种检索能力接口
BaseVectorStore port = Retriever 需要的一种向量存储能力接口
```

这样做的意义是：上层依赖接口，不依赖具体实现。`QueryEngine` 只知道 `DenseRetriever.retrieve()`，不需要知道下面到底是 Chroma、Qdrant、OpenAI 还是别的 provider。

`reference-04` 里每个接口盒子里的 `default: ...` 指：这个能力接口背后默认使用的具体代码类。它不是新的一层架构，也不是只能用这一种实现；它表示“如果配置没有替换，系统先用这个实现”。

例子：

| Port / Interface | 默认实现类 | 意思 |
|---|---|---|
| `DenseRetriever` | `VectorDenseRetriever` | 默认用向量检索路线 |
| `SparseRetriever` | `BM25SparseRetriever` | 默认用 BM25 关键词检索路线 |
| `FusionStrategy` | `RRFFusion` | 默认用 RRF 合并 dense/sparse 排名 |
| `BaseReranker` | `NoneReranker` / `CrossEncoderReranker` / `LLMReranker` | rerank 可以关掉，也可以换实现 |
| `ResponseBuilder` | `DefaultResponseBuilder` | 默认组装 `QueryResponse` |

`Q1a QueryEngine.search()` 是 `Q1 KnowledgeService.query()` 的内部细节。它的意思是：`QueryEngine` 自己不做 embedding、BM25、rerank 的具体算法，而是按顺序编排 Q2 到 Q7 这些 port。

对照关系：

| 编号 | review-04 看什么 | reference-04 多解释什么 |
|---|---|---|
| Q1 | `KnowledgeService.query()` | 本地/HTTP 两种实现；入口层只能停在这里 |
| Q2 | `query_processor.process()` | 默认 `RuleBasedQueryProcessor`，产出 `ProcessedQuery` |
| Q3 | `dense_retriever.retrieve()` | 内部依赖 `BaseEmbedding` + `BaseVectorStore` |
| Q4 | `sparse_retriever.retrieve()` | 内部依赖 `BM25IndexStore`，必要时用 `get_by_ids` 补正文 |
| Q5 | `fusion.fuse()` | 默认 `RRFFusion` |
| Q6 | `metadata_filter.apply()` | 默认 `DefaultMetadataFilter` |
| Q7 | `reranker.rerank_or_keep()` | `NoneReranker/CrossEncoderReranker/LLMReranker`，失败要回退 |
| Q8 | `response_builder.build()` | 内部用 `CitationGenerator`、`MultimodalAssembler`、`ImageStore` |
| Q9 | MCP adapter 转换响应 | 只做协议转换，不直接调 retriever/store |

查 retriever/store/response builder 的接口、默认实现、替换点时再看：[reference-04-query-response-ports-full.puml](diagrams/reference-04-query-response-ports-full.puml)，可视图：[SVG](diagrams/reference_04_query_response_ports_full.svg)。

模块职责：

| 模块 | 输入 | 输出 | 它负责 | 它不负责 |
|---|---|---|---|---|
| `KnowledgeService` | `QueryRequest` | `QueryResponse` | 给 MCP/CLI/Dashboard 一个稳定门面 | 暴露 Chroma/BM25 细节 |
| `QueryProcessor` | `QueryRequest` | `ProcessedQuery` | 关键词、filters、query 标准化 | 查库 |
| `DenseRetriever` | standalone query | `RetrievalCandidate[]` | 调 embedding + vector store，把 `SearchHit` 映射成 dense candidate | fusion/rerank |
| `SparseRetriever` | keywords | `RetrievalCandidate[]` | 调 BM25，把 `SearchHit` 映射成 sparse candidate | dense embedding |
| `FusionStrategy` | 多路 candidates | fused candidates | 合并 dense/sparse 排名 | 查库 |
| `MetadataFilter` | candidates + filters | filtered candidates | 做硬过滤/兜底过滤 | 生成答案 |
| `BaseReranker` | query + candidates | reranked candidates | 精排或回退 | 召回 |
| `ResponseBuilder` | request + candidates | `QueryResponse` | answer、citations、images | MCP JSON-RPC |
| MCP Tool Adapter | `QueryResponse` | MCP `content/structuredContent` | 协议格式转换 | 检索 |

Review 重点：

- MCP Tool 只能调 `KnowledgeService`。
- Store 只能返回 `SearchHit`，不能返回 `RetrievalCandidate`。
- Dense/Sparse Retriever 是 `SearchHit -> RetrievalCandidate` 的边界。
- Reranker 必须可关闭、失败可回退。
- `QueryResponse` 是本地模式和 HTTP 模式共用的契约。

---

## 7. Observability / Evaluation 怎么定位问题

对应图：[diagrams/review-05-observability-evaluation.puml](diagrams/review-05-observability-evaluation.puml)，可视图：[SVG](diagrams/review_05_observability_evaluation.svg)

当你 debug 或复盘时，按症状定位：

| 症状 | 先看哪里 | 可能模块 |
|---|---|---|
| 文件重复摄取、跳过异常 | ingestion trace + integrity store | `FileIntegrityStore` |
| chunk 太碎或找不到上下文 | ingestion trace 的 split/transform 阶段 | `BaseSplitter` / `DocumentChunker` / `BaseTransform` |
| 图片搜不到 | chunk metadata、image_refs、caption trace | `DocumentChunker` / `ImageCaptioner` / `ImageStore` |
| 关键词明明出现却搜不到 | sparse retrieval trace | `BM25IndexStore` / `SparseRetriever` |
| 语义相关但搜不到 | dense retrieval trace | `BaseEmbedding` / `BaseVectorStore` / `DenseRetriever` |
| dense/sparse 都有但排序差 | fusion/rerank trace | `FusionStrategy` / `BaseReranker` |
| 回答没引用或引用错 | response build trace | `CitationGenerator` / `ResponseBuilder` |
| 评估分数下降 | evaluation report + bad cases | `BaseEvaluator` / golden set / retrieval strategy |

观测和评估的边界：

- Trace 解释“一次运行发生了什么”。
- Evaluation 解释“一批 case 的质量好不好”。
- Cache 只影响性能，不应该改变输出数据结构。
- Trace/cache/evaluation 失败不能中断 MCP/RAG 主流程。

---

## 8. 面试准备时怎么用

你不需要一开始就背所有细节，可以按模块准备：

| 面试问题类型 | 你应该看 |
|---|---|
| “你这个系统整体怎么设计？” | Architecture 图 + 数据契约图 |
| “为什么说模块化？” | Ports / Adapters、`KnowledgeService`、`IngestionService` |
| “文档怎么入库？” | Ingestion Ports 图 |
| “查询怎么召回？” | Query/Response Ports 图 |
| “Hybrid Search 怎么接进去？” | `DenseRetriever`、`SparseRetriever`、`FusionStrategy` |
| “怎么 debug 坏 case？” | Observability/Evaluation 图 |
| “怎么替换 OpenAI/Chroma/Reranker？” | Factory/Registry + Port 接口 |

这套图的价值不是让你一次懂所有算法，而是让你知道：**每个算法在系统里的位置、输入输出、失败时该去哪里查。**
