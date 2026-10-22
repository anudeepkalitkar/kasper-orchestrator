---
description: Turn overnight mode on or off — walk the open backlog unattended, then leave a morning report.
argument-hint: on [until <YYYY-MM-DDTHH:MM>] | off
allowed-tools: Read, Edit, Write
---

Overnight mode: $ARGUMENTS

The switch is the header of `tasks/BACKLOG.md` (local-only, never committed), which the
`overnight` Stop hook (`$HOME/.claude/scripts/overnight.py`) reads after every turn:

```
overnight: on                                   # or off; the only two values
session: <the session id that turned it on>
until: 2026-10-23T07:00                         # optional, local time, ISO 8601
- [ ] 021-some-task — <goal in one line>        # open
- [x] 020-other-task — done 2026-10-22, PR #21  # done
- [~] 019-third-task — parked: <reason> on <branch>, <WIP commit or PR>
```

Only those header lines and the `- [ ]` / `- [x]` / `- [~]` item lines mean anything to the
hook; the rest of the file is free text. Edit the header lines in place (add them at the top
when absent); never touch the items' wording except to change their state.

**`on`** (optionally with `until <time>`):

1. Read `tasks/BACKLOG.md`. If it has no `- [ ]` item, or an open item has no one-line goal,
   say so and stop — overnight needs work it can start without asking.
2. Set `overnight: on`, `session: <this session's id>` — the id in the session scratch path
   printed at session start (`claude-temp/sessions/<id>/`) — and, if the human gave one,
   `until: <time>`; remove a stale `until:` otherwise.
3. Remind the human: landing stays theirs. Every finished item is parked with its PR; the
   morning report is the merge list. A permission prompt still waits for them.
4. Stop. The next Stop starts the walk.

**`off`** runs **"end the night"** — the one ending procedure, never `off` without the report:

1. **First** append `tasks/morning-<YYYY-MM-DD>.md` (today's date; append, never overwrite)
   from the backlog's current state, even when no item was touched — then the report says so
   and why. Sections: one line per item touched (done / parked / blocked, branch, WIP commit or
   PR, rounds used, verify outcome); "decisions you need to make" (every parked question
   verbatim); "PRs waiting for your merge"; "parked, resume with" (branch + commit).
2. **Then** set `overnight: off`.

The same procedure runs when the hook says `Overnight ended (…)`, when Codex is unusable (seat
exit 3), when a task cannot start (dirty or stale mainline) or a parking checkpoint is refused,
when KASPER is ended, and as the first act of any new session that starts under a header still
`on` for another session (session start prints `Overnight: on (session …)`).
