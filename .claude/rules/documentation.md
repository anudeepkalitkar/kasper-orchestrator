# Rule: Documentation — durable system docs in `docs/`, local-only task docs, ADRs by visibility

**`docs/` answers "how does this system work and why is it built this way" and lives as long as
the system; `tasks/` holds one living document per unit of work and closes when the task ships.
Don't mix the two. Drift is a bug in both. `tasks/` never enters git: it is a local-only working
document in every project, exactly like `claude-memory/` and `claude-temp/` — which differs in one
way: scratch is working residue, accumulating until the human clears it by hand, so nothing durable
lives there. `docs/adr/` goes by the repo's visibility — committed in a **private** repo, local-only
in a public one or one whose visibility cannot be read — while the rest of `docs/` and the READMEs
are committed with the code.**

## `docs/` — the system
```
docs/
  ARCHITECTURE.md   # services/modules, data, flows, boundaries — the source of truth
  design/           # per-screen/feature design specs, written BEFORE non-trivial UI/feature code
  deploy/           # topologies, runbooks, ops procedures, diagrams (+ diagram source)
  adr/              # one decision per file, with the why — committed only in a private repo
README.md           # repo root: what/why/how to run; a short README per package too
```
1. **Docs are living.** A change that alters architecture, a flow, a deploy step, or a documented
   decision updates the doc **in the same change** — the committed docs in that PR, the ADR and
   task records as it happens. A doc that contradicts the code is a defect.
2. **Docs-first for non-trivial work.** Architecture/design are written before the code that
   implements them; a design spec in `docs/design/` precedes non-trivial UI.
   Skip the ceremony for small, obvious changes.
3. **Diagrams keep their source** (`.py`/`.mmd`/`.drawio` committed next to the rendered image) —
   never a binary image alone.

### ADRs — record every significant, hard-to-reverse decision (local unless the repo is private)
Write one when the decision is costly to reverse or a future teammate would ask why (stack, data
model, sync-vs-async, auth model, major dependency, API contract shape, deploy approach); don't
ADR trivial choices. Naming: `docs/adr/NNNN-short-title.md`, zero-padded, monotonic. **Whether
`docs/adr/` is committed follows the repo's visibility.** A **private** repo tracks its ADRs with
the code — they are the team's record, and they land in the PR that makes the decision. A
**public** repo, or one whose visibility cannot be read (no `gh`, no remote, offline, a lookup
that times out, any answer other than `PRIVATE` — `INTERNAL` included), keeps them local-only:
the author's own record on the author's own machine, never staged, pushed, or reviewed in a PR.
The SessionStart hook decides it once per session and prints which it is — `ADRs: tracked
(private repo)` or `ADRs: local-only`; a public repo that already tracks them is cleaned once
(below), and a private repo still holding untracked ADRs gets them staged in a `chore` commit of
their own the next time a task lands there. Written docs-first and **append-only** either way:
to change an accepted decision, write a new ADR that supersedes it and mark the old one
`Superseded by ADR-NNNN` — the trail of why things changed is what that record keeps. Template:
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
Write the ADR when the decision is made, not after; link it from the task doc's Decisions & Notes.

## `tasks/` — one living doc per task (local only)
`tasks/` is excluded from git and never committed, in every repo: the doc is live state for the
session doing the work and for whoever resumes it on this machine, not something the repo carries —
and a repo already tracking it is cleaned once, deliberately: `git rm -r --cached tasks` in a commit
of its own (add `docs/adr` to that command in a **public** repo; a private one keeps its ADRs
tracked), after which they stay out — and `/checkpoint`'s staging must never re-add them.
Create `tasks/<NNN>-<slug>.md` at task start (`/new-task` scaffolds it; the session running
the task is its single writer — under KASPER, that is the KASPER session itself, whose one
piece of hands-on work is `tasks/` bookkeeping). Keep it current as state changes — tick subtasks as they complete,
record decisions when made, add branch/PR/commit links as they happen; close with a Review section.
**The canonical template** (loop-ready fields per [[autonomous-workflow]] §3 — they are what
makes the doc resumable by any fresh session):
```
# Task <NNN>: <title>

- **Status:** planning | in-progress | in-review | done
- **Branch:** <feature branch or n/a>
- **PR:** <link or n/a>
- **Created:** <YYYY-MM-DD>
- **Verify pass:** Codex tester seat, then Codex reviewer seat — once, at task end

## Goal
<what done looks like>

## Plan / Subtasks
- [ ] 1. <subtask title> — commit: pending
      goal:       <observable outcome a verifier can judge>
      done-check: `<runnable command, e.g. ruff check . && mypy . && pytest tests/unit -k upload>`
      cap:        5
      owner:      <agent>
- [ ] 2. ...

## Decisions & Notes
- <decision> — <why>

## Review
<filled when done: outcome + how verified>
```
Use absolute dates, and write the done-check exactly as it runs in the repo — **bare `mypy`**
where `pyproject.toml` names the files to check, `mypy .` otherwise. Tick a subtask only when
its done-check ran green; record cap-exhausted subtasks as **blocked**, never done.

## How to apply
- New project: create `docs/ARCHITECTURE.md`, `docs/adr/`, and (with a UI) `docs/design/` during
  foundations, before feature code. `tasks/` is created on demand and stays out of git — the
  SessionStart hook (`project_dirs.py`) adds it, with `claude-memory/` and `claude-temp/`, to the
  repo's local `.git/info/exclude`, and adds `docs/adr/` too unless `gh` reports the repo
  `PRIVATE`, where it leaves that entry out and drops one an earlier run wrote; the project's
  shared `.gitignore` is never touched, so the rule never reaches anyone else's checkout.
- Every change that alters system behavior: check whether ARCHITECTURE, a design spec, or a runbook
  needs updating — same PR; whether the decision needs an ADR — same day, in the decision record.
- Task state changed? Update the task doc — a stale task doc defeats its
  purpose.

Related: [[autonomous-workflow]] (loop-ready fields, docs-first planning), [[git-workflow]]
(branch/PR links tracked in the task doc), [[code-standards]] (don't over-document trivia).
