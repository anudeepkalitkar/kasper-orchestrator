# KASPER

**K**alitkar **A**utonomous **S**ystem for **P**rogramming, **E**ngineering & **R**easoning.

One Claude Code session that runs your project: you talk to it, it delegates the real work
to discipline **subagents** — developer, tester, documentor, git-workflow, code-reviewer,
researcher — and keeps the task doc while they work. Claude Code is the mechanism; KASPER
is a skill, a set of agent definitions, a few hook scripts, and an installer. It runs on
macOS, Linux and Windows from one clone.

What KASPER *is* lives under `.claude/`: the `/kasper` skill in `.claude/skills/kasper/`,
the agent definitions in `.claude/agents/` (one file per discipline — edit one and the
next delegation uses it; its frontmatter pins that agent's model and tools), the standing
rules in `.claude/rules/`, the commands in `.claude/commands/`, the permission ledger, and
the hook scripts in `.claude/scripts/` (all dispatched through `run_hook.py`). The code
beside it is small: root `install.py` plus `kasper/install_config.py` — the settings
renderer, and the whole Python package. Full design: `docs/ARCHITECTURE.md`; the decision
trail, including the machinery that was built and deliberately deleted: `docs/adr/`.

## Use

```bash
git clone <repo> && cd kasper-orchestrator
python3 install.py               # once per machine (`python install.py` on Windows)
```

That is the install: it needs nothing but Python 3.12+ and a logged-in `claude`. It
merge-copies this repo's `.claude/` into your Claude home and renders `settings.json` for
your platform. Nothing you already have is overwritten — a file of yours that differs is
kept, and the new version is written beside it as `<name>.kasper-new`. Because the config
lands in your Claude home, the skill and the agents are available in **every** project;
there is no per-project install step. Prereqs and per-OS details:
`docs/deploy/fresh-mac.md`, `fresh-linux.md`, `fresh-windows.md`.

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

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # once, for the gate's tools
ruff check . && mypy . && pytest tests/unit    # green before any merge
pytest tests/integration                       # the heavier tier, when touched
```

`feat/*` branches, checkpoint per subtask, PR to protected `development`.
