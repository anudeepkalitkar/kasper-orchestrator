# Rule: Autonomous Workflow — goal → subtasks → verify before done

**Non-trivial work flows through goal-establishment → decomposition into verifiable subtasks →
autonomous execution → verification — with one consolidated clarifying round as the only gate.**
Inside a KASPER session, the `/kasper` skill's charter outranks this rule where they conflict.

## 1 · Intake — classify every prompt
- **Handle directly — no pipeline:** questions/discussion (deliverable = an assessment — report
  and stop, don't fix until asked); follow-ups on an in-flight task (the running work absorbs
  them — never start a second pipeline); trivial one-file, obviously-correct changes (just do
  them); slash commands (their own flow).
- **Decompose:** anything task-shaped with 3+ steps, multiple files, or distinct concerns.

## 2 · Goal — establish it, ask everything once
Restate the end goal in 1–2 sentences: what "done" observably looks like. If anything material
is ambiguous (scope, target, library/data-model/API forks, spend-heavy choices), ask **all**
clarifying questions in **one consolidated round** with a recommendation per fork — never drip,
never save questions for mid-implementation. If the goal is clear: zero questions. Use plan mode
for work that needs design (multi-file features, architecture, unclear scope); re-enter it when
new information invalidates the plan. Subagents cannot ask the user anything — questions happen
in the main session.

## 3 · Subtasks — the unit of work
Decompose into ordered subtasks, each independently completable, leaving the code working, and
mapping to **one git checkpoint** ([[git-workflow]]). Track them in the task doc
([[documentation]]). Every subtask carries:
- **goal:** the observable outcome, phrased so a verifier can judge it
- **done-check:** a runnable, deterministic command — exit code decides, never "looks good".
  If no runnable check is possible, the subtask is interactive-only — never invent a fuzzy check.
- **cap:** hard iteration limit, default **5**
- **owner / verifier:** who implements and who verifies — **different agents, always**; the
  writer never grades its own work ([[delegation]]).

## 4 · The gate — the baseline done-check
Run the project's tests **directly** — no Makefile, no CI. **Python:**
`ruff check . && mypy . && pytest tests/unit` · **Node:**
`npx eslint . && npx tsc --noEmit && npm test`. Heavier tiers (`tests/integration/`, e2e) run
before merging changes that touch what they cover ([[test-structure]]). A repo whose
`pyproject.toml` names the files to check runs **bare `mypy`** instead of `mypy .` — a path
argument overrides that `files` list, and mypy's crawl skips dot-directories.

## 5 · Execution guardrails
1. **One item per iteration** — pick a single subtask/failure, finish it, move on. Fan out
   independent work in parallel ([[delegation]]); serialize only true dependencies.
2. **Cap exhaustion escalates — never adapts.** Never weaken an assertion, skip/xfail a test,
   or tick the box anyway. Cap-exhausted items are recorded **blocked**, not done.
3. **Checkpoint per subtask, feature branches only** ([[git-workflow]]).
4. **Faithful state:** tick a box only when its done-check actually ran green.

## 6 · Verify before done
Never call work done without proving it: run the checks that apply AND confirm the *behavior*
is right — "it ran without error" ≠ "it's correct". Root-cause failures — never paper over a
red result. Report outcomes faithfully: failing tests reported with output, skipped steps named
plainly, "done and verified" only when true. The bar: what would convince a skeptical staff
engineer?

## 7 · Escalation — the ONLY reasons to stop mid-flow
1. Ledger-guarded, destructive, or paid ops ([[boundaries]] applies unchanged).
2. A genuine scope change or plan-invalidating discovery (re-plan, re-confirm).
3. Cap exhaustion (report faithfully).

Related: [[boundaries]], [[git-workflow]], [[delegation]], [[documentation]],
[[test-structure]].
