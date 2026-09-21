# KASPER

**K**alitkar **A**utonomous **S**ystem for **P**rogramming, **E**ngineering & **R**easoning.

One Claude Code session that runs your project: you talk to it, it delegates the real work
to discipline **subagents** — developer, documentor, git-workflow, researcher — and, once per
task, to two verification seats that run on the OpenAI Codex CLI — tester and code-reviewer —
and keeps the task doc while they work. Claude Code is the mechanism; KASPER is a skill, a set
of agent definitions, standing rules, and a few hook scripts.

What KASPER *is* lives under `.claude/`: the `/kasper` skill in `.claude/skills/kasper/`,
the agent definitions in `.claude/agents/` (one file per discipline — edit one and the
next delegation uses it; its frontmatter pins that agent's model and tools), the Codex seat
prompts in `.claude/codex/`, the standing
rules in `.claude/rules/`, the commands in `.claude/commands/`, the permission ledger, and
the hook scripts in `.claude/scripts/` (all dispatched through `run_hook.py`). That, the
installer (`install.py`) with its `tests/` and `pyproject.toml`, this README and `CLAUDE.md`
are what this repo holds today. There is no `docs/adr/` or `tasks/` here: under KASPER task docs
are local working files in every repo, and so are decision records in a public repo like this one
— the SessionStart hook keeps them out of git through each repo's own `.git/info/exclude`, never a
shared `.gitignore` — so they stay on the machine that wrote them and out of everyone else's
checkout. In a private repo the hook leaves `docs/adr/` out of that list and the ADRs are
committed with the code. This repo is the project's only home; further components land here as
development continues.

## Use

```bash
git clone <repo> && cd kasper-orchestrator
python3 install.py                  # Windows: python install.py
```

The installer copies KASPER's own files — `agents/`, `codex/`, `commands/`, `rules/`,
`scripts/`, `skills/`, `sounds/`, `CLAUDE.md` — out of `.claude/` into your Claude home, `~/.claude/`,
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
`install.py` says so and exits 1. The verification seats additionally need the OpenAI Codex
CLI (`npm install -g @openai/codex`, then `codex login`); without it the seat stops with exit
code 3 and tells you to sign in. On Windows, Git Bash must be installed: Claude Code runs
the hook commands through it.

On native Windows the seats need one more step: Codex runs them under a restricted sandbox
policy, and it can only honour one when its config declares a native sandbox backend — a
version upgrade alone does not supply it. With none declared, `codex doctor` reports the
sandbox backend disabled, and a headless seat, whose approvals are set to never so it cannot
ask, is rejected with a sandbox error before any verdict. Declare the native sandbox in the
Codex config until `codex doctor` shows the backend enabled — unelevated mode works, elevated
is stronger — and re-check that after every `winget upgrade OpenAI.Codex`, which overwrites
the folder Codex is installed into.

Then, in any project:

```bash
cd <project> && claude       # then type /kasper
```

That session is now `<Project>'s KASPER`. Ask it for anything: it answers questions
directly and delegates real work, one task per agent, reporting back tersely — a short
digest, with the full evidence written under the session's own scratch dir —
`claude-temp/sessions/<session-id>/`, created by the SessionStart hook and named in the
session's context, reports landing in its `reports/`. Permission prompts — including
ones raised inside an agent — surface right there for you to answer; the ledger decides
what never needs asking. The one that writes code is never the one that verifies it —
verification is a single pass per task, run on Codex — only `git-workflow` commits, and **merges always wait for your explicit yes**. Tell
KASPER to end when you're done: it writes the session's memory and stops. Nothing is
cleaned up for you — `claude-temp/` accumulates across sessions, and clearing it is
yours to do whenever you like.

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
