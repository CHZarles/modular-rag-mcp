# Title-Aware BM25 Release Record

- Date: 2026-08-09
- Base commit: `9b3f4fd`
- Production strategy: BM25 only
- Provider used for online evaluation: MiniMax `embo-01`, 1536 dimensions

## Scope

`metadata.title` is tokenized once with the unchanged Chunk body when sparse statistics are built.
Stored and returned Chunk text is unchanged. The tokenizer fingerprint and BM25 snapshot version were
advanced so a body-only index cannot be reused silently. Image coverage is now a strict `3/3` gate.

No new retrieval configuration, weighting, dependency, Dense default, reranker, or evaluation framework
was added. The offline CI workflow runs tests, Ruff, mypy, and the React lint/build without provider
credentials.

## Retrieval Evidence

Untouched HotpotQA baseline at `9b3f4fd`:

| Strategy | Hit@5 | MRR@5 |
|---|---:|---:|
| BM25 | 0.9083 | 0.7543 |
| Dense | 0.2750 | 0.1800 |
| Hybrid | 0.7250 | 0.4508 |

Images: `2/3`.

Title-aware result on the same 120-query HotpotQA benchmark:

| Strategy | Hit@5 | MRR@5 |
|---|---:|---:|
| BM25 | 0.9417 | 0.8326 |
| Dense | 0.2750 | 0.1800 |
| Hybrid | 0.8083 | 0.4878 |

Images: `3/3`, MRR@5 `0.7778`. BM25 passes both text gates and the image gate.

PostgreSQL with the three image cases in the same index:

| Strategy | Text Hit@5 | Text MRR@5 |
|---|---:|---:|
| BM25 | 0.9143 | 0.9000 |
| Dense | 0.5714 | 0.4619 |
| Hybrid | 0.8857 | 0.7543 |

Images: `3/3`, MRR@5 `0.8333`. For comparison with the older 38-case mixed report, combining the
35 text cases and 3 image cases gives BM25 Hit@5 `0.9211` and MRR@5 `0.8947`, versus the recorded
`0.9211` and `0.8553`. PostgreSQL therefore did not regress.

## Performance Evidence

Local deterministic benchmark on 10,000 title-bearing chunks, 100 queries, and `top_k=10`:

| Build | Query p50 | Query p95 | Query max |
|---:|---:|---:|---:|
| 82.84 ms | 59.37 ms | 96.35 ms | 129.77 ms |

The measured p95 is below the 500 ms optimization threshold, so the query-time statistics rebuild was
not changed.

## Verification

- `NO_PROXY=127.0.0.1,localhost .venv/bin/pytest`: `646 passed, 5 skipped`.
- `.venv/bin/ruff check src scripts tests`: passed.
- `.venv/bin/mypy src scripts`: passed for 131 source files.
- `npm --prefix web run lint`: passed.
- `npm --prefix web run build`: passed.
- `git diff --check`: passed.
- Real HotpotQA and PostgreSQL evaluation commands both returned exit code 0.

## Decision And Migration

Keep BM25 as the production default. Dense and hybrid remain disabled because neither meets the quality
gates. Do not add a reranker or another heuristic based on this result.

Before using an existing local installation, remove the old BM25 snapshot and re-ingest every PDF. If
Dense retrieval was previously enabled, rebuild its local index as well because the index fingerprint
changed. Real-provider scores are point-in-time regression evidence and should be rerun before a release
when the provider model or benchmark data changes.
