# KASPER — project instructions

KASPER is one Claude Code session that delegates real work to discipline subagents; this repo is
its config and its only home. **The config itself is [.claude/CLAUDE.md](.claude/CLAUDE.md)** (the
index of rules, agents, commands and hooks, installed to `~/.claude/CLAUDE.md` by
[install.py](install.py)) — it is not repeated here, so it is not loaded twice.

This repo is public: its `docs/adr/` and `tasks/` stay local, never committed. The gate is
`ruff check . && mypy && pytest tests/unit` — **bare `mypy`**, because mypy's crawl skips
dot-directories and `pyproject.toml` lists `.claude/scripts` explicitly. Here KASPER's scripts run
as `python3 .claude/scripts/<x>.py` so the copy under development is the one exercised.
