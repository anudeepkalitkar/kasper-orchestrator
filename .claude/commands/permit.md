---
description: Curate the permission ledger — add allows, record denials with reasoning, list entries, review the unknowns log.
argument-hint: <command-or-key> [note] | --deny <pattern-or-command> <note> | --list | --review
---

# /permit — curate the permission ledger

Manage `.claude/permissions-ledger.json` (see rule `boundaries.md`). Parse `$ARGUMENTS`:

## `/permit <command-or-key> [note]`
Add a standing allow. Derive the right shape: a plain head (`open`), a scoped prefix
(`aws ecs describe-services`), or — if the request only makes sense as a pattern (contains
"push", branch names, flags that matter) — an `allow_patterns` regex. Write it into `allow_keys`
(with the note) or `allow_patterns`. If it duplicates or is subsumed by an existing entry, say so
and change nothing. If it would weaken an `ask_patterns` guard, warn what the guard protects and
ask the user to confirm once before removing/narrowing that guard.

## `/permit --deny <pattern-or-command> <note>`
Add a `denials` entry: a regex (escape a literal command), the user's reasoning as the note, and
today's date. This forces a prompt (with that note shown) whenever a future command matches.

## `/permit --list`
Show the ledger grouped: seeded allows (count + notable), auto-recorded grants (key, example,
date), guards/denials (pattern + note). Keep it scannable — a table per group.

## `/permit --review`
Triage `.claude/permission-unknowns.log`: aggregate by frequency, propose promotions for the
recurring safe ones (say which key/pattern each would become) and flag anything that should stay
prompting. Apply what the user approves, then truncate the log.

## Always
- Edit the JSON directly (it's small); keep it valid — atomic in intent, one coherent change.
- After any edit, run a quick sanity check:
  `echo '{"tool_name":"Bash","tool_input":{"command":"<representative command>"}}' | python3 .claude/scripts/bash_permission_gate.py`
  and confirm the decision matches the user's intent before reporting done.
- Never remove or weaken an `ask_patterns` guard without the user explicitly confirming that exact guard.
