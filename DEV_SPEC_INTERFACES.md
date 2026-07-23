# DEV_SPEC_INTERFACES: 模块接口与解耦规范

> 版本：0.2  
> 目的：把 Modular RAG MCP Server 的各个工程环节拆成稳定接口，使每个环节可以单独理解、单独实现、单独测试、单独 review。  
> 关联文档：[DEV_SPEC.md](DEV_SPEC.md)、[DEV_SPEC_AGENT_OBSERVABILITY.md](DEV_SPEC_AGENT_OBSERVABILITY.md)、[INTERFACE_DIAGRAMS.md](INTERFACE_DIAGRAMS.md)

---

## 1. 总目标

本规范不重新描述业务愿景，而是约束“代码应该怎么分层、怎么依赖、怎么替换”：

- MCP / CLI / Dashboard 入口只依赖应用服务接口，不直接碰检索、索引、数据库。
- Ingestion、Query、Response、Evaluation、Observability 每条链路都有自己的输入输出契约。
- 每个 provider 或算法实现只实现接口，不反向依赖上层业务。
- 所有跨模块数据统一放在 `src/core/types.py` 或对应服务契约文件里，避免各模块复制自己的 schema。
- 每个接口都配 contract test 和 fake implementation，使 review 可以聚焦在一个环节内完成。

非目标：

- 不在本规范中选定所有第三方库的最终实现；具体实现以主 [DEV_SPEC.md](DEV_SPEC.md) 排期为准。
- 不把所有能力都抽成远程服务；默认仍是本地优先、单进程可运行。
- 不为了“可插拔”制造空转抽象；只有跨技术实现、跨入口复用或需要独立测试的环节才定义接口。

---

## 2. 分层规则

### 2.1 调用方向

唯一允许的依赖方向：

```text
Entry Adapters
  MCP tools / CLI / Dashboard
        |
        v
Application Services
  KnowledgeService / IngestionService / DocumentService / EvaluationService
        |
        v
Domain Orchestrators
  IngestionPipeline / QueryEngine / ResponseBuilder / EvalRunner
        |
        v
Ports
  Loader / Splitter / Embedding / VectorStore / Reranker / TraceSink / Evaluator
        |
        v
Adapters
  MarkItDown / LangChain / Chroma / BM25 / OpenAI / Azure / Ragas / SQLite
```

禁止方向：

- `mcp_server/tools/*` 禁止直接 import `ChromaStore`、`BM25Indexer`、`EmbeddingClient`。
- `Dashboard` 页面禁止直接 new pipeline 依赖树；必须走 service/factory。
- `libs/*` provider 禁止 import `mcp_server`、`dashboard`、`ingestion.pipeline`。
- `TraceSink` 失败禁止中断主链路。

### 2.2 三类接口

| 类型 | 用途 | 示例 | 变更策略 |
|---|---|---|---|
| Domain Contract | 业务稳定数据结构 | `Document`, `Chunk`, `QueryResponse` | 兼容演进，新增字段必须可选 |
| Port Interface | 上层依赖的能力接口 | `BaseVectorStore`, `BaseReranker` | 变更必须配 contract test |
| Adapter Interface | 第三方库封装 | `ChromaStore`, `AzureOpenAIClient` | 可替换，不能污染 Domain Contract |

### 2.3 可 review 单元

| Review 单元 | 只需要理解 | 不需要理解 | 关键验收 |
|---|---|---|---|
| MCP Tool | Tool schema、`KnowledgeService` 契约、MCP 返回格式 | Chroma、BM25、Embedding 细节 | 注入 `FakeKnowledgeService` 可测 |
| KnowledgeService | `QueryRequest/QueryResponse`、本地/HTTP 两种实现 | MCP JSON-RPC 细节 | 本地和 HTTP 实现输出同一 schema |
| QueryEngine | query processing、dense/sparse、fusion、filter、rerank | MCP content 组装 | 每个阶段可 fake，可看 trace |
| IngestionPipeline | `IngestionRequest`、load/split/transform/embed/upsert | MCP、Dashboard UI | 每阶段输入输出稳定，可重跑幂等 |
| Provider Adapter | 一个 port 的行为 | 调用它的业务场景 | contract test 通过即可 |
| DocumentManager | list/detail/delete/stats | 摄取算法细节 | 跨存储删除一致 |
| Observability | run/span/event/artifact | 检索算法细节 | 失败隔离、可查询、可导出 |
| Evaluation | golden set、metrics、report | MCP 协议细节 | 指标 shape 稳定 |

