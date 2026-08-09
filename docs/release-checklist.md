# Release Checklist

## Automated

- [ ] Offline quality workflow passes on the release commit.
- [ ] `git diff --check` passes and the worktree contains no unintended files.
- [ ] HotpotQA derived data and attribution remain tracked; the 58 MB raw file remains ignored.

## Retrieval

- [ ] Real-provider HotpotQA BM25: Hit@5 >= 0.90 and MRR@5 >= 0.80.
- [ ] Real-provider PostgreSQL BM25 matches or improves its recorded baseline.
- [ ] All three text-to-image cases retrieve a returnable original image.
- [ ] Dense and reranker remain disabled unless a recorded benchmark proves an improvement.

## Local Release

- [ ] Secrets and `config/settings.local.yaml` are not tracked.
- [ ] After an index fingerprint or BM25 snapshot change, remove the old index and re-ingest all PDFs.
- [ ] Start the MCP server and dashboard, then verify readiness, one retrieval, and one image response.
- [ ] Record the commit, commands, metrics, migration note, and any residual risk before tagging.
