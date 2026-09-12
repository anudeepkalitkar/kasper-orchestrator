---
description: Commit and push the current subtask (refuses on shared branches development/main/master).
argument-hint: <commit message>
allowed-tools: Bash(python3 .claude/scripts/git_checkpoint.py:*)
---

Checkpoint the current subtask to git with the message: $ARGUMENTS

Run:

```
python3 .claude/scripts/git_checkpoint.py "$ARGUMENTS"
```

This stages all changes, commits, and pushes the current feature branch. It **refuses on a shared branch** (`development`/`main`/`master`, plus legacy `qa`) per `.claude/rules/git-workflow.md` — in that case branch off `development` first (`git switch -c feat/<task-slug> development`) and retry. After a successful checkpoint, update the task doc by hand — tick the completed subtask and record the commit.