---

## 3. 核心数据契约

所有跨模块复用的数据结构优先放在 `src/core/types.py`。字段命名用 snake_case；对象必须可 JSON 序列化。

```python
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, Literal, Protocol

JsonDict = dict[str, Any]
Metadata = dict[str, Any]

@dataclass(frozen=True)
class ImageRef:
    image_id: str
    path: str
    collection: str
    source_path: str
    page: int | None = None
    mime_type: str = "image/png"
    text_offset: int | None = None
    text_length: int | None = None
    position: JsonDict = field(default_factory=dict)

@dataclass(frozen=True)
class Document:
    id: str
    text: str
    metadata: Metadata

@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    metadata: Metadata
    source_ref: str
    chunk_index: int
    start_offset: int | None = None
    end_offset: int | None = None

@dataclass(frozen=True)
class ChunkRecord:
    id: str
    text: str
    metadata: Metadata
    dense_vector: list[float] | None = None
    sparse_vector: JsonDict | None = None
    content_hash: str | None = None
```

### 3.1 Query 数据契约

```python
@dataclass(frozen=True)
class ProcessedQuery:
    original_query: str
    standalone_query: str
    keywords: list[str]
    filters: JsonDict = field(default_factory=dict)

@dataclass(frozen=True)
class SearchHit:
    id: str
    text: str
    metadata: Metadata
    score: float
    score_kind: Literal["similarity", "distance", "bm25", "unknown"] = "unknown"
    raw: JsonDict = field(default_factory=dict)

@dataclass(frozen=True)
class RetrievalCandidate:
    chunk_id: str
    text: str
    metadata: Metadata
    score: float
    source: Literal["dense", "sparse", "fusion", "rerank"]
    rank: int
    debug: JsonDict = field(default_factory=dict)

@dataclass(frozen=True)
class Citation:
    citation_id: str
    chunk_id: str
    source_path: str
    page: int | None
    text: str
    score: float
    metadata: Metadata = field(default_factory=dict)

@dataclass(frozen=True)
class ImagePayload:
    image_id: str
    mime_type: str
    data_base64: str | None = None
    uri: str | None = None
    source_ref: str | None = None
    metadata: JsonDict = field(default_factory=dict)

@dataclass(frozen=True)
class QueryRequest:
    query: str
    top_k: int = 5
    collection: str = "default"
    filters: JsonDict = field(default_factory=dict)
    include_images: bool = True
    request_id: str | None = None

@dataclass(frozen=True)
class QueryResponse:
    answer: str
    citations: list[Citation]
    items: list[RetrievalCandidate]
    images: list[ImagePayload] = field(default_factory=list)
    request_id: str | None = None
    metadata: JsonDict = field(default_factory=dict)
```

约束：

- `QueryResponse.items` 保留机器可复盘的候选结果；`answer/citations/images` 面向最终响应。
- `SearchHit` 是存储/索引层的中性命中结果；`RetrievalCandidate` 是 Query 层候选结果，只有 Retriever/Fusion/Reranker 可以创建或修改。
- `ImageRef` 是本地/存储引用；`ImagePayload` 是响应契约，必须支持本地模式和 HTTP 模式，不要求一定有本地文件路径。
- `score` 只保证同一阶段内可比较；跨阶段比较必须通过 `source/debug` 解释。
- HTTP 版 `KnowledgeService` 也必须返回同一 `QueryResponse` shape。

### 3.2 Ingestion 数据契约

```python
@dataclass(frozen=True)
class IngestionRequest:
    source_path: str
    collection: str = "default"
    force: bool = False
    request_id: str | None = None

@dataclass(frozen=True)
class IngestionResult:
    source_path: str
    collection: str
    status: Literal["success", "skipped", "failed"]
    file_hash: str
    document_id: str | None = None
    chunk_count: int = 0
    image_count: int = 0
    error: str | None = None
    metadata: JsonDict = field(default_factory=dict)

ProgressCallback = Callable[[str, int, int], None]
```

