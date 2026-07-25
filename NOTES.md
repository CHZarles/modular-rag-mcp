# NOTES

## Project identity

- **Name**: `modular-rag-mcp-server` (aka "smart-knowledge-hub" in older docs)
- **Goal**: a learn-by-teaching RAG + MCP educational framework; the repo itself is the artefact.
- **Single source of truth for work**: `DEV_SPEC.md` (the 69-task schedule in §6).
- **Author intent**: project owner wants to wake up to finished artefacts, not babysit the loop.

## Tools / channels (canonical names)

- **DEV_SPEC** — the spec document at repo root. Authoritative schedule lives in §6.
- **auto-coder** — the skill that drives one task per cycle. Lives in `.claude/skills/auto-coder/`.
- **Sync spec** — `python .claude/skills/auto-coder/scripts/sync_spec.py [--force]`. Re-derives `.claude/skills/auto-coder/references/*.md` from `DEV_SPEC.md`.
- **Reference chapters** — `01-overview`, `02-features`, `03-tech-stack`, `04-testing`, `05-architecture`, `06-schedule`, `07-future`. Read-only caches; never edited directly.
- **Loop** — a recurring dev sweep over DEV_SPEC. Currently captured as `workflows/dev-spec-sweep.md`.

## Terminology

- **Task** — a single `[ ]` / `[~]` / `[x]` row in the §6 progress table.
- **Stage** — the grouping letter (A / B / C / … / I).
- **Checkpoint** — human touch-point. In this project, only **hard-failure** is one. Commit is not a checkpoint.
- **Brief** — the message shown at a checkpoint. Format is fixed in the workflow spec.

## Status (as of 2026-07-25)

- Stage A: A1 ✅, A2 ⬜, A3 ⬜
- Total: 1 / 69 (≈ 1 %)
- Loop workflow: `workflows/dev-spec-sweep.md` drafted but not yet exercised end-to-end.