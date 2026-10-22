# Rule: Autonomous Workflow — goal → subtasks → verify before done

**Non-trivial work flows through goal → decomposition into verifiable subtasks → autonomous
execution → a bounded verify loop — with one consolidated clarifying round as the only gate.**
Inside a KASPER session, the `/kasper` skill outranks this rule where they conflict.

## 1 · Intake
- **Handle directly:** questions and discussion (the deliverable is an assessment — report, don't
  fix until asked); follow-ups on an in-flight task; trivial one-file, obviously-correct changes;
  slash commands.
- **Decompose:** anything with 3+ steps, multiple files, or distinct concerns.

## 2 · Goal
Restate the end goal in 1–2 sentences: what "done" observably looks like. If anything material is
ambiguous (scope, target, library/data-model/API forks, spend-heavy choices), ask **all** questions
in **one consolidated round** with a recommendation per fork — never drip them. A clear goal needs
zero questions, and a plan with no fork is stated, not asked. Use plan mode for work that needs
design. Subagents cannot ask the user anything; questions happen in the main session.

## 3 · Subtasks
Ordered, each independently completable, leaving the code working, mapping to **one git
checkpoint** ([[git-workflow]]), tracked in the task doc ([[documentation]]). Each carries:
- **goal** — the observable outcome; **done-check** — a runnable, deterministic command whose exit
  code decides (no runnable check → the subtask is interactive-only, never a fuzzy check);
- **cap** — hard iteration limit, default **5**; **owner** — who implements and runs the done-check.

**Verification is once per task:** the developer implements the whole task, one Codex **tester**
seat authors the tests and runs the gate, one Codex **reviewer** seat judges the diff, one
`--stack` per stack touched. A red gate or `REVIEW: changes` re-enters the developer, **a round at
a time, capped (default 3)**, then tester and reviewer run again; the loop and the action per seat
exit code live in the `/kasper` skill. A design needing judgment before it is built goes to the
**arch-reviewer** seat. The writer never grades its own work ([[delegation]]).

## 4 · The gate (the one place the recipe is written)
Run tests **directly** — no Makefile, no CI. **Python:** `ruff check . && mypy . && pytest
tests/unit` · **Node:** `npx eslint . && npx tsc --noEmit && npm test`. A repo whose
`pyproject.toml` names the files to check runs **bare `mypy`** — a path argument overrides that
`files` list, and mypy's crawl skips dot-directories. The tester seat runs it at task end; a
subtask owner runs it as their done-check. Heavier tiers (`tests/integration/`, e2e) run before
merging what they cover ([[test-structure]]).

## 5 · Execution guardrails
1. **One item per iteration**; fan out independent work in parallel, serialize true dependencies.
2. **Cap exhaustion escalates — never adapts.** Never weaken an assertion, skip/xfail a test, or
   tick the box anyway. Cap-exhausted items are recorded **blocked**.
3. **Checkpoint per subtask, feature branches only.**
4. **Faithful state:** tick a box only when its done-check actually ran green.

## 6 · Verify before done
The proof is the tester's `GATE: green` plus the reviewer's `REVIEW: approve` and confirmation the
*behavior* is right — "it ran without error" ≠ "it's correct". Root-cause failures; report
outcomes faithfully. The bar: what would convince a skeptical staff engineer?

## 7 · Escalation — the only reasons to stop mid-flow
Ledger-guarded, destructive, or paid ops ([[boundaries]]); a scope change; cap exhaustion.
Overnight, each parks the item instead (`/kasper` skill).

Related: [[boundaries]], [[git-workflow]], [[delegation]], [[documentation]], [[test-structure]].