约束：

- `skipped` 是成功路径的一种，不应被当作异常。
- 所有阶段只接收上一阶段产物，不直接回头读取原始文件，除非接口明确允许。
- 每个 chunk 的 `id` 必须确定性生成，保证重复摄取幂等。

### 3.3 管理与评估契约

```python
@dataclass(frozen=True)
class CollectionInfo:
    name: str
    document_count: int
    chunk_count: int
    image_count: int = 0
    metadata: JsonDict = field(default_factory=dict)

@dataclass(frozen=True)
class DocumentSummary:
    doc_id: str
    source_path: str
    title: str | None = None
    summary: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

@dataclass(frozen=True)
class DeleteResult:
    source_path: str
    collection: str
    deleted_chunks: int
    deleted_images: int
    removed_bm25: bool
    removed_integrity_record: bool
    errors: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    query: str
    expected_chunk_ids: list[str] = field(default_factory=list)
    expected_answer: str | None = None
    metadata: JsonDict = field(default_factory=dict)

@dataclass(frozen=True)
class EvaluationReport:
    run_id: str
    metrics: dict[str, float]
    cases: list[JsonDict]
    metadata: JsonDict = field(default_factory=dict)
```

---

## 4. 应用服务接口

应用服务是入口层唯一允许调用的业务接口。

### 4.1 KnowledgeService

```python
class KnowledgeService(Protocol):
    def query(self, request: QueryRequest, trace: "TraceContext | None" = None) -> QueryResponse: ...
    def list_collections(self) -> list[CollectionInfo]: ...
    def get_document_summary(self, doc_id: str) -> DocumentSummary: ...
```

实现：

- `LocalKnowledgeService`：本进程调用 `QueryEngine`、`ResponseBuilder`、`ImageStorage`、`DocumentManager`。
- `HttpRetrievalServiceClient`：通过普通 HTTP 调共享检索服务，但必须消费/返回同一 `QueryRequest/QueryResponse`。
- `FakeKnowledgeService`：测试 MCP tools、Dashboard service，不依赖向量库。

Review 关注：

- Tool 是否只调用 `KnowledgeService`。
- `QueryResponse` 是否可 JSON 序列化。
- 本地和 HTTP 实现是否通过同一 contract tests。

### 4.2 IngestionService

```python
class IngestionService(Protocol):
    def ingest(
        self,
        request: IngestionRequest,
        on_progress: "Callable[[str, int, int], None] | None" = None,
        trace: "TraceContext | None" = None,
    ) -> IngestionResult: ...
```

实现：

- `LocalIngestionService`：包装 `IngestionPipeline`。
- `FakeIngestionService`：Dashboard ingestion 页面和 CLI 参数测试使用。

约束：

- Dashboard、CLI 不直接 new `IngestionPipeline` 全依赖树。
- `on_progress` 是可选边带通道，不影响结果契约。

### 4.3 DocumentService

```python
class DocumentService(Protocol):
    def list_documents(self, collection: str | None = None) -> list[DocumentSummary]: ...
    def get_document_detail(self, doc_id: str) -> JsonDict: ...
    def delete_document(self, source_path: str, collection: str) -> DeleteResult: ...
    def get_collection_stats(self, collection: str | None = None) -> CollectionInfo: ...
```

实现：

- `DocumentManager` 作为默认实现，协调 Chroma、BM25、ImageStorage、FileIntegrity。
- Dashboard 的 Data Browser / Ingestion Manager 只依赖这个接口。

### 4.4 EvaluationService

```python
class EvaluationService(Protocol):
    def run_evaluation(self, cases: list[EvaluationCase], evaluator_names: list[str] | None = None) -> EvaluationReport: ...
```

实现：

- `LocalEvaluationService`：加载 evaluator factory，调用 `EvalRunner`。
- `FakeEvaluationService`：Dashboard evaluation panel、CI 报告格式测试使用。

约束：

