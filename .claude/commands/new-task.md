---
description: Scaffold a new task document under tasks/ (local, never committed).
argument-hint: <task title>
allowed-tools: Write, Bash(python3 .claude/scripts/new_task.py:*)
---

Create a new task document for: $ARGUMENTS

Never put the title into shell syntax — not a quoted argument, not a heredoc; quotes,
backticks, `$( )` or a delimiter line in it would break out and run as shell. Instead:

1. Write the title above, verbatim, with the **Write** tool to `new-task-title.txt` in this session's
   scratch dir (`claude-temp/sessions/<session-id>/`, printed at session start).
2. Run:

```
python3 .claude/scripts/new_task.py --title-file claude-temp/sessions/<session-id>/new-task-title.txt
```

Then open the created file and fill in the **Goal** and initial **Plan / Subtasks** based on the task. Keep the task doc current as the single source of truth for the task's state. `tasks/` is excluded from git locally — the doc stays local and is never committed.
