---
description: Loop — run the project gate, fix root causes, checkpoint — until green or the cap is hit.
argument-hint: [extra done-check command] [--cap N]
---

Run the **until-green inner loop** on the current feature branch (per `.claude/rules/autonomous-workflow.md`):

$ARGUMENTS

1. **Resolve the done-check.** The project gate — first found wins: `make gate` → `npm run gate` →
   `scripts/gate.sh` → stack default (Python: `ruff check . && mypy . && pytest tests/unit`, with
   **bare `mypy`** where `pyproject.toml` names the files to check — a path argument overrides that
   `files` list; Node: `npx eslint . && npx tsc --noEmit && npm test` — recipe canonical in
   `.claude/rules/autonomous-workflow.md`'s gate convention; change it there first). If an extra command was given in the
   arguments, the done-check is the gate **and** that command. Cap: `--cap N` if given, else **5**.
2. **Precondition:** must be on a `feature/*`/`fix/*` branch — refuse on shared branches
   (`development`/`main`/`master`, plus legacy `qa`).
3. **Loop (one fix per iteration)** — announce each: `── loop iteration <i>/<cap> ──`. Run the
   done-check → if green, stop. If red, pick the **first** failure, fix its **root cause**
   (delegate substantial fixes to `python-developer` / `frontend-developer`), then checkpoint via
   `/checkpoint` and repeat. On exit print
   `── loop complete: <green|cap-exhausted> after <i> iterations ──`.
4. **Never weaken the check** — no skipped/xfail'd tests, softened assertions, or loosened config
   to get to green.
5. **On cap exhaustion:** stop and escalate — report what was tried, the remaining failure with real
   output, and your diagnosis. Do not mark anything done.

Finish with a faithful summary: iterations used, what changed (commits), and the final done-check
output.