- Dashboard 评估面板只依赖 `EvaluationService`，不直接 import Ragas/DeepEval。
- 评估失败不能污染已持久化的 query/ingestion trace。

---

## 5. Ingestion 链路接口

### 5.1 FileIntegrity

```python
class FileIntegrityStore(Protocol):
    def compute_sha256(self, source_path: str) -> str: ...
    def should_skip(self, file_hash: str, collection: str) -> bool: ...
    def mark_processing(self, file_hash: str, source_path: str, collection: str) -> None: ...
    def mark_success(self, file_hash: str, source_path: str, collection: str, chunk_count: int) -> None: ...
    def mark_failed(self, file_hash: str, source_path: str, collection: str, error: str) -> None: ...
    def remove_record(self, file_hash: str, collection: str) -> None: ...
    def list_processed(self, collection: str | None = None) -> list[JsonDict]: ...
```

默认实现：`SQLiteIntegrityStore`。  
Review 关注：重复文件 early exit、WAL/事务、失败状态不会被误判为可跳过。

### 5.2 Loader

```python
class BaseLoader(Protocol):
    supported_extensions: tuple[str, ...]
    def load(self, source_path: str, collection: str, trace: "TraceContext | None" = None) -> Document: ...
```

约束：

- Loader 只做格式统一、基础 metadata、图片引用收集。
- Loader 不切分、不生成 embedding、不写向量库。
- 当前默认只实现 PDF -> canonical Markdown。

Contract test：

- sample PDF 能输出 `Document.text`。
- `metadata.source_path/doc_type/collection` 必填。
- 有图片时插入 `[IMAGE: image_id]`，并写入 `metadata.images`。

### 5.3 Splitter 与 DocumentChunker

第三方 splitter 保持纯文本接口；业务对象转换放在 `DocumentChunker`。

```python
class BaseSplitter(Protocol):
    def split_text(self, text: str, trace: "TraceContext | None" = None) -> list[str]: ...

class DocumentChunker(Protocol):
    def split_document(self, document: Document, trace: "TraceContext | None" = None) -> list[Chunk]: ...
```

约束：

- `BaseSplitter` 不认识 `Document/Chunk`，方便替换 LangChain / semantic splitter。
- `DocumentChunker` 负责 chunk id、metadata 继承、`image_refs` 按 chunk 分发。
- 同一 `Document` 重复切分必须产生同一组 chunk ids。

### 5.4 Transform

```python
class BaseTransform(Protocol):
    name: str
    def transform(self, chunks: list[Chunk], trace: "TraceContext | None" = None) -> list[Chunk]: ...
```

推荐实现：

- `ChunkRefiner`
- `MetadataEnricher`
- `ImageCaptioner`
- `TransformChain`

约束：

- Transform 必须幂等：同一输入重复执行不应持续膨胀文本。
- 单个 chunk 失败时优先标记 metadata 并降级，不阻塞整篇文档。
- 不能写向量库；只能返回增强后的 chunks。

### 5.5 Embedding 与 Sparse Encoding

```python
class BaseEmbedding(Protocol):
    def embed(self, texts: list[str], trace: "TraceContext | None" = None) -> list[list[float]]: ...

class SparseEncoder(Protocol):
    def encode(self, chunks: list[Chunk], trace: "TraceContext | None" = None) -> list[JsonDict]: ...
```

约束：

- `BaseEmbedding.embed([])` 行为必须明确：推荐返回 `[]`。
- 输出向量数量必须等于输入文本数量。
- provider 返回的 usage/cost 只能写 trace，不进入 domain object 必填字段。

### 5.6 存储写入

