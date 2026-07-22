# Modular RAG MCP Server 图集

> 基于 [DEV_SPEC.md](DEV_SPEC.md) 整理。  
> 图使用 Mermaid 编写，可在 GitHub、VS Code Mermaid Preview、Obsidian 等工具中直接渲染。  
> Draw.io 版本已按 `DayuanJiang/next-ai-draw-io` 的 `.drawio` XML 结构生成：[diagrams/modular-rag-mcp-server.drawio](diagrams/modular-rag-mcp-server.drawio)。

Draw.io 文件包含 6 个页面：

- `01 系统架构`
- `02 Ingestion 时序`
- `03 Query 时序`
- `04 混合检索与重排`
- `05 可观测与评测闭环`
- `06 可插拔与配置驱动`

---

## 1. 总体系统架构图

主路径仍是本地 Stdio MCP Server；可选的网络访问能力只出现在 `KnowledgeService` 实现层。

```mermaid
flowchart TB
  subgraph clients["MCP Clients"]
    copilot["GitHub Copilot"]
    claude["Claude Desktop"]
    other["Other MCP Clients"]
  end

  subgraph mcp["MCP Server Layer"]
    transport["Stdio Transport"]
    protocol["JSON-RPC / MCP Protocol Handler"]
    tools["MCP Tools"]
    qtool["query_knowledge_hub"]
    listtool["list_collections"]
    summarytool["get_document_summary"]
  end

  subgraph service["KnowledgeService Boundary"]
    ks["KnowledgeService<br/>implementation hidden<br/>local by default, network-capable if configured"]
  end

  subgraph query["Query Engine"]
    qp["Query Processor"]
    dense["Dense Retriever"]
    sparse["Sparse Retriever / BM25"]
    fusion["RRF Fusion"]
    filter["Metadata Filter"]
    rerank["Reranker<br/>None / Cross-Encoder / LLM"]
    response["Response Builder<br/>TextContent / ImageContent / Citations"]
  end

  subgraph ingestion["Ingestion Pipeline"]
    loader["PDF Loader<br/>MarkItDown"]
    splitter["Splitter<br/>RecursiveCharacterTextSplitter"]
    transform["Transform<br/>Refine / Metadata / Image Caption"]
    embed["Embedding<br/>Dense + Sparse"]
    upsert["Upsert<br/>VectorStore + BM25 + ImageStore"]
  end

  subgraph libs["Pluggable Libraries"]
    llm["LLM Factory"]
    embf["Embedding Factory"]
    splitf["Splitter Factory"]
    vsf["VectorStore Factory"]
    rrf["Reranker Factory"]
    evalf["Evaluator Factory"]
  end

  subgraph storage["Local Storage"]
    chroma["Chroma Vector DB"]
    bm25["BM25 Index"]
    image["Image Store + Image Index SQLite"]
    history["Ingestion History SQLite"]
    logs["Trace Logs JSONL"]
  end

  subgraph obs["Observability & Dashboard"]
    trace["TraceContext"]
    dashboard["Streamlit Dashboard"]
    eval["Evaluation Module<br/>Ragas / Custom"]
  end

  clients --> transport --> protocol --> tools
  tools --> qtool --> ks
  tools --> listtool --> ks
  tools --> summarytool --> ks
  ks --> qp

  qp --> dense
  qp --> sparse
  dense --> fusion
  sparse --> fusion
  fusion --> filter
  filter --> rerank --> response --> protocol
  filter -. fallback .-> response

  loader --> splitter --> transform --> embed --> upsert
  upsert --> chroma
  upsert --> bm25
  upsert --> image
  loader --> history

  dense --> chroma
  sparse --> bm25
  response --> image

  llm --> transform
  llm --> rerank
  embf --> embed
  embf --> dense
  splitf --> splitter
  vsf --> chroma
  rrf --> rerank
  evalf --> eval

  query --> trace
  ingestion --> trace
  trace --> logs
  logs --> dashboard
  storage --> dashboard
  eval --> dashboard
```

