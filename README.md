# KASPER

**K**alitkar **A**utonomous **S**ystem for **P**rogramming, **E**ngineering & **R**easoning.

One Claude Code session that runs your project: you talk to it, it delegates the real work
to discipline **subagents** — developer, tester, documentor, git-workflow, code-reviewer,
researcher — and keeps the task doc while they work. Claude Code is the mechanism; KASPER
is a skill, a set of agent definitions, standing rules, and a few hook scripts.

What KASPER *is* lives under `.claude/`: the `/kasper` skill in `.claude/skills/kasper/`,
the agent definitions in `.claude/agents/` (one file per discipline — edit one and the
next delegation uses it; its frontmatter pins that agent's model and tools), the standing
rules in `.claude/rules/`, the commands in `.claude/commands/`, the permission ledger, and
the hook scripts in `.claude/scripts/` (all dispatched through `run_hook.py`). That, the
installer (`install.py`) with its `tests/` and `pyproject.toml`, this README, `CLAUDE.md`,
the task records in `tasks/`, and the decision record in `docs/adr/` are what this repo
holds today. This repo is the project's only home; further components land here as
development continues.

## Use

```bash
git clone <repo> && cd kasper-orchestrator
python3 install.py                  # Windows: python install.py
```

The installer copies KASPER's own files — `agents/`, `commands/`, `rules/`, `scripts/`,
`skills/`, `sounds/`, `CLAUDE.md` — out of `.claude/` into your Claude home, `~/.claude/`,
and **merges** `settings.json` and `permissions-ledger.json` into whatever is already
there, so your own keys (the model pin, the theme, your own hooks) and your own allow keys,
denials and machine-specific grants survive untouched. Anything it would overwrite is
copied first into `~/.claude/kasper-backup-<timestamp>/`, and everything it wrote is
recorded in `~/.claude/kasper-manifest.json`.

- `--dry-run` — print the plan (create / overwrite / unchanged / merge / remove, plus every
  backup it would take) and write nothing at all.
- `--status` — check the home against the manifest: exit **0** when every installed file
  still matches and `settings.json` still carries the hooks, **1** on any drift or when
  nothing is installed.
- `--uninstall` — delete the installed files that are still untouched, keep and report any
  you edited, and strip KASPER's hooks from `settings.json`; the permissions block and the
  ledger stay behind, the ledger because your grants live in it.
- `--home PATH` — act on a Claude home other than `~/.claude`.
- `--python NAME` — the interpreter rendered into the hook commands (default: `python` on
  Windows, `python3` elsewhere).

**Re-running `python3 install.py` is the update path**: changed files are re-copied, the two
merged files re-merged, and files this repo no longer ships are removed — each backed up
first.

After that the skill, the agents, the rules, the commands and the hooks are live in **every**
project: `settings.json` resolves every hook through
`python3 "$HOME/.claude/scripts/run_hook.py"` (rendered with `python` on Windows), so the
home copy is what runs. A project can still carry its own `.claude/` to override pieces —
the dispatcher prefers a project's own `.claude/scripts/<name>.py` and falls back to the
home copy — but a project copy alone, with nothing installed at home, runs no hooks at all.

It needs nothing but **Python 3.12+** and a logged-in `claude` — on an older interpreter
`install.py` says so and exits 1. On Windows, Git Bash must be installed: Claude Code runs
the hook commands through it.

Then, in any project:

```bash
cd <project> && claude       # then type /kasper
```

That session is now `<Project>'s KASPER`. Ask it for anything: it answers questions
directly and delegates real work, one task per agent, reporting back tersely — a short
digest, with the full evidence written to `claude-temp/reports/<task>-<agent>.md`.
Permission prompts — including ones raised inside an agent — surface right there for you
to answer; the ledger decides what never needs asking. The agent that writes code is never
the one that verifies it, only `git-workflow` commits, and **merges always wait for your
explicit yes**. Tell KASPER to end when you're done: it writes the session's memory and
stops.

## Develop (this repo)

This repo is where KASPER is developed. `feat/*` branches, checkpoint per subtask, PR to
protected `master`. The gate here is `ruff check . && mypy && pytest tests/unit` — bare
`mypy`, because mypy's directory crawl skips dot-directories, so `pyproject.toml` names
`.claude/scripts` explicitly in its `files` list instead. The tooling lives in a gitignored
`.venv`:

```bash
python3 -m venv .venv && .venv/bin/pip install ruff mypy pytest         # macOS/Linux
.venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest tests/unit
```

```powershell
py -m venv .venv                                                        # Windows
.venv\Scripts\pip install ruff mypy pytest
.venv\Scripts\ruff check .
.venv\Scripts\mypy
.venv\Scripts\pytest tests\unit
```

(venv puts the tools in `bin/` on POSIX and `Scripts\` on Windows; Windows PowerShell 5.1
has no `&&`, so the gate runs a line at a time there — every line must exit 0.)

`pytest tests/integration` runs the disk tier — real installs into temporary homes through
`--home`.
