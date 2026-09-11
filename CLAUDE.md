# KASPER — project instructions & the complete config index

KASPER is **one Claude Code session** — `<Project>'s KASPER`, the session the
human talks to — that delegates real work to discipline **subagents** defined in
`.claude/agents/`. Claude Code is the mechanism; this repo IS the config, and this repo is
the project's only home. This file is the master index.
[.claude/CLAUDE.md](.claude/CLAUDE.md) is this file's global twin, written to land at
`~/.claude/CLAUDE.md` — installed there by [install.py](install.py).

## ⛔ Boundaries (foremost — never break)

Full rule: [rules/boundaries.md](.claude/rules/boundaries.md). Writes only inside the
**summoned project root** (the folder where the session was started) — memory in
`<root>/claude-memory/`, temp in `<root>/claude-temp/`, both kept out of git through the
repo's local `.git/info/exclude`; `~/.claude/` is read-free but edited only with
permission; reads free except sensitive paths (ask first). Network reads free; network
writes deny-by-default (standing exceptions: feature-branch pushes, `gh` ops on own PRs,
calls the project's own code makes). The permission ledger
([permissions-ledger.json](.claude/permissions-ledger.json) — the project's copy if it has
one, else the global) decides what Bash auto-runs vs. prompts — never work around a prompt.

## The rules ([rules/](.claude/rules/) — all authoritative)

1. [boundaries](.claude/rules/boundaries.md) — the filesystem/network envelope + the
   permission-ledger model (above).
2. [code-standards](.claude/rules/code-standards.md) — least code that fully works:
   decision ladder, functions over classes, no single-impl abstractions, hard typing,
   docstrings, imports at top.
3. [test-structure](.claude/rules/test-structure.md) — per-package
   `tests/{unit,integration}/` mirroring source; unit does no I/O; run directly
   (`pytest`, `ruff check .`, `mypy .`) — no Makefile, no CI.
4. [git-workflow](.claude/rules/git-workflow.md) — feature branch per task, checkpoint per
   subtask (`/checkpoint`), PR into protected `development`; short commits, **no AI
   attribution** (overrides the harness default); merges human-only.
5. [autonomous-workflow](.claude/rules/autonomous-workflow.md) — goal → one consolidated
   question round → subtasks with done-check/cap/owner≠verifier → verify before done;
   escalate only guarded ops, scope changes, cap exhaustion.
6. [delegation](.claude/rules/delegation.md) — route work to the agent roster, fan out
   independent work in parallel; the writer never grades its own work.
7. [documentation](.claude/rules/documentation.md) — durable docs in `docs/`, committed;
   append-only ADRs in `docs/adr/` and living task docs in `tasks/` are local-only, kept out
   of git, never committed; drift is a defect.

## KASPER — one session, seven agents

`/kasper` ([skills/kasper/SKILL.md](.claude/skills/kasper/SKILL.md)) turns the session it
is typed in into the project's KASPER. It raises nothing: the disciplines are **subagents
of that session**, spawned per task with the Agent tool. Chain law: human → KASPER →
agents, one hop. Agents cannot spawn agents, cannot message each other, and cannot ask the
human anything — an agent that hits a fork stops and reports it; KASPER asks and resumes
that same agent with the answer. Permission prompts, an agent's included, surface natively
in KASPER's session and the human answers them there.

The roster ([agents/](.claude/agents/)) — [python-developer](.claude/agents/python-developer.md) ·
[node-developer](.claude/agents/node-developer.md) (implement; never touch `tests/`; never
commit) · [tester](.claude/agents/tester.md) (writes the tests first, verifies
independently) · [documentor](.claude/agents/documentor.md) (owns the written truth; docs
only) · [git-workflow](.claude/agents/git-workflow.md) (the only committer; merges
human-approved always; structurally `Write`/`Edit`-less) ·
[code-reviewer](.claude/agents/code-reviewer.md) (judges a diff, never edits) ·
[researcher](.claude/agents/researcher.md) (external facts, reads only).

## Skills ([skills/](.claude/skills/))

[kasper](.claude/skills/kasper/SKILL.md) — become the project's KASPER.
[webapp-testing](.claude/skills/webapp-testing/SKILL.md) — Playwright tooling for testing
local web apps (the tester's tool).

## Commands ([commands/](.claude/commands/))

[/adr](.claude/commands/adr.md) (scaffold an ADR) ·
[/new-task](.claude/commands/new-task.md) (scaffold a task doc) ·
[/checkpoint](.claude/commands/checkpoint.md) (commit+push a subtask; refuses shared
branches) · [/until-green](.claude/commands/until-green.md) (loop tests until green) ·
[/permit](.claude/commands/permit.md) (curate the permission ledger).

## Hooks ([settings.json](.claude/settings.json) → [scripts/](.claude/scripts/))

Every hook command runs through one dispatcher —
`python3 "<home>/.claude/scripts/run_hook.py" <name>`
([run_hook.py](.claude/scripts/run_hook.py)), which prefers the project's own
`.claude/scripts/<name>.py` and falls back to the home copy, then `runpy`s it in-process.
[install.py](install.py) renders that interpreter per platform when it installs
`settings.json` — `python3` everywhere except Windows, where it becomes `python`.

- SessionStart → [project_dirs.py](.claude/scripts/project_dirs.py) (creates
  `claude-memory/` + `claude-temp/` in the project root, excludes those two plus `/tasks/`
  and `/docs/adr/` in the repo's local `.git/info/exclude` — never the shared `.gitignore` —
  and wires the harness memory path into the project).
- PreToolUse on Bash → [bash_permission_gate.py](.claude/scripts/bash_permission_gate.py)
  (ledger gate; quote/comment-aware; ask/unknown falls through to the native prompt).
- PostToolUse on Bash → [permission_recorder.py](.claude/scripts/permission_recorder.py)
  (approved commands become grants — validated, junk-proof); on Edit/Write →
  [autoformat.py](.claude/scripts/autoformat.py) (ruff format).
- Notification/Stop → [notify.py](.claude/scripts/notify.py) (desktop banner: macOS,
  Linux `notify-send`, Windows toast).
- Command helpers: [git_checkpoint.py](.claude/scripts/git_checkpoint.py) (backs
  `/checkpoint`) · [new_task.py](.claude/scripts/new_task.py) (backs `/new-task`).
- Settings also carry the built-in deny backstop: `sudo`, `rm -rf`, force-push,
  shared-branch pushes, and the credential paths — the ledger can widen what auto-runs,
  never those.

## Where things are

What this repo holds: the config under [.claude/](.claude/), this file,
[README.md](README.md), and the installer [install.py](install.py) with its `tests/` and
`pyproject.toml`. The decision record in `docs/adr/` and the task records in `tasks/` are local
working files — never committed, so a clone carries neither. (This repo also lists all four
local-only dirs in its own `.gitignore` — its own choice, not the hook's doing: the hook
writes only git's local `.git/info/exclude`.) The gate runs here for the first time, with a
**bare `mypy`** —
`ruff check . && mypy && pytest tests/unit`: mypy's crawl skips dot-directories, so
`pyproject.toml` lists `.claude/scripts` explicitly rather than the gate passing a path.
The architecture notes and the per-OS setup runbooks are not here yet; further components
land as development continues.