---

## 2. 数据摄取 Ingestion 时序图

```mermaid
sequenceDiagram
  autonumber
  participant User as User / CLI / Dashboard
  participant Pipeline as IngestionPipeline
  participant Integrity as FileIntegrityChecker
  participant Loader as PDFLoader
  participant Splitter as Splitter
  participant Transform as TransformChain
  participant Vision as VisionLLM
  participant Embedder as EmbeddingClient
  participant Vector as ChromaStore
  participant BM25 as BM25Indexer
  participant Image as ImageStorage
  participant Trace as TraceContext

  User->>Pipeline: run(source_path, collection)
  Pipeline->>Trace: create ingestion trace
  Pipeline->>Integrity: compute_sha256(source_path)
  Integrity-->>Pipeline: file_hash

  alt file already processed successfully
    Pipeline->>Trace: record skipped
    Pipeline-->>User: IngestionResult(skipped=true)
  else new or changed file
    Pipeline->>Loader: load PDF
    Loader-->>Pipeline: Document(markdown, metadata, images)
    Pipeline->>Trace: record load stage

    Pipeline->>Splitter: split(Document)
    Splitter-->>Pipeline: chunks
    Pipeline->>Trace: record split stage

    Pipeline->>Transform: refine / enrich chunks
    loop image refs
      Transform->>Vision: generate caption(image, context)
      Vision-->>Transform: caption
      Transform->>Image: save image + index mapping
    end
    Transform-->>Pipeline: enriched chunks
    Pipeline->>Trace: record transform stage

    Pipeline->>Embedder: embed chunk texts
    Embedder-->>Pipeline: dense vectors
    Pipeline->>BM25: build sparse index
    BM25-->>Pipeline: sparse index updated
    Pipeline->>Trace: record embed stage

    Pipeline->>Vector: upsert chunks + vectors + metadata
    Vector-->>Pipeline: upsert result
    Pipeline->>Integrity: mark success(file_hash, chunk_count)
    Pipeline->>Trace: record upsert and finish
    Pipeline-->>User: IngestionResult(success)
  end
```

---

## 3. 查询 Query 时序图

```mermaid
sequenceDiagram
  autonumber
  participant Client as MCP Client
  participant Server as MCP Server
  participant Tool as query_knowledge_hub
  participant Service as KnowledgeService
  participant Trace as TraceContext
  participant QP as QueryProcessor
  participant Emb as EmbeddingClient
  participant Vector as ChromaStore
  participant Sparse as BM25Retriever
  participant Fusion as RRFFusion
  participant Rerank as Reranker
  participant Builder as ResponseBuilder
  participant Image as ImageStorage

  Client->>Server: tools/call query_knowledge_hub(query, top_k, collection)
  Server->>Tool: dispatch tool call
  Tool->>Service: query(QueryRequest)
  Note right of Service: implementation can be local or network-backed

  Service->>Trace: create query trace

  Service->>QP: process query
  QP-->>Service: standalone query, keywords, filters
  Service->>Trace: record query_processing

  par Dense route
    Service->>Emb: embed query
    Emb-->>Service: query vector
    Service->>Vector: search(query vector, filters, top_n)
    Vector-->>Service: dense candidates
  and Sparse route
    Service->>Sparse: search(keywords, filters, top_n)
    Sparse-->>Service: sparse candidates
  end

  Service->>Trace: record dense and sparse stages
  Service->>Fusion: fuse(dense candidates, sparse candidates)
  Fusion-->>Service: fused candidates
  Service->>Trace: record fusion

  alt reranker enabled
    Service->>Rerank: rerank(query, fused candidates)
    Rerank-->>Service: reranked candidates
  else reranker disabled or failed
    Service->>Trace: record fallback to fused ranking
  end

  Service->>Image: resolve image refs if any
  Image-->>Service: image bytes / paths
  Service->>Builder: build QueryResponse with citations
  Builder-->>Service: QueryResponse
  Service->>Trace: finish query trace

  Service-->>Tool: QueryResponse
  Tool->>Builder: build MCP response
  Builder-->>Tool: TextContent + optional ImageContent + structuredContent
  Tool-->>Server: tool result
  Server-->>Client: JSON-RPC response
```

