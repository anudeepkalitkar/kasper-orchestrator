---
description: Scaffold an Architecture Decision Record under docs/adr/ (local, never committed).
argument-hint: <decision title>
allowed-tools: Bash(ls docs/adr*), Bash(date *), Read, Write
---

Create an Architecture Decision Record for: **$ARGUMENTS** — per `.claude/rules/documentation.md`.

Steps:
1. Find the next number: list `docs/adr/` (create the folder if missing). Take the highest existing
   `NNNN-*.md` and add 1, zero-padded to 4 digits; use `0001` if none exist.
2. Get today's date: `date +%F`.
3. Slugify the title (lowercase, non-alphanumeric → `-`). Write `docs/adr/<NNNN>-<slug>.md` with this
   template, filling **Context** and **Decision** from the title/your understanding of the task, and
   leaving the rest as prompts for the user to complete:

```
# ADR-<NNNN>: <title>

- **Status:** proposed
- **Date:** <today>
- **Deciders:** <who — fill in>

## Context
<the forces at play — constraints, requirements, the problem this decides>

## Decision
We will <the decision, active voice>.

## Alternatives considered
- <option> — <why rejected>

## Consequences
<what this makes easy, what it makes hard, the trade-offs accepted>
```

4. Report the file path and a one-line summary. ADRs are **append-only** — to change a past decision,
   write a new ADR that supersedes it (set the old one's status to `Superseded by ADR-NNNN`), don't
   rewrite history. `docs/adr/` is excluded from git locally: the ADR is a local record, never
   staged or pushed.
   Link this ADR from the current task doc's "Decisions & Notes" if one exists.
