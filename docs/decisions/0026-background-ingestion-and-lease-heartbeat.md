# ADR 0026: Background Dashboard Ingestion and Lease Heartbeats

> **状态**：Superseded（Streamlit 退役后改为 FastAPI lifespan 关闭 Job Service）
> **承接方案**：docs/plans/0001-production-readonly-rag-component.md C0

## Context

Streamlit reruns a page in response to each interaction. Running an entire PDF ingestion inside
that request keeps the browser attached to one long script execution. A 118-page document can
produce more than one thousand chunks; optional per-chunk refinement, metadata extraction, and
image captioning can therefore turn one click into thousands of serial model calls. The page looks
frozen, navigation is blocked, and the fixed ingestion claim may expire before publication.

## Decision

- Persist the uploaded PDF atomically before submitting work.
- Run Dashboard ingestion through a process-wide, single-worker job service cached by Streamlit.
- Keep immutable, lock-protected job snapshots containing status, stage, progress, result, and
  elapsed-time origin; poll those snapshots from a one-second Streamlit fragment.
- Recover the newest active job when the browser session is refreshed, while rejecting duplicate
  work for the same source and collection.
- Make rule-based fast ingestion the Dashboard default. LLM chunk refinement, LLM metadata
  enrichment, and image captioning run only when the user enables `AI 增强`.
- Build the fast profile from a copied `Settings` value so the process-wide configuration and CLI
  behavior are not mutated.
- Start a lease heartbeat immediately after a generation claim. Renew every one third of the lease
  duration, capped at 30 seconds, and stop the heartbeat before staging and publication.

## Why This Works

The request thread now performs only upload persistence, dependency assembly, and job submission.
The worker owns the expensive pipeline, so Streamlit can rerun, navigate, and repaint progress
without interrupting ingestion. A single worker also avoids concurrent writes from multiple
Dashboard clicks while the generation state machine remains the final concurrency authority.

The heartbeat preserves that authority during slow external calls. Every renewal uses the current
generation and claim token; a fenced worker cannot renew and therefore cannot silently regain
publication rights. Before publishing, the pipeline joins the heartbeat and surfaces any renewal
failure as a normal failed ingestion result.

Fast ingestion still performs parsing, deterministic cleanup, chunking, dense and sparse encoding,
and storage. It skips only optional generative enrichment, which should not be an accidental cost
multiplier for large uploads.

## Trade-offs

Job state is process-local. A Dashboard process restart loses live presentation state and stops its
worker, although the persisted claim and generation fence still prevent partial data from becoming
visible. A distributed deployment should replace the in-memory executor with a durable queue and
persisted job records. Model enrichment also remains serial; background execution fixes control
plane responsiveness, not the cost of intentionally enabling thousands of model calls.