```python
class BaseVectorStore(Protocol):
    def upsert(self, records: list[ChunkRecord], trace: "TraceContext | None" = None) -> None: ...
    def query(self, vector: list[float], top_k: int, filters: JsonDict | None = None, trace: "TraceContext | None" = None) -> list[SearchHit]: ...
    def get_by_ids(self, ids: list[str]) -> list[ChunkRecord]: ...
    def delete_by_metadata(self, filters: JsonDict) -> int: ...

class BM25IndexStore(Protocol):
    def upsert(self, chunks: list[Chunk], sparse_vectors: list[JsonDict], trace: "TraceContext | None" = None) -> None: ...
    def query(self, keywords: list[str], top_k: int, filters: JsonDict | None = None, trace: "TraceContext | None" = None) -> list[SearchHit]: ...
    def remove_document(self, source_path: str, collection: str) -> None: ...

class ImageStore(Protocol):
    def save_refs(self, images: list[ImageRef], trace: "TraceContext | None" = None) -> None: ...
    def get(self, image_id: str) -> ImageRef | None: ...
    def list_by_document(self, source_path: str, collection: str) -> list[ImageRef]: ...
    def delete_by_document(self, source_path: str, collection: str) -> int: ...
```

约束：

- `VectorStore.query` 和 `BM25IndexStore.query` 只返回中性的 `SearchHit`，不能返回或依赖 `RetrievalCandidate`。
- `SearchHit` 必须带 `text/metadata`；如果底层无法直接返回，adapter 内部负责补齐。
- `SearchHit.score_kind` 描述底层分数含义，分数归一化由 Retriever 层负责。
- 删除操作按 metadata/source_path/collection 执行，不让上层理解各存储内部索引。
- BM25 和 VectorStore 的 `chunk_id` 必须来自同一 `ChunkRecord.id`。

### 5.7 IngestionPipeline 编排

```python
class IngestionPipeline:
    def __init__(
        self,
        integrity: FileIntegrityStore,
        loader: BaseLoader,
        chunker: DocumentChunker,
        transforms: list[BaseTransform],
        embedding: BaseEmbedding,
        sparse_encoder: SparseEncoder,
        vector_store: BaseVectorStore,
        bm25_store: BM25IndexStore,
        image_store: ImageStore,
    ) -> None: ...

    def run(
        self,
        request: IngestionRequest,
        on_progress: "Callable[[str, int, int], None] | None" = None,
        trace: "TraceContext | None" = None,
    ) -> IngestionResult: ...
```

阶段顺序：

```text
integrity -> load -> split -> transform -> embed/sparse -> upsert vector/bm25/images -> mark_success
```

Review 关注：

- pipeline 只编排，不包含 provider 细节。
- 每阶段失败能记录 trace 并返回/抛出清晰错误。
- `force=True` 能绕过 skip，但仍复用幂等 upsert。

---

## 6. Query 链路接口

### 6.1 QueryProcessor

```python
class QueryProcessor(Protocol):
    def process(self, request: QueryRequest, trace: "TraceContext | None" = None) -> ProcessedQuery: ...
```

约束：

- 只输出 standalone query、keywords、filters。
- 不执行检索，不调用 embedding。
- 过滤条件必须是通用 `JsonDict`，不能泄露 Chroma 私有语法。

### 6.2 Retriever

```python
class DenseRetriever(Protocol):
    def retrieve(self, query: str, top_k: int, filters: JsonDict | None = None, trace: "TraceContext | None" = None) -> list[RetrievalCandidate]: ...

class SparseRetriever(Protocol):
    def retrieve(self, keywords: list[str], top_k: int, filters: JsonDict | None = None, trace: "TraceContext | None" = None) -> list[RetrievalCandidate]: ...
```

约束：

- Dense route 内部组合 `BaseEmbedding + BaseVectorStore`。
- Sparse route 内部组合 `BM25IndexStore`，需要正文时通过 `BaseVectorStore.get_by_ids` 或 BM25 payload 补齐。
- Retriever 负责把底层 `SearchHit` 映射为 `RetrievalCandidate`，并写入 `source="dense"|"sparse"`、`rank` 和原始分数 debug。
- 任一路失败时可以降级，但必须写 trace event。

### 6.3 Fusion、Filter、Rerank

```python
class FusionStrategy(Protocol):
    def fuse(self, ranked_lists: list[list[RetrievalCandidate]], top_k: int, trace: "TraceContext | None" = None) -> list[RetrievalCandidate]: ...

class MetadataFilter(Protocol):
    def apply(self, candidates: list[RetrievalCandidate], filters: JsonDict, trace: "TraceContext | None" = None) -> list[RetrievalCandidate]: ...

class BaseReranker(Protocol):
    def rerank(self, query: str, candidates: list[RetrievalCandidate], top_k: int, trace: "TraceContext | None" = None) -> list[RetrievalCandidate]: ...
```

