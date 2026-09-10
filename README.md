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
the hook scripts in `.claude/scripts/` (all dispatched through `run_hook.py`). That, this
README, `CLAUDE.md`, and the decision record in `docs/adr/` are what this repo holds today.
This repo is the project's only home; further components land here as development continues.

## Use

```bash
git clone <repo> && cd kasper-orchestrator
```

Installing is a hand copy — there is no installer here yet. Copy the contents of `.claude/`
(`agents/`, `commands/`, `rules/`, `scripts/`, `skills/`, `sounds/`, `CLAUDE.md`,
`permissions-ledger.json`) into your Claude home, `~/.claude/`, and **merge**
`settings.json` into the one already there rather than replacing it — your own keys, the
model pin and theme among them, live in that file. After that the skill, the agents, the
rules, the commands and the hooks are live in **every** project: `settings.json` resolves
every hook through `python3 "$HOME/.claude/scripts/run_hook.py"`, so the home copy is what
runs. A project can still carry its own `.claude/` to override pieces — the dispatcher
prefers a project's own `.claude/scripts/<name>.py` and falls back to the home copy — but a
project copy alone, with nothing installed at home, runs no hooks at all. It needs nothing
but Python 3.12+ and a logged-in `claude`. The hook commands name `python3`; on Windows,
change them to `python`.

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
protected `master`. The Python package, its tests, and the tooling that runs the gate on
them are not here yet.
