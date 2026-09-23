# Global Claude Code config

This is the global configuration for every project on this machine: the rules, agents,
skills, commands, hooks, and permission ledger that govern all Claude Code sessions, with
or without KASPER invoked. Inside a KASPER session the `/kasper` skill outranks this file
where they conflict. The config is versioned in the kasper-orchestrator repo — edit it
there, then run `python3 install.py` from that clone (`python install.py` on Windows) to
install or update this home; `python3 install.py --status` reports whether this home still
matches what was installed.

## ⛔ Boundaries (foremost — never break)

Full rule: [rules/boundaries.md](rules/boundaries.md). Writes only inside the
**summoned project root** (the folder where the session was started) — memory in
`<root>/claude-memory/`, temp in `<root>/claude-temp/` (it accumulates — nothing sweeps it;
the human clears it), both kept out of git through the repo's local `.git/info/exclude` — which
also carries `tasks/` everywhere, and `docs/adr/` unless the repo is private;
`~/.claude/` is read-free but edited only with permission; reads free except sensitive
paths (ask first). Network reads free; network writes deny-by-default (standing exceptions:
feature-branch pushes, `gh` ops on own PRs, calls the project's own code makes). The
permission ledger ([permissions-ledger.json](permissions-ledger.json) — the project's
copy if it has one, else the global) decides what Bash auto-runs vs. prompts — never work
around a prompt.

## The rules ([rules/](rules/) — all authoritative)

1. [boundaries](rules/boundaries.md) — the filesystem/network envelope + the
   permission-ledger model (above).
2. [code-standards](rules/code-standards.md) — least code that fully works:
   decision ladder, functions over classes, no single-impl abstractions, hard typing,
   docstrings, imports at top.
3. [test-structure](rules/test-structure.md) — per-package
   `tests/{unit,integration}/` mirroring source; unit does no I/O; run directly
   (`pytest`, `ruff check .`, `mypy .` — bare `mypy` where `pyproject.toml` names
   the files) — no Makefile, no test/lint CI (ship workflows allowed).
4. [git-workflow](rules/git-workflow.md) — feature branch per task, checkpoint per
   subtask (`/checkpoint`), PR into protected `development`; short commits, **no AI
   attribution** (overrides the harness default); merges human-only.
5. [autonomous-workflow](rules/autonomous-workflow.md) — goal → one consolidated
   question round → subtasks with done-check/cap/owner≠verifier → verify before done;
   escalate only guarded ops, scope changes, cap exhaustion.
6. [delegation](rules/delegation.md) — route work to the agent roster, fan out
   independent work in parallel; the writer never grades its own work.
7. [documentation](rules/documentation.md) — durable docs in `docs/`, committed;
   living task docs in `tasks/` are local-only, never committed; append-only ADRs in
   `docs/adr/` are committed in a private repo, local-only in a public one; drift is a defect.
8. [model-routing](rules/model-routing.md) — the model tier is scored from each
   brief at spawn time and passed as the Agent tool's `model`; agent files pin no model,
   and a spawn that passes none inherits the session's own — the most expensive.

## KASPER — one session, eight agents and three Codex seats

`/kasper` ([skills/kasper/SKILL.md](skills/kasper/SKILL.md)) turns the session it
is typed in into the project's KASPER. It raises nothing: the eight Claude disciplines are
**subagents of that session**, spawned per task with the Agent tool — each at the model tier
`model_route.py` scores from its brief, since no agent file pins one; the three verification
seats are Bash calls into the OpenAI Codex CLI, not agents. Chain law: human → KASPER →
agents, one hop. Agents cannot spawn agents, cannot message each other, and cannot ask the
human anything — an agent that hits a fork stops and reports it; KASPER asks and resumes
that same agent with the answer. Permission prompts, an agent's included, surface natively
in KASPER's session and the human answers them there.

The roster ([agents/](agents/)) — four developers, each of which never touches
`tests/` and never commits, plus four supporting disciplines:
[python-developer](agents/python-developer.md) (Python, carrying FastAPI,
SQLAlchemy 2 + Alembic, Celery, PyTorch/OCR and boto3 as sections) ·
[typescript-developer](agents/typescript-developer.md) (TypeScript — Node
servers, React 19 + Vite, Next 16) ·
[terraform-developer](agents/terraform-developer.md) (HCL, modules, tftest, AWS —
plans and reports, never applies) ·
[devops-developer](agents/devops-developer.md) (Dockerfiles, compose, GitHub
Actions, shell — builds locally, never pushes an image or deploys) ·
[architect](agents/architect.md) (turns a goal into a design spec in
`docs/design/` and a proposed ADR; docs only, never code or tests) ·
[documentor](agents/documentor.md) (owns the written truth; docs
only) · [git-workflow](agents/git-workflow.md) (the only committer; merges
human-approved always; structurally `Write`/`Edit`-less) ·
[researcher](agents/researcher.md) (external facts, reads only).

Verification is not a subagent. The **tester**, **code-reviewer**, and **arch-reviewer** are
seats on the OpenAI Codex CLI, run headless by one ledger-gated Bash call —
`python3 .claude/scripts/codex_seat.py tester|reviewer|arch-reviewer --brief <file> --out <file>
[--stack <name>...]` ([codex_seat.py](scripts/codex_seat.py)) — whose prompt is the seat's
role file in [codex/](codex/), then one stack section from [codex/stacks/](codex/stacks/)
per `--stack` the call names — which is what makes a seat language-aware — then the brief
KASPER wrote. **One verify pass per task, not per subtask:**
the developer implements the task, the tester seat authors the tests and runs the
gate, the reviewer seat judges the diff; the arch-reviewer seat judges a design spec or a proposed
ADR before any of it is built. The tester's report opens `GATE: green` or
`GATE: red`; the script exits 0 green · 1 red · 2 bad arguments · 3 Codex not signed in (the
human runs `codex login`) · 4 timeout · 5 no verdict. Owner ≠ verifier now also means
vendor ≠ vendor.

## Skills ([skills/](skills/))

[kasper](skills/kasper/SKILL.md) — become the project's KASPER.
[webapp-testing](skills/webapp-testing/SKILL.md) — Playwright tooling for testing
local web apps.

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
  `claude-memory/` + `claude-temp/` in the project root, excludes those two plus `/tasks/`
  in the repo's local `.git/info/exclude` — never the shared `.gitignore` — and `/docs/adr/`
  too unless `gh` reports the repo `PRIVATE`, printing `ADRs: tracked (private repo)` or
  `ADRs: local-only`; wires the harness memory path into the project, and creates
  `claude-temp/sessions/<id>/`, printing that scratch path into context).
