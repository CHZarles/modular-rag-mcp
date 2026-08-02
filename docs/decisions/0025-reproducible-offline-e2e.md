# ADR 0025: Reproducible Offline End-to-End Acceptance

## Context

The final acceptance path crosses separate CLI, MCP, Dashboard, and evaluation processes.
Using an online embedding endpoint makes that engineering check depend on credentials, quota,
network availability, and provider compatibility. A test-only provider registered in one Python
process also cannot be discovered by a separately launched MCP server.

## Decision

- Provide a built-in `hash` embedding backend for local acceptance and demonstrations.
- Build its vectors with deterministic signed feature hashing over normalized lexical tokens and
  Chinese character n-grams, then L2-normalize each vector.
- Resolve CLI configuration in this order: an explicit Python `settings_path`,
  `RAG_SETTINGS_PATH`, then `config/settings.yaml`.
- Keep the same settings file, embedding dimension, and persisted stores across ingest, query,
  MCP, Dashboard, and evaluation processes.
- Treat collection as an explicit data boundary. Ingest acceptance data into the collection that
  each query or golden-set case names rather than silently changing query defaults.

## Why This Works

Feature hashing maps each lexical feature to a stable vector bucket and sign. It needs no learned
weights or vocabulary file, so the same text produces the same vector in every process. Shared
configuration then makes the persisted Chroma vectors dimensionally compatible with query
vectors, while shared SQLite/BM25 paths preserve generation visibility and sparse retrieval.

The environment-variable layer is important for subprocess acceptance: the official MCP client,
Streamlit, and shell CLIs can all select one isolated runtime without modifying production
configuration. Explicit function arguments remain highest priority so tests and embedding callers
stay hermetic.

## Trade-offs

`hash` is not a semantic embedding model. It captures lexical overlap and is suitable for testing
composition, persistence, protocol, trace, and evaluation boundaries, but it must not be used as
evidence of production retrieval quality. Production deployments should use OpenAI, Azure, Ollama,
or another registered learned embedding backend. Retrieval quality remains a separate golden-set
gate rather than being conflated with infrastructure acceptance.