---

## 4. 混合检索与重排流程图

```mermaid
flowchart LR
  query["User Query"] --> preprocess["Query Processing<br/>standalone query / keywords / filters"]

  preprocess --> dense_embed["Query Embedding"]
  dense_embed --> dense_search["Dense Search<br/>Chroma cosine similarity"]

  preprocess --> keyword["Keyword Extraction<br/>synonym / alias expansion"]
  keyword --> sparse_search["Sparse Search<br/>BM25"]

  dense_search --> dense_top["Dense Top-N"]
  sparse_search --> sparse_top["Sparse Top-N"]

  dense_top --> rrf["RRF Fusion"]
  sparse_top --> rrf

  rrf --> post_filter["Metadata Filter<br/>pre-filter if possible / post-filter fallback"]
  post_filter --> topm["Top-M Candidates"]

  topm --> rerank_choice{"Rerank enabled?"}
  rerank_choice -->|None| final_topk["Final Top-K"]
  rerank_choice -->|Cross-Encoder| cross["Cross-Encoder Scoring"]
  rerank_choice -->|LLM| llmrank["LLM Rerank JSON ids"]

  cross --> final_topk
  llmrank --> final_topk
  final_topk --> citations["Citations + Scores + Source Metadata"]
  citations --> response["MCP Tool Response"]
```

---

## 5. 可插拔组件关系图

```mermaid
classDiagram
  class Settings {
    +knowledge_service.mode
    +llm.provider
    +embedding.provider
    +splitter.backend
    +vector_store.backend
    +rerank.backend
    +evaluation.backends
  }

  class Factory {
    +from_settings(settings)
  }

  class KnowledgeService {
    <<interface>>
    +query(request)
    +list_collections()
    +get_document_summary(doc_id)
  }

  class BaseLLM {
    <<interface>>
    +chat(messages)
  }
  class BaseEmbedding {
    <<interface>>
    +embed(texts)
  }
  class BaseSplitter {
    <<interface>>
    +split(document)
  }
  class BaseVectorStore {
    <<interface>>
    +add(chunks)
    +query(vector, filters)
    +delete_by_metadata(filter)
  }
  class BaseReranker {
    <<interface>>
    +rerank(query, candidates)
  }
  class BaseEvaluator {
    <<interface>>
    +evaluate(query, retrieved_chunks, answer, ground_truth)
  }

  Settings --> Factory
  Factory --> KnowledgeService
  Factory --> BaseLLM
  Factory --> BaseEmbedding
  Factory --> BaseSplitter
  Factory --> BaseVectorStore
  Factory --> BaseReranker
  Factory --> BaseEvaluator

  BaseLLM <|.. AzureOpenAI
  BaseLLM <|.. OpenAI
  BaseLLM <|.. Ollama
  BaseLLM <|.. DeepSeek

  BaseEmbedding <|.. OpenAIEmbedding
  BaseEmbedding <|.. BGEEmbedding
  BaseEmbedding <|.. OllamaEmbedding

  BaseSplitter <|.. RecursiveCharacterTextSplitter
  BaseVectorStore <|.. ChromaStore
  BaseReranker <|.. CrossEncoderReranker
  BaseReranker <|.. LLMReranker
  BaseEvaluator <|.. RagasEvaluator
  BaseEvaluator <|.. CustomEvaluator
  KnowledgeService <|.. LocalKnowledgeService
  KnowledgeService <|.. HttpRetrievalServiceClient
```

---

## 6. 本地存储关系图

