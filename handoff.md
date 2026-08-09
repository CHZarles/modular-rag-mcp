# Session Handoff

## Workspace

- Repository: `/Users/charles/Documents/modular-rag-mcp`
- Branch: `mac-local`
- This handoff is included with the completed MCP upload and Agent CLI implementation.
- No API key or temporary token was added to tracked files.

## Current Product Boundary

The project is a small local shared MCP retrieval service. It returns retrieved chunks, sources, page numbers, stage scores and related images; it does not generate answers.

The MCP surface now contains five Tools:

```text
query_knowledge_hub
list_collections
get_document_summary
upload_document
get_ingestion_job
```

The instance does not implement authentication, authorization, tenant isolation or distributed storage. `collection` is a retrieval namespace, not a security boundary.

## Completed In This Session

### MCP Upload

- Added `upload_document`, accepting `filename`, standard PDF Base64, `collection`, `force` and `ai_enrichment`.
- Added `get_ingestion_job`, returning `queued`, `running`, `success`, `skipped` or `failed` state with stage progress.
- Limited decoded files to 50 MiB and rejected invalid Base64, empty files, non-PDF content and invalid filenames.
- Kept absolute paths, file hashes, uploaded content and raw Provider exceptions out of MCP job responses.
- Propagated the MCP `X-Request-ID` value into the background `IngestionRequest` and ingestion Trace.
- Registered both Tools for stdio and Streamable HTTP without loading ingestion Providers during MCP initialization.

### Safe Concurrency

- Promoted `IngestionJobService` to `src/application/ingestion_jobs.py` for Dashboard and MCP reuse.
- Added a bounded active queue of 8 jobs, one ingestion worker and a terminal history limit of 100 jobs.
- Added `UploadIngestionCoordinator` with independent temporary files, `fsync`, atomic publication and per-target POSIX `flock`.
- The target lock is acquired before a job is accepted and held through ingestion. A concurrent upload for the same stable filename returns `document_busy` without changing the active source file.
- Different files may be received concurrently and queued, but ingestion remains serial so Chroma, BM25 and image indexes are not written by multiple Pipelines at once.
- Existing Pipeline snapshots, SQLite claim/lease, lease heartbeat and generation fence remain the publication correctness boundary.
- Dashboard `POST /api/ingestion/jobs` now uses the same coordinator and reads at most 50 MiB plus one byte from `UploadFile` before rejecting an oversized file.

### Agent CLI

- Added the packaged `modular-rag-cli` console entry point in `extension/cli/`.
- Commands: `collections`, `query`, `document`, `upload` and `job`.
- stdout contains one compact `structuredContent` JSON value; diagnostics use stderr.
- CLI exit codes: `0` success, `1` MCP Tool business error, `2` input/connection/protocol failure.
- CLI uses the MCP SDK Streamable HTTP client with `trust_env=False`; it does not call the ingestion Pipeline directly.

## Main Files

```text
src/application/ingestion_jobs.py
src/application/upload_ingestion.py
src/mcp_server/tools/ingestion_jobs.py
src/mcp_server/server.py
src/observability/dashboard/api.py
extension/cli/main.py
docs/decisions/0028-mcp-upload-concurrency.md
```

The old Dashboard job-service module remains as a small import compatibility shim; new code imports the application-layer service.

## Verification Completed

All of the following passed on 2026-08-09:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts extension
.venv/bin/mypy src scripts extension

npm --prefix web test
npm --prefix web run lint
npm --prefix web run build

uv build --wheel
uv run --isolated --no-project \
  --with ./dist/modular_rag_mcp_server-0.1.1-py3-none-any.whl \
  modular-rag-cli --help
```

The test suite includes:

- same-process and cross-process target lock checks;
- bounded queue and staging cleanup checks;
- concurrent same-document upload rejection through real HTTP MCP;
- Dashboard same-file concurrency regression coverage;
- actual Agent CLI upload and job polling through an in-process HTTP MCP server;
- owned coordinator shutdown during MCP lifespan termination;
- Wheel package and console-entry verification.

No live MinerU ingestion was performed as part of this change. Pipeline integration uses deterministic fakes; the existing full Pipeline suite also passed.

## Operation

Start the HTTP MCP server:

```bash
RAG_SETTINGS_PATH="$PWD/config/settings.yaml" modular-rag-mcp-http
```

Submit and poll from the CLI:

```bash
modular-rag-cli upload ./document.pdf --collection default
modular-rag-cli job <job_id>
```

For the configured MinerU loader, the server process still requires `MINERU_API_TOKEN`. `RAG_MCP_URL` or CLI `--url` selects a non-default MCP endpoint.

## Deliberate Limits

- Job state is process-local. After a server restart, clients may resubmit; content idempotency and generation fencing prevent duplicate publication.
- The file lock uses POSIX `flock`, matching the current macOS/Linux deployment boundary.
- Upload uses Base64 in one MCP Tool call and is limited to 50 MiB. Chunked or multipart upload is not implemented.
- Ingestion is intentionally single-worker. Add parallel Pipeline workers only after every backing store has a measured and tested multi-writer contract.
- No Synology integration is included.

## Next Work

1. Run one real PDF through `modular-rag-cli upload` with the configured MinerU token and confirm the resulting document through `query` and `document`.
2. Add durable SQLite job state only if restart-time polling continuity becomes a real requirement; it is not required for current local operation.
3. Do not add multi-worker ingestion, distributed queues or multi-tenant abstractions without a concrete deployment requirement.
