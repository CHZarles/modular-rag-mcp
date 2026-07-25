# Workflow: dev-spec-sweep

> Sweep the entire `DEV_SPEC.md` schedule end-to-end, one task per commit, fully unattended except for hard failures.

## Trigger

- **Event**: user invokes `/loop-me 使用repo里的skill，根据spec开始开发` against a repo whose root contains `DEV_SPEC.md` and `.claude/skills/auto-coder/`.
- **Required state at trigger**:
  - Working tree is on a clean branch (e.g. `clean-start`).
  - `config/settings.yaml` exists (A1 has been delivered at least once).
  - No in-progress merge / rebase.
- **Pre-flight (silent, do not ask)**:
  - `git status --porcelain` must be empty. If not, refuse to start.
  - `python .claude/skills/auto-coder/scripts/sync_spec.py --force` must succeed.
  - `pytest -q` must currently pass. If it does not, abort and report.

## Loop body

For each task in `06-schedule.md`, in order, picking the first `IN_PROGRESS` then the first `NOT_STARTED`:

1. **Sync spec** — re-run `python .claude/skills/auto-coder/scripts/sync_spec.py` (no `--force`; cheap idempotent step).
2. **Pick task** — read `references/06-schedule.md`; if the task has external dependencies that are not yet satisfied (`[x]`), skip with a logged reason.
3. **Read context** — load `references/03-tech-stack.md`, `04-testing.md`, `05-architecture.md` for the task; extract inputs/outputs, file list, acceptance criteria.
4. **Implement** — write the code exactly as the spec demands; prefer editing existing files over creating new ones; honour `config/settings.yaml` over hard-coded constants.
5. **Write tests** — add tests under `tests/unit/` or `tests/integration/` per spec; mock external services.
6. **Self-review** — before running tests, sanity-check imports and file paths.
7. **Test & auto-fix** — run the relevant pytest file. On failure, analyse → fix → retry, **up to 3 rounds**. Use the **full** error message; do not paraphrase.
8. **Persist** — update `DEV_SPEC.md` status cell to `[x]`; re-sync.
9. **Auto-commit** — `git add` the changed files explicitly (no `git add -A`); commit with message `feat(<stage>): [<task-id>] <task name>`. Use `--no-verify` is forbidden.
10. **Loop** — go to step 1.

Continue until **either** the schedule contains zero `NOT_STARTED` and zero `IN_PROGRESS` rows **or** a task exhausts its 3 auto-fix rounds.

## Checkpoints

This workflow has **one** checkpoint type. There is no per-task confirmation, no per-stage confirmation, and no commit pause.

### Hard-failure checkpoint (only pause)

Fires when:
- Step 7 has used all 3 auto-fix rounds without a green pytest, **or**
- Pre-flight failed (dirty tree, sync error, baseline pytest already red).

Brief presented at this checkpoint:

```
[STUCK] <task-id> <task name>
  Round 1 error: <one-line summary>
  Round 2 error: <one-line summary>
  Round 3 error: <one-line summary>
  Suspected root cause: <one-sentence hypothesis>
  Files touched: <list>
  Suggested next action: skip | manual-fix | debug
```

The workflow then halts. No automatic `git stash`, no automatic rollback. The working tree is left exactly as the last failed attempt left it.

### Final completion brief

Fires only when the loop body exits with zero remaining tasks. Brief:

```
[DONE] dev-spec-sweep complete
  Tasks committed: N / 69
  Skipped (with reason): <list or "none">
  Last commit: <sha> <subject>
  Working tree: clean
  Branch: <name>
```

## Push-right

- **No** mid-task checkpoint: the implementer writes the code, writes the tests, runs them, fixes them, commits, and moves on without asking.
- **No** commit-message review: the message is fixed-format and committed as-is.
- **No** "should I also do task X" questions: only the next-scheduled task is touched.
- **No** branch switching mid-sweep.
- The only human touch-point is the hard-failure brief or the final completion brief.

## What an implementer agent needs (no further questions)

- Repo path: implicit from invocation cwd.
- Branch: stays on whatever branch the trigger was fired on; never `git checkout`s another branch.
- Commit author: whatever `git config user.*` already says. Do not override.
- Commit signing: whatever the repo's hooks demand. Do not skip.
- Test runner: `pytest` from `pyproject.toml`. No custom invocation.
- Spec source of truth: `DEV_SPEC.md`. Chapter files under `.claude/skills/auto-coder/references/` are read-only caches re-derived by `sync_spec.py`.
- Reference map: `references/01-overview.md` (project context), `02-features.md` (when in doubt about feature scope), `03-tech-stack.md` (libraries), `04-testing.md` (test layout), `05-architecture.md` (module design), `06-schedule.md` (task queue), `07-future.md` (out of scope — do **not** pull tasks from here).

## Out of scope

- Auto-merging to `main`.
- Auto-pushing to remote.
- Editing `DEV_SPEC_AGENT_OBSERVABILITY.md` or `DEV_SPEC_INTERFACES.md` — those are reference docs, not in the task schedule.
- Touching `README.md` (lives in stage I).
- Running `qa-tester` / `resume-writer` / `package` skills.