约束：

- Fusion 默认 RRF，但 `FusionStrategy` 不绑定 RRF。
- Reranker 必须可关闭：`NoneReranker` 保留原顺序。
- Reranker 失败必须回退到 fusion 排名，不让查询失败。

### 6.4 QueryEngine

```python
class QueryEngine(Protocol):
    def search(self, request: QueryRequest, trace: "TraceContext | None" = None) -> list[RetrievalCandidate]: ...
```

默认编排：

```text
QueryRequest
  -> QueryProcessor
  -> DenseRetriever + SparseRetriever
  -> FusionStrategy
  -> MetadataFilter
  -> BaseReranker
  -> top-k candidates
```

Review 关注：

- 每个阶段可以 fake 并单测编排。
- candidate 数量限制在每阶段明确配置。
- no result、empty query、single-route failure 都有明确行为。

---

## 7. Response 与 MCP 接口

### 7.1 ResponseBuilder

```python
class CitationGenerator(Protocol):
    def generate(self, candidates: list[RetrievalCandidate]) -> list[Citation]: ...

class MultimodalAssembler(Protocol):
    def resolve_images(self, candidates: list[RetrievalCandidate], include_images: bool) -> list[ImagePayload]: ...

class ResponseBuilder(Protocol):
    def build(self, request: QueryRequest, candidates: list[RetrievalCandidate], trace: "TraceContext | None" = None) -> QueryResponse: ...
```

约束：

- `ResponseBuilder` 输出 domain-level `QueryResponse`，不是 MCP raw response。
- MCP-specific `content/structuredContent` 由 tool adapter 负责转换。
- 本地模式可把 `ImageRef.path` 解析成 `ImagePayload.data_base64`；HTTP 模式只消费远端返回的 `ImagePayload`，不得假设远端路径可读。

### 7.2 MCP Tool Adapter

```python
class ToolHandler(Protocol):
    name: str
    input_schema: JsonDict
    def call(self, arguments: JsonDict) -> JsonDict: ...
```

`query_knowledge_hub` 只允许：

```text
arguments -> QueryRequest -> KnowledgeService.query -> QueryResponse -> MCPToolResult
```

禁止：

```text
arguments -> DenseRetriever / ChromaStore / BM25Indexer
```

Contract test：

- 注入 `FakeKnowledgeService`。
- 断言 `content[0].type == "text"`。
- 断言 `structuredContent.citations[*]` 字段稳定。
- 异常转换为规范 MCP/JSON-RPC error，不泄露堆栈和密钥。

---

## 8. Observability 与 Evaluation 接口

### 8.1 Trace

```python
class BaseTracer(Protocol):
    def start_run(self, name: str, run_type: str, inputs: JsonDict) -> "RunContext": ...
    def start_span(self, run_id: str, name: str, span_type: str, inputs: JsonDict) -> "SpanContext": ...
    def record_event(self, run_id: str, span_id: str | None, event_type: str, payload: JsonDict) -> None: ...
    def record_artifact(self, run_id: str, span_id: str | None, artifact_type: str, content: JsonDict | str) -> None: ...

class BaseTraceSink(Protocol):
    def on_run_started(self, run: JsonDict) -> None: ...
    def on_run_finished(self, run: JsonDict) -> None: ...
    def on_span_started(self, span: JsonDict) -> None: ...
    def on_span_finished(self, span: JsonDict) -> None: ...
    def on_event(self, event: JsonDict) -> None: ...
    def on_artifact(self, artifact: JsonDict) -> None: ...
```

约束：

- 业务组件最多依赖 `BaseTracer` 或 `TraceContext`。
- 外部观测平台只实现 `BaseTraceSink`。
- sink 异常必须被吞掉并记录 warning，不能影响主流程。

### 8.2 Cache

