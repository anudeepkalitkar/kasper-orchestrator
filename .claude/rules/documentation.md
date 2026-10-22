# Rule: Documentation — durable system docs in `docs/`, local-only task docs, ADRs by visibility

**`docs/` answers "how does this system work and why" and lives as long as the system; `tasks/`
holds one living document per unit of work and closes when the task ships. Don't mix them. Drift
is a bug in both.** `tasks/` never enters git — local-only in every project, like `claude-memory/`
and `claude-temp/` (which differ: scratch is working residue, so nothing durable lives there).
`docs/adr/` goes by repo visibility — committed in a **private** repo, local-only in a public one
or one whose visibility cannot be read. The rest of `docs/` and the READMEs are committed.

## `docs/` — the system
```
docs/
  ARCHITECTURE.md   # services/modules, data, flows, boundaries — the source of truth
  design/           # per-feature design specs, written BEFORE non-trivial code
  deploy/           # topologies, runbooks, ops procedures, diagrams (+ source)
  adr/              # one decision per file, with the why
README.md           # what/why/how to run; a short README per package too
```
1. **Docs are living.** A change to architecture, a flow, a deploy step or a documented decision
   updates the doc **in the same change** — committed docs in that PR, ADR and task records as it
   happens. A doc that contradicts the code is a defect.
2. **Docs-first for non-trivial work**; skip the ceremony for small, obvious changes.
3. **Diagrams keep their source** (`.py`/`.mmd`/`.drawio` beside the image), never an image alone.

### ADRs — significant, hard-to-reverse decisions
Write one when a decision is costly to reverse or a teammate would ask why (stack, data model,
auth model, major dependency, API contract shape, deploy approach); never for trivial choices.
Name: `docs/adr/NNNN-short-title.md`, zero-padded, monotonic. **Tracked or local follows
the repo's exclude file**: the SessionStart hook writes `/docs/adr/` into `.git/info/exclude`
unless `gh` reports `PRIVATE` (no `gh`, no remote, offline, or any other answer keeps it local),
printing `ADRs: tracked (private repo)` or `ADRs: local-only`; a checkpoint stages ADRs exactly when
that entry is absent ([[git-workflow]]). A local-only ADR is never staged, pushed or reviewed in a
PR. Written docs-first and **append-only**: to change an accepted decision, write a new ADR that
supersedes it and mark the old one `Superseded by ADR-NNNN`. Template:
```
# ADR-NNNN: <short title>
- **Status:** proposed | accepted | superseded by ADR-MMMM
- **Date:** YYYY-MM-DD
- **Deciders:** <who>
## Context        <the forces at play>
## Decision       We will …
## Alternatives considered   <option — why rejected, one line each>
## Consequences   <what this makes easy/hard; trade-offs accepted>
```
Write it when the decision is made; link it from the task doc's Decisions & Notes.

## `tasks/` — one living doc per task (local only)
Create `tasks/<NNN>-<slug>.md` at task start (`/new-task`); the session running the task — under
KASPER, KASPER itself — is its single writer. Keep it current: tick subtasks, record decisions,
add branch/PR/commit links, close with a Review. A repo already tracking `tasks/` is cleaned once:
`git rm -r --cached tasks` in a commit of its own (plus `docs/adr` where ADRs are local-only).
**The canonical template:**
```
# Task <NNN>: <title>

- **Status:** planning | in-progress | in-review | done
- **Branch:** <feature branch or n/a>
- **PR:** <link or n/a>
- **Created:** <YYYY-MM-DD>
- **Rounds:** 0/3
- **Verify pass:** Codex tester seat, then Codex reviewer seat — a bounded loop at task end

## Goal
<what done looks like>

## Plan / Subtasks
- [ ] 1. <subtask title> — commit: pending
      goal:       <observable outcome a verifier can judge>
      done-check: `<runnable command, e.g. ruff check . && mypy . && pytest tests/unit -k upload>`
      cap:        5
      owner:      <agent>

## Decisions & Notes
- <decision> — <why>

## Review
<filled when done: outcome + how verified>
```
Use absolute dates; write the done-check exactly as it runs in the repo (bare `mypy` where
`pyproject.toml` names the files). Tick a subtask only when its done-check ran green; record
cap-exhausted subtasks as **blocked**, never done.

Related: [[autonomous-workflow]], [[git-workflow]], [[boundaries]], [[code-standards]].
