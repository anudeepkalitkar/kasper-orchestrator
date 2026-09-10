---
description: Scaffold a new task document under tasks/.
argument-hint: <task title>
allowed-tools: Bash(python3 .claude/scripts/new_task.py:*)
---

Create a new task document for: $ARGUMENTS

Run:

```
python3 .claude/scripts/new_task.py "$ARGUMENTS"
```

Then open the created file and fill in the **Goal** and initial **Plan / Subtasks** based on the task. Keep the task doc current as the single source of truth for the task's state.