- PreToolUse on Bash → [bash_permission_gate.py](scripts/bash_permission_gate.py)
  (ledger gate; quote/comment-aware; ask/unknown falls through to the native prompt).
- PostToolUse on Bash → [permission_recorder.py](scripts/permission_recorder.py)
  (approved commands become grants — validated, junk-proof); on Edit/Write →
  [autoformat.py](scripts/autoformat.py) (ruff format).
- Notification/Stop → [notify.py](scripts/notify.py) (a native system sound per
  event — macOS Glass/Funk, the Windows and freedesktop equivalents — with the bundled
  `notify.wav` as the fallback).
- Command helpers: [git_checkpoint.py](scripts/git_checkpoint.py) (backs
  `/checkpoint`) · [new_task.py](scripts/new_task.py) (backs `/new-task`). Beside them,
  [codex_seat.py](scripts/codex_seat.py) runs a Codex verification seat and
  [model_route.py](scripts/model_route.py) picks the model tier for one spawn (it backs
  the routing step) — KASPER calls both directly; neither is a hook.
- Settings also carry the built-in deny backstop: `sudo`, `rm -rf`, force-push,
  shared-branch pushes, and the credential paths — the ledger can widen what auto-runs,
  never those.

## Where things are

Everything above lives beside this file, under `~/.claude/` — the Codex seat prompts in
`codex/` among them. The source of truth is the
kasper-orchestrator repo — the project's only home; that repo is public, so its decision record
lives in the clone's local, git-excluded `docs/adr/`, never in the repo itself — in a private repo
the hook leaves `docs/adr/` tracked and the ADRs are committed with the code. Change the config there
and run its `install.py` to bring this home up to date; never hand-edit this home copy and
expect it to survive — an install replaces the files it owns (backing up what it overwrites)
— and never let a session rewrite it silently.