```python
class BaseCacheStore(Protocol):
    def get(self, namespace: str, key: str) -> JsonDict | None: ...
    def set(self, namespace: str, key: str, value: JsonDict, ttl_seconds: int | None = None) -> None: ...
    def delete(self, namespace: str, key: str) -> None: ...
```

初期 namespace：

- `embedding`
- `vision_caption`
- `retrieval`
- `rerank`

缓存命中只改变性能，不改变语义结果 shape。

### 8.3 Evaluation

```python
class BaseEvaluator(Protocol):
    name: str
    def evaluate(self, case: EvaluationCase, response: QueryResponse, trace: "TraceContext | None" = None) -> dict[str, float]: ...

class EvalRunner(Protocol):
    def run(self, cases: list[EvaluationCase], evaluators: list[BaseEvaluator]) -> EvaluationReport: ...
```

约束：

- Evaluator 输出扁平 `dict[str, float]`，便于 Dashboard、CI、JSONL 导出复用。
- Ragas/DeepEval 只能作为 adapter，不把第三方对象传出接口。

---

## 9. Factory 与配置装配

```python
class ComponentRegistry(Protocol):
    def build_knowledge_service(self) -> KnowledgeService: ...
    def build_ingestion_service(self) -> IngestionService: ...
    def build_document_service(self) -> DocumentService: ...
    def build_evaluation_service(self) -> EvaluationService: ...
    def build_query_engine(self) -> QueryEngine: ...
    def build_tracer(self) -> BaseTracer: ...
```

规则：

- `settings.yaml` 只在 factory/registry 层解析。
- 业务类构造函数接收接口实例，不接收整份 settings，除非该类本身就是 factory。
- provider-specific 配置不得穿透到 domain contract。
- 未知 provider/backend 必须 fail fast，并给出可读错误。

推荐测试：

```text
tests/unit/test_component_registry.py
tests/unit/test_*_factory.py
tests/unit/test_*_contract.py
```

---

## 10. 独立 Review 模板

每个接口实现提交时必须包含以下信息，方便单独 review：

```markdown
## Review Card: <module>

- Interface:
- Implementation:
- Inputs:
- Outputs:
- Invariants:
- Failure behavior:
- Trace fields:
- Fake dependencies:
- Contract tests:
- Integration tests:
- Config keys:
- Known limitations:
```

### 10.1 Ingestion Stage Review Checklist

- Loader：只负责 parse，不切分不入库。
- Chunker：chunk id 稳定，metadata/images 正确分发。
- Transform：幂等，失败降级，trace 记录 provider/method。
- Embedding：输入输出数量一致，空输入明确。
- Storage：upsert 幂等，删除接口可用。
- Pipeline：只编排，跳过/失败/成功状态清晰。

### 10.2 Query Stage Review Checklist

- QueryProcessor：不触发外部模型或检索。
- DenseRetriever：只依赖 embedding/vector store 接口。
- SparseRetriever：只依赖 BM25/index store 接口。
- Fusion：纯函数优先，deterministic。
- Reranker：可关闭、可回退、Top-M 限制明确。
- QueryEngine：每阶段 candidate 数量、filter 行为、fallback 行为可测。

### 10.3 MCP/Dashboard Review Checklist

- MCP Tool：只依赖 `KnowledgeService`。
- Dashboard 页面：只依赖 service，不直接访问底层 provider。
- Response：文本兜底永远在 `content[0]`。
- 图片：本地路径只在本地实现内解析，HTTP 模式不假设远端文件可读。

---

## 11. 验收标准

本接口规范完成后，后续代码实现必须满足：

- 任意 MCP tool 可用 fake service 完成单元测试。
- 任意 provider adapter 可用 contract test 独立 review。
- Ingestion 的 load/split/transform/embed/upsert 可逐阶段 fake。
- Query 的 process/dense/sparse/fusion/rerank/response 可逐阶段 fake。
- Dashboard 不依赖 MCP server 进程，也不直接访问 provider。
- Observability、cache、evaluation 失败不阻塞主 RAG 流程。
- 新增一个 LLM/Embedding/Reranker/VectorStore/Evaluator 后端，只需要：
  1. 实现对应 port；
  2. 注册 factory；
  3. 补 contract test；
  4. 修改配置。
