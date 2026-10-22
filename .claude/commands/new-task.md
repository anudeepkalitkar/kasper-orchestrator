---
description: Scaffold a new task document under tasks/ (local, never committed) and start its branch.
argument-hint: <task title>
allowed-tools: Write, Bash(python3 "$HOME/.claude/scripts/new_task.py":*)
---

Create a new task document for: $ARGUMENTS

Never put the title into shell syntax — not a quoted argument, not a heredoc; quotes,
backticks, `$( )` or a delimiter line in it would break out and run as shell. Instead:

1. Write the title above, verbatim, with the **Write** tool to `new-task-title.txt` in this session's
   scratch dir (`claude-temp/sessions/<session-id>/`, printed at session start).
2. Run:

```
python3 "$HOME/.claude/scripts/new_task.py" --branch --title-file claude-temp/sessions/<session-id>/new-task-title.txt
```

(In the KASPER source repo itself, run `.claude/scripts/new_task.py` instead, so the copy under
development is the one exercised.)

`--branch` starts the task on its own branch, `feat/<NNN>-<slug>`, and fills the doc's
**Branch:** line. It refuses — exit 1, the reason on stderr, no doc written — unless all three
hold:

- the current branch is the **mainline**: `development` if the repo has it, else the branch
  `origin/HEAD` names, else `master` or `main`, whichever exists;
- the working tree is clean (`git status --porcelain` is empty; excluded paths such as
  `tasks/` never show);
- after a `git fetch`, the mainline is not behind its upstream (a mainline with no upstream
  passes, with a note).

A branch of that name that already exists also makes it exit 1; nothing is overwritten. By day,
relay a refusal to the human; overnight, park the item with the reason (`dirty baseline` /
`mainline behind`) and end the night.

Then open the created file and fill in the **Goal** and initial **Plan / Subtasks** based on the task. Keep the task doc current as the single source of truth for the task's state — including its **Rounds:** line (the verify loop's re-entries, cap 3). `tasks/` is excluded from git locally — the doc stays local and is never committed.
