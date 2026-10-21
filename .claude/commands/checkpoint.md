---
description: Commit and push the current subtask (refuses on shared branches development/main/master).
argument-hint: <commit message>
allowed-tools: Write, Bash(python3 .claude/scripts/git_checkpoint.py:*)
---

Checkpoint the current subtask to git with the message: $ARGUMENTS

Never put the message into shell syntax — not a quoted argument, not a heredoc; quotes,
backticks, `$( )` or a delimiter line in it would break out and run as shell. Instead:

1. Write the message above, verbatim, with the **Write** tool to `checkpoint-message.txt` in this session's
   scratch dir (`claude-temp/sessions/<session-id>/`, printed at session start).
2. Run:

```
python3 .claude/scripts/git_checkpoint.py --message-file claude-temp/sessions/<session-id>/checkpoint-message.txt
```

This stages tracked changes plus untracked files, prints what it staged, commits, and pushes the current feature branch. It refuses — naming the paths — when anything pending is under `tasks/`, `claude-temp/`, `claude-memory/` or `docs/adr/`, or is a `.env`/`.env.*` file; it refuses on a detached HEAD; and it **refuses on a shared branch** (`development`/`main`/`master`, plus legacy `qa`) per `.claude/rules/git-workflow.md` — in that case branch off `development` first (`git switch -c feat/<task-slug> development`) and retry. After a successful checkpoint, update the task doc by hand — tick the completed subtask and record the commit.
