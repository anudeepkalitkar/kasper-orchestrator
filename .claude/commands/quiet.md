---
description: Turn quiet mode on or off — KASPER messages the human only at milestones.
argument-hint: on | off
allowed-tools: Read, Edit, Write
---

Quiet mode: $ARGUMENTS

The switch is the first line of `tasks/STATUS.md` (local-only, never committed), which KASPER
reads at `/kasper` time and rewrites in its bookkeeping call:

```
quiet: on                                       # or off; the only two values
- 026-quiet-mode · step 2 of 4 · round 1/3 · last: GATE green · 2026-10-24T14:05
```

Edit the `quiet:` line in place. File absent: create it with only that line. File present but
no `quiet:` header: prepend the line, keep every other line. Leave the task lines alone. Confirm in one line, then stop. Inside KASPER it takes effect at once;
outside KASPER it is stored and read at the next `/kasper`.
