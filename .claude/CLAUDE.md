# Global Claude Code config

This is the global configuration for every project on this machine: the rules, agents,
skills, commands, hooks, and permission ledger that govern all Claude Code sessions, with
or without KASPER invoked. Inside a KASPER session the `/kasper` skill outranks this file
where they conflict. The config is versioned in the kasper-orchestrator repo — edit it
there, then copy the changed files into this home to bring it up to date. There is no
installer in that repo yet, so the copy is done by hand.

## ⛔ Boundaries (foremost — never break)

Full rule: [rules/boundaries.md](rules/boundaries.md). Writes only inside the
**summoned project root** (the folder where the session was started) — memory in
`<root>/claude-memory/`, temp in `<root>/claude-temp/`, both gitignored; `~/.claude/` is
read-free but edited only with permission; reads free except sensitive paths (ask first).
Network reads free; network writes deny-by-default (standing exceptions: feature-branch
pushes, `gh` ops on own PRs, calls the project's own code makes). The permission ledger
([permissions-ledger.json](permissions-ledger.json) — the project's copy if it has
one, else the global) decides what Bash auto-runs vs. prompts — never work around a prompt.

## The rules ([rules/](rules/) — all authoritative)

1. [boundaries](rules/boundaries.md) — the filesystem/network envelope + the
   permission-ledger model (above).
2. [code-standards](rules/code-standards.md) — least code that fully works:
   decision ladder, functions over classes, no single-impl abstractions, hard typing,
   docstrings, imports at top.
3. [test-structure](rules/test-structure.md) — per-package
   `tests/{unit,integration}/` mirroring source; unit does no I/O; run directly
   (`pytest`, `ruff check .`, `mypy .`) — no Makefile, no CI.
4. [git-workflow](rules/git-workflow.md) — feature branch per task, checkpoint per
   subtask (`/checkpoint`), PR into protected `development`; short commits, **no AI
   attribution** (overrides the harness default); merges human-only.
5. [autonomous-workflow](rules/autonomous-workflow.md) — goal → one consolidated
   question round → subtasks with done-check/cap/owner≠verifier → verify before done;
   escalate only guarded ops, scope changes, cap exhaustion.
6. [delegation](rules/delegation.md) — route work to the agent roster, fan out
   independent work in parallel; the writer never grades its own work.
7. [documentation](rules/documentation.md) — durable docs + append-only ADRs in
   `docs/`, living task docs in `tasks/`; drift is a defect.

## KASPER — one session, seven agents

`/kasper` ([skills/kasper/SKILL.md](skills/kasper/SKILL.md)) turns the session it
is typed in into the project's KASPER. It raises nothing: the disciplines are **subagents
of that session**, spawned per task with the Agent tool. Chain law: human → KASPER →
agents, one hop. Agents cannot spawn agents, cannot message each other, and cannot ask the
human anything — an agent that hits a fork stops and reports it; KASPER asks and resumes
that same agent with the answer. Permission prompts, an agent's included, surface natively
in KASPER's session and the human answers them there.

The roster ([agents/](agents/)) — [python-developer](agents/python-developer.md) ·
[node-developer](agents/node-developer.md) (implement; never touch `tests/`; never
commit) · [tester](agents/tester.md) (writes the tests first, verifies
independently) · [documentor](agents/documentor.md) (owns the written truth; docs
only) · [git-workflow](agents/git-workflow.md) (the only committer; merges
human-approved always; structurally `Write`/`Edit`-less) ·
[code-reviewer](agents/code-reviewer.md) (judges a diff, never edits) ·
[researcher](agents/researcher.md) (external facts, reads only).

## Skills ([skills/](skills/))

[kasper](skills/kasper/SKILL.md) — become the project's KASPER.
[webapp-testing](skills/webapp-testing/SKILL.md) — Playwright tooling for testing
local web apps (the tester's tool).

## Commands ([commands/](commands/))

[/adr](commands/adr.md) (scaffold an ADR) ·
[/new-task](commands/new-task.md) (scaffold a task doc) ·
[/checkpoint](commands/checkpoint.md) (commit+push a subtask; refuses shared
branches) · [/until-green](commands/until-green.md) (loop tests until green) ·
[/permit](commands/permit.md) (curate the permission ledger).

## Hooks ([settings.json](settings.json) → [scripts/](scripts/))

Every hook command runs through one dispatcher —
`python3 "<home>/.claude/scripts/run_hook.py" <name>`
([run_hook.py](scripts/run_hook.py)), which prefers the project's own
`.claude/scripts/<name>.py` and falls back to the home copy, then `runpy`s it in-process.

- SessionStart → [project_dirs.py](scripts/project_dirs.py) (creates
  `claude-memory/` + `claude-temp/` in the project root, gitignores both, and wires the
  harness memory path into the project).
- PreToolUse on Bash → [bash_permission_gate.py](scripts/bash_permission_gate.py)
  (ledger gate; quote/comment-aware; ask/unknown falls through to the native prompt).
- PostToolUse on Bash → [permission_recorder.py](scripts/permission_recorder.py)
  (approved commands become grants — validated, junk-proof); on Edit/Write →
  [autoformat.py](scripts/autoformat.py) (ruff format).
- Notification/Stop → [notify.py](scripts/notify.py) (desktop banner: macOS,
  Linux `notify-send`, Windows toast).
- Command helpers: [git_checkpoint.py](scripts/git_checkpoint.py) (backs
  `/checkpoint`) · [new_task.py](scripts/new_task.py) (backs `/new-task`).
- Settings also carry the built-in deny backstop: `sudo`, `rm -rf`, force-push,
  shared-branch pushes, and the credential paths — the ledger can widen what auto-runs,
  never those.

## Where things are

Everything above lives beside this file, under `~/.claude/`. The source of truth is the
kasper-orchestrator repo — the project's only home — whose decision record is in
`docs/adr/`. Change the config there and copy it into this home; never hand-edit this home
copy and expect it to survive, and never let a session rewrite it silently.