```mermaid
flowchart TB
  docs["data/documents/{collection}<br/>原始文档"] --> loader["PDF Loader"]
  loader --> chunks["Chunks + Metadata"]
  loader --> images_raw["Extracted Images"]

  chunks --> chroma["data/db/chroma<br/>Dense vectors + chunk payload"]
  chunks --> bm25["data/db/bm25<br/>BM25 inverted index"]
  images_raw --> image_files["data/images/{collection}<br/>原始图片文件"]
  images_raw --> image_db["data/db/image_index.db<br/>image_id -> file_path"]

  docs --> history["data/db/ingestion_history.db<br/>file_hash / status / chunk_count"]

  query["Query Engine"] --> chroma
  query --> bm25
  query --> image_db
  image_db --> image_files

  trace["TraceContext"] --> trace_logs["logs/traces.jsonl"]
  dashboard["Streamlit Dashboard"] --> trace_logs
  dashboard --> chroma
  dashboard --> image_db
  dashboard --> history
```

---

## 7. 可观测性与 Dashboard 数据流图

Dashboard 仅作为本地开发者工具使用，不注册为 MCP tools，也不作为对外 API 层。

```mermaid
flowchart LR
  subgraph pipelines["Runtime Pipelines"]
    ingestion["Ingestion Pipeline"]
    query["Query Pipeline"]
  end

  ingestion --> trace["TraceContext"]
  query --> trace

  trace --> stages["Stage Records<br/>method / provider / details / latency"]
  stages --> jsonl["logs/traces.jsonl"]

  subgraph dashboard["Developer Dashboard (local only)"]
    overview["Overview"]
    browser["Data Browser"]
    ingest_mgr["Ingestion Manager"]
    ingest_trace["Ingestion Traces"]
    query_trace["Query Traces"]
    eval_panel["Evaluation Panel<br/>local dev only"]
  end

  jsonl --> ingest_trace
  jsonl --> query_trace
  jsonl --> overview

  stores["Chroma / ImageIndex / FileIntegrity / BM25"] --> overview
  stores --> browser
  stores --> ingest_mgr

  eval["EvalRunner + Evaluators"] --> eval_panel
```

---

## 8. 评估闭环流程图

```mermaid
flowchart TB
  golden["Golden Test Set<br/>query / expected_chunk_ids / expected_sources"] --> runner["EvalRunner"]
  runner --> search["Hybrid Search<br/>Dense + Sparse + RRF + Rerank"]
  search --> retrieved["Retrieved Results"]

  retrieved --> custom["Custom Metrics<br/>Hit Rate / MRR / NDCG / Latency"]
  retrieved --> ragas["Ragas Evaluator<br/>Faithfulness / Answer Relevancy / Context Precision"]

  custom --> report["Eval Report"]
  ragas --> report

  report --> dashboard["Evaluation Panel<br/>local metrics / trends / query details"]
  report --> ci["CI / PR Quality Gate"]
  report --> badcase["Bad Case Collection"]

  badcase --> improve["Tune Strategy<br/>chunk size / embedding / rerank / prompt"]
  improve --> runner
```

---

## 9. 文档删除与生命周期管理时序图

```mermaid
sequenceDiagram
  autonumber
  participant User as Dashboard User
  participant UI as Ingestion Manager
  participant Manager as DocumentManager
  participant Chroma as ChromaStore
  participant BM25 as BM25Indexer
  participant Images as ImageStorage
  participant Integrity as FileIntegrityChecker

  User->>UI: delete_document(source_path, collection)
  UI->>Manager: delete_document(source_path, collection)
  Manager->>Chroma: delete_by_metadata(source, collection)
  Chroma-->>Manager: deleted chunk count
  Manager->>BM25: remove_document(source)
  BM25-->>Manager: index updated
  Manager->>Images: delete images for source
  Images-->>Manager: deleted image count
  Manager->>Integrity: remove_record(file_hash)
  Integrity-->>Manager: record removed
  Manager-->>UI: DeleteResult
  UI-->>User: refreshed document list
```
