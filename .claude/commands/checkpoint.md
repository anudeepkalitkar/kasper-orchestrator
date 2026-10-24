---
description: Commit and push the current subtask (refuses on shared branches — development/main/master/qa and the resolved mainline).
argument-hint: <commit message>
allowed-tools: Write, Bash(python3 "$HOME/.claude/scripts/git_checkpoint.py":*)
---

Checkpoint the current subtask to git with the message: $ARGUMENTS

Never put the message into shell syntax — not a quoted argument, not a heredoc; quotes,
backticks, `$( )` or a delimiter line in it would break out and run as shell. Instead:

1. Write the message above, verbatim, with the **Write** tool to `checkpoint-message.txt` in this session's
   scratch dir (`claude-temp/sessions/<session-id>/`, printed at session start).
2. Run:

```
python3 "$HOME/.claude/scripts/git_checkpoint.py" --message-file claude-temp/sessions/<session-id>/checkpoint-message.txt
```

(In the KASPER source repo itself, run `.claude/scripts/git_checkpoint.py` instead, so the copy
under development is the one exercised.)

This stages tracked changes plus untracked files, prints what it staged, commits, and pushes the current feature branch. It refuses — naming the paths — when anything pending is under `tasks/`, `claude-temp/` or `claude-memory/`, or is a `.env`/`.env.*` file; it refuses on a detached HEAD; and it **refuses on a shared branch** (`development`/`main`/`master`, plus legacy `qa`, plus the repo's mainline under whatever name — `development` if the repo has it, else the branch `origin/HEAD` names, else `master` or `main`) per `.claude/rules/git-workflow.md` — in that case branch off the mainline first (`git switch -c feat/<task-slug> <mainline>`) and retry. When the mainline cannot be resolved, the four names are still refused.

**`docs/adr/`** is staged exactly when the repo's `.git/info/exclude` lacks `/docs/adr/` — the
same exclude file the SessionStart hook writes (a private repo commits its ADRs; a public or
unknown one keeps them local). When the exclude file lists it, or the repo cannot be read,
pending ADRs are refused like any other local-only path.

**`--only <path>...`** commits exactly those repo-relative paths and nothing else, by refusal:

```
python3 "$HOME/.claude/scripts/git_checkpoint.py" --message-file claude-temp/sessions/<session-id>/checkpoint-message.txt --only <path> <path>
```

Put `--only` last and pass the message only via `--message-file` (or stdin): `--only` takes every
following operand as a path, so each must exist in the worktree or be tracked by git (a deleted
tracked file is fine) — an operand that is neither, such as a trailing message, is refused, named,
before anything is staged.

It also refuses before committing when a listed path is forbidden, when the index already holds a
path outside the list (unstage it first — it names it), or when the list stages nothing or
something outside itself. Every other changed file stays unstaged and uncommitted. Use it to
integrate one node's declared files.

After a successful checkpoint, update the task doc by hand — tick the completed subtask and record the commit.
