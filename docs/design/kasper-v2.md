# Design: KASPER v2 — the verify loop, overnight mode, the triager, and the v1 defects

- **Status:** proposed 2026-10-22 (task 020), revised the same day on the arch-reviewer's
  pass 1 (6 P1, 4 P2, fixed by class) and the researcher's verified harness facts
  (`<scratch>/reports/020-researcher.md`); the human's 2026-10-22 decisions are folded in
- **ADR:** [ADR-0016](../adr/0016-kasper-v2.md) (proposed)
- **Scope:** the seven items the human decided on 2026-10-21 (`tasks/BACKLOG.md` item 3).
  Out of scope: quiet mode; the permission gate (task 019, parked); `docs/ARCHITECTURE.md`
  (still owed, separate task)
- **Baseline:** master `66dcf3b`. Already fixed there and not designed again: S5 (run_hook
  trusts the project copy by manifest), S7 (checkpoint refuses detached HEAD and local-only
  paths), S8 (`/checkpoint` and `/new-task` take a file, never `$ARGUMENTS` in shell), P1/P2
  (task 017)
- **Decided by the human, 2026-10-22:** `REVIEW: changes` on P1 and P2; overnight cap 60
  continuations with `until` optional; the triager on every non-trivial task; a Stop hook may
  be added to `settings.json`; the rules are deduped in place; the task branch is made by
  `new_task.py --branch`

## What and why

KASPER v1 verifies once and stops. The human's 2026-10-21 analysis
(`claude-temp/sessions/3e5f891b-d6c9-4587-9c06-eaf57256c758/reports/analysis-consolidated.md`)
found that it sat idle for 21 hours waiting for a reply (L1), has no re-test or re-review
loop and no action defined for a red gate or a seat failure (L2, L3), reviews one finding at a
time, and dies with a usage limit (L7). On top of that sit twelve script defects (S1–S12), a
router that charges for safety wording (S2), and a rule set that loads twice in this repo
(D1).

**Done looks like**, observably:

1. A task's verification is a loop: tester → reviewer → developer fix → tester → reviewer,
   bounded by a round cap, with a scripted action for every seat exit code 0–5, and a
   reviewer first line a script can read (`REVIEW: approve` / `REVIEW: changes`). A finding
   is fixed as a class, never one instance at a time.
2. With `overnight: on` in `tasks/BACKLOG.md`, the session that turned it on keeps working
   through the open backlog items unattended, parks anything it cannot finish, and leaves
   `tasks/morning-<date>.md` for the human. Every task starts on its own branch from a clean,
   up-to-date mainline.
3. A goal becomes a plan tree written by a `triager` agent; KASPER runs its independent
   nodes in parallel, at most four agents at once, each writer in its own worktree, and only
   each node's declared files are ever staged.
4. The architect is spawned only for big or hard-to-undo work, or when the human asks.
5. A subagent that was spawned at `fable` and died on a documented limit or overload error is
   respawned once at `opus` without asking.
6. KASPER's replies to the human explain every term of art and never trim an explanation.
7. S1–S4, S6, S9–S12 are fixed, the router's exclusion rule never touches risk words, and the rules say each
   fact once.

## Modules touched

All paths are relative to the repo root; the same files land in `~/.claude/` through
`install.py` (its `OWNED_PATHS` are directories, so the new files under `scripts/`,
`agents/` and `commands/` need no installer change).

| File | What changes | What stays |
|---|---|---|
| `.claude/scripts/codex_seat.py` | `read_gate_verdict` → `read_verdict(out, seat)` driven by one `VERDICT_LINES: dict[Seat, tuple[str, str]]` (`tester: ("GATE: green", "GATE: red")`, `reviewer: ("REVIEW: approve", "REVIEW: changes")`); the reviewer branch of `run_seat` mirrors the tester's (exit 0/1/5, no longer Codex's own return code — S9's "empty review exits 0"); `project` comes from `git rev-parse --show-toplevel` with a 10 s timeout instead of `(Path.cwd() / ".git").is_dir()` (S9 worktrees, S12 subdirectory); `--brief`/`--out` are resolved before Codex runs with `cwd=project`; `shutil.which("codex")` resolves the executable once and feeds both `is_logged_in` and `codex_argv` (S10); the run uses `launch` + `Popen.wait(timeout)` + `kill_and_wait`, moved here from `debate.py`, so a timeout kills the whole process group (S11) | exit-code numbering, `build_prompt`, `--stack`, `--dry-run`, the `.log` transcript (overwritten by design), the arch-reviewer branch |
| `.claude/scripts/debate.py` | `launch`, `kill_group`, `kill_and_wait`, `resolve_executables` deleted here and imported from `codex_seat` (`sys.path.insert(0, Path(__file__).parent)` — the precedent is `permission_recorder.py:34`) | the three-phase protocol (ADR-0014) |
| `.claude/codex/reviewer.md` | the final message's first line is `REVIEW: approve` or `REVIEW: changes`; findings are grouped under `## Class: <name>` headings (the sweep contract, below) | the P1/P2/P3 ranking, `file:line` grounding, "No findings." |
| `.claude/scripts/overnight.py` (new) | the Stop hook that keeps an overnight session going (§2) | — |
| `.claude/settings.json` | a second `Stop` handler, `run_hook.py overnight`, after `notify` (approved by the human 2026-10-22) | hooks, allow, deny; **no** `autoContinueAtUsageLimit` key — it is on by default and user-scoped (§5) |
| `install.py` | `status()` also hashes the source repo's owned files (the manifest records `source`) and prints `behind  <rel>` when the home matches the manifest but not the source; `behind` exits 1 (S4) | ownership, backups, uninstall, `merge_settings` |
| `.claude/scripts/git_checkpoint.py` | `--only <path>...`: commit exactly those paths and nothing else, **by refusal**, with the commit code untouched — before staging, any path already in the index (`git diff --cached --name-only`) that is not in the list is a refusal naming it (a clean baseline, §3, means this never fires in a wave; a human-staged stray is the human's to unstage); then `git add -- <paths>`; then the staged set must be non-empty and a subset of the list, else refuse; a forbidden path in the list is refused as today. `docs/adr/` leaves `FORBIDDEN_DIRS`; it is refused exactly when `project_dirs.adr_local_only(root)` says so (imported via `sys.path.insert(0, Path(__file__).parent)`, the `permission_recorder.py:34` precedent) — one function, so the checkpoint and the SessionStart hook can never disagree (class 5) | the shared-branch and detached-HEAD refusals, `tasks/`/`claude-*`/`.env` refusals, `--message-file`, the `commit`/`push` code |
| `.claude/scripts/new_task.py` | `--branch`: refuse unless on the mainline with a clean tree that is not behind its upstream (class 2, below); then `git switch -c feat/<NNN>-<slug>` (10 s timeouts) and fill the doc's `Branch:` line; the template gains `- **Rounds:** 0/3`; an existing branch name makes `git switch -c` fail and the command exit 1 — nothing is overwritten | numbering, slug, title-file |
| `.claude/commands/new-task.md`, `.claude/commands/checkpoint.md`, `.claude/commands/overnight.md` (new) | `/new-task` passes `--branch` and states the preconditions; `/checkpoint` documents `--only` and the ADR rule; `/overnight on|off` edits the backlog header (§2); all name the installed script path (S1) | — |
| `.claude/scripts/model_route.py` | `count_phrases` skips a phrase only as the object of an explicit exclusion in one clause: a `PROHIBITIONS` entry (do not, don't, never, must not, mustn't), then an `EXCLUSION_VERBS` entry (touch, edit, change, modify, delete, write, run), then at most `EXCLUSION_OBJECT_WORDS = 4` words before the phrase, none of them a `SPAN_STOP_WORDS` entry; punctuation (, ; : . ! ?) also ends the clause. Human decision 2026-10-22: risk words are never discounted, so this rule no longer touches the risk list; risk is always scored in full and errors lean expensive (S2) | every constant and threshold |
| `.claude/rules/model-routing.md` | one table row for the exclusion rule, in the same commit as the constant; its command line names the installed path (S1); owned whole by node C | — |
| `.claude/scripts/project_dirs.py` | `_link_harness_memory`: a clashing file is moved to `<stem>.from-harness<suffix>`, and when that exists too, `<stem>.from-harness-2<suffix>`, `-3`, … — the first free name, never an overwrite (class 4); `shutil.rmtree` → `harness_mem.rmdir()` so a non-empty dir fails loudly (S3); `main()` prints `Overnight: on (session <id>, until <t>)` when the backlog header says so, so a new session sees it. New public `adr_local_only(root: Path) -> bool | None`: locates the exclude file with the existing `_exclude_file(root)` (`git rev-parse --git-path info/exclude`, which is what makes a linked worktree — `.git` a file — resolve correctly) and answers whether any line of it covers `/docs/adr/` by the same slash-stripped equivalence `_ensure_excluded` uses at `:141-144`; that equivalence moves into one helper, `_entry_present(lines, entry)`, that `_ensure_excluded` calls too — no copy. None when `root` is not a repo (the checkpoint then refuses, as it already does off-repo) | everything else |
| `.claude/scripts/autoformat.py` | `_RUFF_STEPS[0]` becomes `["check", "--fix", "--select", "I", "--quiet"]` — import ordering only; F401 removal is left to the gate so a just-added import survives (S6); the stale `make setup` docstring line goes | `ruff format`, never-blocks |
| `.claude/scripts/notify.py` | drop `SND_ASYNC` so the WAV finishes before the hook process exits (S10; Windows still unverified) | sound choice |
| `.claude/agents/triager.md` (new) | the planning-only agent (§3) | — |
| `.claude/skills/kasper/SKILL.md` | step 2 gets the seat action table; step 3 becomes the triager + narrowed architect trigger; new steps for task start, overnight, parallel waves, model fallback; the Reporting protocol becomes the plain-language rules; **Ending KASPER** gains step 0 (turn overnight off); script paths per S1 | chain law, landing human-gated, task doc single-writer |
| `.claude/rules/*.md` except `model-routing.md`, `CLAUDE.md`, `.claude/CLAUDE.md`, `README.md`, `.claude/agents/*.md`, `.claude/commands/until-green.md`, `.claude/commands/permit.md` | the dedupe pass, S1 paths, D2/L8, the ADR-by-exclude rule in `git-workflow.md` and `agents/git-workflow.md` (§7) | the rules' decisions |

### S1 — which script path a command names

`python3 .claude/scripts/<x>.py` only resolves in this repo. Every KASPER-facing command
names the installed copy, `python3 "$HOME/.claude/scripts/<x>.py"` — the form `settings.json`
already uses on every platform, including Windows under the Bash tool. The files that carry such
a command today, each owned by exactly one build node (§The build): `SKILL.md`, `CLAUDE.md`,
`.claude/CLAUDE.md`, `README.md`, `rules/boundaries.md`, `rules/autonomous-workflow.md`,
`rules/delegation.md`, `commands/permit.md`, `commands/until-green.md` → node D;
`commands/new-task.md`, `commands/checkpoint.md` (their `allowed-tools` patterns included) →
node B; `rules/model-routing.md` → node C. Not S1: the ledger's `_doc`, `run_hook.py`'s
docstring and `settings.json` (already `$HOME`). One exception, stated once in SKILL.md: in
the KASPER source repo (the one `~/.claude/kasper-manifest.json` records as `source`) KASPER
runs `.claude/scripts/<x>.py` so the copy under development is the one exercised. The ledger's
`python3` key ("dev toolchain") already allows both heads; no ledger change.

## Data and flows

### 1 · The verify loop

```
developer ──▶ tester ──green──▶ reviewer ──approve──▶ git-workflow (ready to land)
   ▲            │ red               │ changes
   └────────────┴───────────────────┘   one re-entry = one round; cap in the task doc (default 3)
```

Source: `docs/design/kasper-v2-verify-loop.mmd`.

**Round.** A round is one developer re-entry caused by `GATE: red` or `REVIEW: changes`. The task
doc's `Rounds: n/3` counts them; the first pass is round 0. Cap exhausted → the task is recorded
**blocked** (never done), parked overnight or put to the human by day. After any re-entry the
tester runs again (the head that lands must be the head the gate saw), then the reviewer.

**The reviewer's verdict line** — `.claude/codex/reviewer.md`, same contract as the tester's: the
first line is exactly `REVIEW: approve` or `REVIEW: changes`, nothing before it. `changes` when
any P1 or P2 finding exists (the human's decision); `approve` when there are none or only P3
nits, which KASPER records in the task doc's Review section and does not route back.
`codex_seat.py reviewer` exits 0 approve · 1 changes · 5 no verdict line (which also covers the
empty-report case, S9).

**Sweep the class.** Findings are grouped:

```
REVIEW: changes
## Class: swallowed exceptions — `except: pass` hides the failure; raise or log it
[P1] .claude/scripts/x.py:42 — the OSError is swallowed and the caller sees success
[P1] .claude/scripts/y.py:17 — same
## Class: missing return annotation
[P2] .claude/scripts/x.py:88 — …
One line naming what is genuinely good.
```

KASPER's re-entry brief to the developer names each class, not each line: "find and fix every
instance of `<class>` in this task's diff, including instances the reviewer did not list, in one
pass; report the count". The next review judges the class: a further instance of a class already
reported is a P1 on the sweep itself. This is the human's 2026-10-21 rule
(`feedback-sweep-the-class`) made into the contract.

**KASPER's action per seat exit code** — the table lives in SKILL.md step 2; the developer builds
nothing for it. Retries (exits 2, 4, 5) never consume a round and are capped at one each.

| exit | meaning (`codex_seat.py`) | tester | reviewer |
|---|---|---|---|
| 0 | green / approve | run the reviewer | git-workflow: checkpoint, PR, report ready to land |
| 1 | red / changes | re-entry: developer gets the report path; round +1; then tester | re-entry by class; round +1; then tester, then reviewer |
| 2 | KASPER's own call is wrong (brief path, stack name, not a repo) | fix the call, rerun once; a second 2 → blocked | same |
| 3 | Codex unusable (not signed in / not on PATH) | stop the task. Day: ask the human to run `codex login`. Night: set `overnight: off`, write the morning report, stop (§2 — every task needs Codex) | same |
| 4 | timeout | rerun once with `--timeout 3600`; a second 4 → blocked | same |
| 5 | no verdict line | rerun once with the brief plus one line restating the first-line rule; a second 5 → blocked | same |

`codex_seat.py arch-reviewer` is unchanged (ranked findings, no verdict, exit 5 only when the
report is empty); KASPER reads its findings and puts P1s to the architect as a resume.

### 2 · Overnight mode

**The backlog file** is the existing `tasks/BACKLOG.md` (local-only, human-edited) with a fixed
line grammar the hook can read and the human can still write by hand:

```
overnight: on                              # or off; the only two values
session: 3e5f891b-d6c9-4587-9c06-eaf57256c758   # the session that turned it on; written by /overnight
until: 2026-10-23T07:00                    # optional, local time, ISO 8601
- [ ] 021-gate-redesign — <goal in one line>
- [x] 020-kasper-v2 — done 2026-10-22, PR #21
- [~] 019-gate-redesign — parked: round cap (3/3) on fix/gate-redesign, PR n/a
```

Open `- [ ]`, done `- [x]`, parked `- [~]`; everything else in the file is free text the hook
ignores. KASPER migrates the current `[todo]`/`[done …]` lines to this grammar as its first
bookkeeping act after the build lands (it is the single writer of `tasks/`).

**The keep-going guard: `overnight.py` versus `/goal`.** Both exist now (A1, A2 verified).
`/goal <condition>` is a session-scoped, prompt-based Stop hook: a Haiku evaluator reads the
transcript (no tools, no files, no clock) and judges met / not yet / impossible; one goal per
session; `/goal clear` turns it off; it self-stops after several turns without a tool call; a
usage limit pauses it; it is deferred while a subagent runs. Against the overnight contract:

| contract item | `/goal` | `overnight.py` |
|---|---|---|
| backlog walk | the evaluator cannot read `tasks/BACKLOG.md`; KASPER must narrate the backlog state every turn — extra turns, the most expensive thing in the session (SKILL step 10) | one file read, deterministic |
| park-and-move-on | a SKILL rule either way | same |
| cap | undocumented "several" no-tool turns, plus the built-in 8 | the built-in 8 plus a counter the human can read |
| off-switch | `/goal clear`, in-session only | `overnight: off` in the file, from any session or the human's editor |
| `until` (the human's optional morning) | no clock | one comparison |
| morning report | a SKILL rule either way | the ending instruction names it |
| testable here | no — harness state | yes, with `tests/helpers/hooks.py` payloads |

`/goal` is less code — none — but meets the contract only by moving the state into prose the
evaluator reads second-hand, and has no `until`. `overnight.py` is ~80 lines whose every decision
is a file read. **Chosen: `overnight.py`.** `/goal` is named in SKILL.md as the zero-build
version the human can type today ("`/goal every open item in tasks/BACKLOG.md is done or
parked and tasks/morning-<date>.md is written`") when the hook is not installed.

**`overnight.py`** is a Stop hook dispatched like every other hook (`run_hook.py overnight`),
wired as the second `Stop` handler in `settings.json`. It reads the payload from stdin (`cwd`,
`session_id`, `stop_hook_active`) exactly as `project_dirs.py main()` does, keeps a counter at
`claude-temp/sessions/<session_id>/overnight.count` and an `overnight.ended` marker beside it
(the session scratch dir — the one place a hook writes, save the fallback marker
`claude-temp/overnight-<session_id>.ended` when that dir refuses `overnight.ended`), and decides in this fixed order; the
first rule that applies wins:

1. **Already ended:** `overnight.ended` or its fallback exists (the night already ended), or cannot be checked (a stat that fails with anything but file-not-found — `Path.exists()` is not used, since it reports a failed stat as absent) → print nothing, exit 0. Checked first.
2. **Not ours:** `session_id` is invalid, or the backlog is absent, unreadable, header not `overnight: on`, or `session:` is not this session's id (`_session_id` and `project_root` come from `project_dirs.py`) → print nothing, exit 0. This is the whole behaviour by day, and in every session but the one that typed `/overnight on` — a next-morning session never resumes the night's walk by accident. An unreadable header means ownership is unknown: a silent pass.
3. **Ending:** after ownership, the counter is read and incremented first (an absent counter is 0; a corrupt one counts as `MAX_CONTINUATIONS`) and written back, then tested in this order: no `- [ ]` line left ("backlog empty"), `until` passed or unreadable ("until passed"), the incremented counter at or over `MAX_CONTINUATIONS = 60` ("cap reached" — the ending fires when the previous count was 59, so the cap's last continuation is the one that writes the morning report). Any `OSError` on the counter ends the night too ("state unreadable") — only file-not-found reads as an absent counter, so a failed read never resets the count; the ending block still needs a persisted marker. Each ending first checks the write-free ceiling: the session transcript (the payload's `transcript_path`, read-only) is scanned from the start in 1 MiB chunks, with a short overlap so a sentinel split across a chunk boundary counts once, stopping as soon as two ending blocks are seen; blocks are counted by the fixed `[overnight-ending]` prefix every ending reason carries, so the ceiling needs no write to hold. The count is an upper bound, not exact (a session that reads the hook's source inflates it), which errs toward silence, never toward repetition. If the ceiling is reached, the hook prints nothing. Otherwise it writes `overnight.ended` and prints `{"decision": "block", "reason": "[overnight-ending] Overnight ended (<why: backlog empty | until passed | cap reached | state unreadable>). Read tasks/BACKLOG.md, then end the night: append tasks/morning-<date>.md and set `overnight: off`; then stop."}` and exits 0; the end-the-night procedure sets `overnight: off`, which silences the hook. If the marker write fails with `PermissionError` and `os.access` says the backlog file and its directory permit writes, the hook writes the fallback marker `claude-temp/overnight-<session_id>.ended` and prints the block only once that write has landed (the next Stop finds it and passes silently; `overnight: off` will still land); with an unwritable backlog, or a fallback write that fails with any `OSError`, the hook passes silently. A block is therefore only ever printed after a marker persisted, so the ending blocks stay bounded even when no write succeeds and the transcript does not persist. Any other `OSError` on the marker (full disk, quota, read-only or I/O error) passes silently with a stderr note `overnight: cannot write marker (<exc>); passing silently.`, since the report and `overnight: off` would fail too and a block nothing can silence is never repeated. A missing or unreadable transcript makes the count unknown and does not by itself silence the hook; the marker rules above decide, exactly as for a count of 0. The cap is an ending condition like the others — there is no silent final stop except these four silent exits after ownership (ceiling, non-permission marker failure, unwritable backlog, failed fallback marker), besides rule 1's marker that cannot be checked.
4. **Continue:** (the counter was already incremented in rule 3), print `{"decision": "block", "reason": "Overnight: <n> open backlog item(s). Read tasks/BACKLOG.md, take the first open one, run it as a task; park it if you cannot finish; then stop."}`, exit 0.

Every reason starts with a tool call ("Read tasks/BACKLOG.md"), so the harness's built-in cap
— eight consecutive blocks with no tool call between them, `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` —
is reset on every continuation that does any work, and ends the night harmlessly for one that
does none; the design relies on it rather than fighting it. `stop_hook_active` is not consulted.

**Ending the night is one procedure, run by every path but four silent hook exits** (the
transcript ceiling, a non-permission marker-write failure, a refused marker with an unwritable
backlog, and a failed fallback marker — rule 3: each stops without a report, by design, because the night cannot be ended or
reported) (classes 1 and terminal-report): "end the night" = one KASPER bookkeeping Bash call that **first appends**
`tasks/morning-<date>.md` from the backlog's current state (even when no item was touched —
the report then says so and why), **then** writes `overnight: off`. `off` is never written
without the report before it. The paths, each ending here:
- the hook's ending block (rule 3): backlog empty, `until` passed, or the cap reached;
- KASPER by itself: seat exit 3; a task-start refusal (dirty tree, stale mainline — §Task
  start); a parking whose WIP checkpoint is refused (§Parking);
- the human: `/overnight off` (the command runs the procedure), or `/kasper` shutdown —
  **Ending KASPER** gains step 0: if the header says `on`, end the night, before writing memory;
- the hook's insurance: if KASPER ignores the ending block, rule 1 is terminal for the session
  and rule 2 keeps every other session out; the report is then written late (next item);
- a crash, the harness's own 8-block cap, or a usage-limit pause: `autoContinueAtUsageLimit`
  resumes the same session (same id, same counter) and the walk continues; any **new** session
  that starts under a header still `on` for another session (`project_dirs.py` prints
  `Overnight: on (session …)` at SessionStart) runs "end the night" as its first bookkeeping
  act and tells the human — the late morning report for a night that died without one.

**`/overnight on|off`** (`.claude/commands/overnight.md`): `on` — KASPER checks the backlog has
open items with one-line goals, writes `overnight: on`, `session: <this session's id>` (from
the scratch path SessionStart printed) and, if the human gave one, `until:`, reminds the human
that landing stays theirs (every finished item is parked with its PR), then stops — the next
Stop starts the walk. `off` — writes `overnight: off`.

**Running one backlog item** is the normal task flow with three overnight rules in SKILL.md:
(a) no `AskUserQuestion` — a fork an agent reports, a landing, a cap exhaustion, exit 3, or a
second exit 2/4/5 **parks** the item; (b) landing is human-only, so every finished item ends
"ready to land" and is parked with its PR link — the morning report is the merge list; (c) a
permission prompt or classifier block waits for a human like it does today (L6 is the parked
task 019's problem, not this design's; the morning report cannot see it, so the human's first
look is the terminal).

**Parking leaves a clean tree** (class 2). To park, KASPER routes to git-workflow: checkpoint
the task branch with `wip(<slug>): parked — <reason>` (the ordinary checkpoint; it pushes the
feature branch, which is in the standing exception), then `git switch <mainline>`. If the
checkpoint is refused (a forbidden path is pending, or the branch is shared), the item is parked
with `tree not clean: <paths>` and KASPER runs "end the night" (report, then `off`) — a dirty
tree is never carried into the next task. The backlog line records the
branch and the WIP commit; the morning report lists it under "parked, resume with".

**Task start** (L9, class 2): `/new-task` runs `new_task.py --branch`, which refuses — exit 1,
reason on stderr — unless all three hold: the current branch is the mainline (`development` if
the repo has it, else the branch `refs/remotes/origin/HEAD` names, else `master`/`main`,
whichever exists — the one rule D2 also writes into `agents/git-workflow.md` and
`commands/checkpoint.md`); `git status --porcelain` is empty (excluded paths never show); and
after `git fetch` (a network read) `git rev-list --count HEAD..@{upstream}` is `0`. Then
`git switch -c feat/<NNN>-<slug>` and the `Branch:` line. By day a refusal is a message to the
human; at night it parks the item with the reason (`dirty baseline` / `mainline behind`), and
because the tree is the same for every item, KASPER then runs "end the night" (report, then
`off`) rather than parking the whole backlog one line at a time. The earlier idea that
uncommitted mainline work "rides along" into the branch is withdrawn: nothing rides along.

**The morning report**, `tasks/morning-<YYYY-MM-DD>.md`, written by KASPER (its `tasks/`
bookkeeping; appended to, never overwritten, if two runs end on one date): one line per item
touched — done / parked / blocked, branch, WIP or PR, rounds used, verify outcome — then
"decisions you need to make" (every parked question verbatim), then "PRs waiting for your
merge", then "parked, resume with" (branch + commit).

### 3 · The triager and parallel waves

**The triager is a planning-only subagent** (`.claude/agents/triager.md`, Read/Grep/read-only
Bash, writes only its report), spawned for every non-trivial task (the human's decision). Chain
law forbids an agent that spawns agents, so the triager plans and KASPER runs. Its value is
where the reading happens: decomposition needs the code in view, and a triager's context is
disposable where KASPER's (~163K per turn) is not. It receives the goal and the files to start
from; it writes `<scratch>/reports/<task>-plan.md`:

```
# Plan: 020 kasper-v2
architect: no — <one line against the trigger rule>
- A seat-loop — owner: python-developer · stack: python · files: .claude/scripts/codex_seat.py, .claude/scripts/debate.py, .claude/codex/reviewer.md · after: — · done-check: `ruff check . && mypy && pytest tests/unit --deselect tests/unit/scripts/test_codex_seat.py::test_reviewer_returns_codex_status_without_requiring_a_gate`
  goal: <one observable outcome>
  old-behaviour tests: tests/unit/scripts/test_codex_seat.py::test_reviewer_returns_codex_status_without_requiring_a_gate
- B overnight — … · after: —
- E docs — … · after: A, B
```

One node per line, children indented under their parent, `after:` naming dependencies by
letter, `files:` naming every path the node will write, and `old-behaviour tests:` naming every
existing test that asserts what the node changes (class 3, below). KASPER copies the nodes into
the task doc's subtasks as they are (they carry the template's goal / done-check / owner; cap
stays 5).

**Waves.** KASPER spawns, in one message, every node whose `after:` nodes are done and whose
`files:` set is disjoint from every other node in the wave — at most `MAX_PARALLEL_AGENTS = 4`
(a SKILL.md constant, not code). Before the wave, KASPER's one bookkeeping Bash call checks the
main tree is clean (`git status --porcelain` empty — if not, git-workflow checkpoints the task
branch first, so every worktree and the tree the patches land on share the same base; class 2
dirty-baseline) and creates one worktree per file-mutating node:

```
git worktree add --detach claude-temp/worktrees/<NNN>-<node> HEAD
```

(`git worktree` is ledger-allowed, "local"; `claude-temp/` is git-excluded.) The brief names the
worktree's absolute path as the agent's working root. Developers never commit, so a detached
worktree is enough — no per-node branch.

**Integration** is `git-workflow`'s, per node, in the main tree on the task branch — and it
stages only the node's declared files (class 2 unscoped staging):

```
git -C <wt> status --porcelain                       # any path outside the node's files: → scope drift, not integrated
git -C <wt> add -- <the node's declared files>
git -C <wt> diff --cached --binary > <scratch>/patches/<NNN>-<node>-<n>.patch   # n: first free number
git apply --index <that patch>
python3 "$HOME/.claude/scripts/git_checkpoint.py" --only <the node's declared files> --message-file …
git worktree remove --force claude-temp/worktrees/<NNN>-<node>
```

Scope drift — a changed path the node did not declare — goes back to KASPER as a report: either
the triager's `files:` was wrong and KASPER adds the path to the node (then integrates), or the
developer strayed and the node is rerun; it is never silently staged. `--only` is the second
fence: it refuses if the index already holds a path outside the node's list, so even a stray
`git add` before integration cannot ride into the node's commit. A failed `git apply` is also a
report: the node reruns in a fresh worktree from the new head. No `git merge` is ever run — it
is guarded by `ask_words`, which at night means a prompt with nobody there.

**What an integration checkpoint may land on** (class 3 at integration). `agents/git-workflow.md`
step 1 requires the tester's green or a full gate run by git-workflow itself; during a wave the
nodes' declared old-behaviour tests are still red by design, and the tester has not run yet. The
rule, written into `agents/git-workflow.md` step 1 as its one narrow exception (node D owns the
file): **an integration checkpoint may land on a gate run by git-workflow that deselects
exactly the old-behaviour tests the task doc lists for the nodes integrated so far — nothing
else — and the task doc's commit line records that deselect list.** Any other red is a stop. The
integrating tester then rewrites the listed tests to the new contract as its first act and runs
the plain gate; the PR is opened only on that plain green, so the exception never reaches a PR.
Then the **one** verify loop on the task branch, with every stack the nodes touched.

### 4 · The architect trigger

SKILL.md step 3 narrows to: spawn `architect` when the work introduces **a new data model**, **a
new external contract** (an API, a file format other tools read, a CLI another script parses),
or **touches three or more components**, **or when the human asks**. The triager applies the
rule with the code in view (`architect: yes|no — why`); KASPER follows it and the human can
override either way. Everything else: the triager's plan is the design, and the arch-reviewer
seat is not run. The debate trigger (step 3's "two of four" rule) is unchanged and still sits in
front of an architect spawn.

### 5 · Model fallback

Three rules in SKILL.md, no code (class: ambiguous failure classification — the predicate is
now specific):

1. **Respawn on a limit, once, only from `fable`.** KASPER respawns a subagent at `model: opus`
   with the same brief only when **all three** hold: the spawn KASPER issued carried
   `model: fable` (the routed or overridden tier it passed — KASPER knows this, it wrote it);
   the result is the harness's early-termination text, `Agent terminated early due to an API
   error`, whose detail matches a documented limit or overload text — `hit your .* limit`,
   `usage limit`, `rate limit`, `overloaded`, `high load`, `529`; and the result carries no
   digest. One respawn per spawn; the task doc gets `fallback: <agent> fable→opus (<detail>)
   <time>` — the existing override-with-a-reason rule. Anything else that fails is a failure
   to report, not a model problem.
2. **A harness-switched report is accepted as is.** A report opening
   `⚠ This agent switched to <model> because its earlier model could not be used.` is a
   completed run; KASPER records the switch in the task doc and does not respawn.
3. **The KASPER session runs on Opus** — a recommendation to the human at `/kasper` time
   (`/model opus`), since only the human's `/model` changes the session's model; the router
   still sends architect briefs to `fable`. Note only, nothing to build:
   `autoContinueAtUsageLimit` is on by default for claude.ai-subscription sessions and lives in
   user or managed scope, not the project's `settings.json`; the human can see it with
   `/config`. It is what resumes an overnight session after an account-wide limit.

### 6 · Plain-language replies

The SKILL.md Reporting protocol is rewritten to four rules: (1) the first use of a term of art
in a session carries its meaning in a clause — "the tester seat (the Codex run that writes the
tests and runs the gate)"; (2) an explanation is never cut to save tokens — length comes from
structure, not trimming: outcome → what it means → what the human must decide; (3) agent digests
(~10 lines) are agent→KASPER wire format and stay; KASPER's relay is the human-facing layer and
translates them; (4) the cost-pressure sentence lives in SKILL.md steps 9–10 only; the seven
agent files and the rules say it once by reference (C3).

### 7 · The rules dedupe (D1) — in place

The human decided the rules are deduped where they are; the double load in this one repo is
accepted. Content: every fact gets one owner file; the others link with `[[name]]`. Owner map —
the gate recipe and the bare-`mypy` note → `autonomous-workflow` §4; ADR visibility and `tasks/`
local-only → `documentation`; the excluded dirs and the `project_dirs` hook → `boundaries`;
checkpoint semantics, including the new rule that a checkpoint stages `docs/adr/` exactly when
`.git/info/exclude` lacks `/docs/adr/` (class 5, replacing "a private repo commits its ADRs by
hand in a chore commit") → `git-workflow`; cost pressure → SKILL.md. Target: the eight rules
from 42.7K chars to ≤ 26K, and the root `CLAUDE.md` (11.1K, loaded beside `.claude/CLAUDE.md`'s
10.1K in this repo) to a ≤ 2K pointer: what this repo is, the bare-`mypy` gate, and "the config
itself is `.claude/CLAUDE.md`". `model-routing.md` is left to node C (it is 3.8K and carries the
constants table).

Also in this pass: D2 — the mainline rule (§2, task start), written where `development` is
assumed (`agents/git-workflow.md:11,16`); L8 — `commands/until-green.md` loses
`make gate`/`npm run gate`/`scripts/gate.sh` and `frontend-developer` and names the gate recipe
by reference and the language-named developers.

## Boundaries

- **Writes:** project root only, as today. New writers: `overnight.py` writes a counter and a
  marker under `claude-temp/sessions/<id>/` (a refused marker falls back to
  `claude-temp/overnight-<id>.ended`); `new_task.py --branch` creates a local branch;
  KASPER writes `tasks/morning-<date>.md` and the backlog header; worktrees and patches live
  under `claude-temp/`. Nothing writes `~/.claude/` (install.py as before); no settings key
  beyond the approved Stop handler.
- **Calls:** `git rev-parse`, `git fetch` (read), `git rev-list`, `git switch`, `git worktree
  add/remove`, `git -C <wt> status/add/diff`, `git apply --index` — local. `git worktree`,
  `git switch`, `python3` are ledger-allowed now; `git fetch`, `git apply`, `git -C` and
  `git rev-list` may prompt once and the recorder promotes them (no widening). A WIP checkpoint
  pushes a feature branch — the standing exception.
- **Must not:** run `git merge`; answer a prompt; merge a PR; respawn more than once or from any
  tier but `fable`; block a Stop once the ending marker exists; let a hook write `tasks/`;
  stage a path a node did not declare; start a task on a dirty or stale mainline.
- **Needs the human's word (given 2026-10-22):** the Stop handler in `settings.json`. Still
  theirs: `/model opus` for the session; the migration of their own `tasks/BACKLOG.md`.

## Test plan by tier

Done-check for every build subtask, run from the repo root:
`ruff check . && mypy && pytest tests/unit` (bare `mypy`: `pyproject.toml` names the files) —
with `--deselect` of the node's declared old-behaviour tests during the build only (class 3).

**`tests/unit/`** (no I/O; scripts loaded with `tests/helpers/hooks.py::load_script`):
- `tests/unit/scripts/test_codex_seat.py` — `read_verdict` for both seats, exact-match only; the
  reviewer branch exits 0/1/5 by verdict line, never by Codex's return code (the integrating
  tester rewrites `test_reviewer_returns_codex_status_without_requiring_a_gate` to this); a fake
  `git rev-parse` failure → exit 2; `shutil.which` None → exit 3; a faked `wait` timeout calls
  `kill_and_wait`.
- `tests/unit/scripts/test_debate.py` — still green with the process helpers imported from
  `codex_seat` (fakes in `tests/helpers/debate.py` patched on the new owner).
- `tests/unit/scripts/test_overnight.py` (new) — the four rules in order from fake payloads and
  fake file reads: marker present → nothing; off / other session
  → nothing; owner with an ending condition (backlog empty, `until` passed, counter at/over the cap, state unreadable) → marker written and the ending block returned, or a silent pass with a stderr note when nothing can be written;
  open item → block with the count; invalid `session_id` → nothing and no file.
- `tests/unit/scripts/test_git_checkpoint.py` — `--only` stages exactly the named paths, refuses
  a forbidden one, an empty set, and a pre-staged path outside the list (naming it); `docs/adr/`
  refused iff `adr_local_only` (faked) says local-only, and refused when it returns None.
- `tests/unit/scripts/test_model_route.py` — "never touch the ledger" still scores the risk word in full (risk words are never discounted, human decision 2026-10-22); the exclusion rule does not touch the risk list.
- `tests/unit/scripts/test_project_dirs.py` — a clashing memory file lands on the first free
  `.from-harness` name with an existing backup untouched, nothing is removed, a non-empty
  harness dir raises; the `Overnight:` line prints iff the header says on; `adr_local_only`
  answers True for `/docs/adr/`, `docs/adr/` and `docs/adr` lines alike (the same
  `_entry_present` that `_ensure_excluded` uses), False for a file without one, None off-repo.
- `tests/unit/scripts/test_overnight.py` — the cap is an ending condition: a counter at or over
  60 returns the ending block with "cap reached" and writes the marker; with the marker present the next Stop is silent.
- `tests/unit/test_install.py` — `status_report` gains `behind`.
- `tests/unit/scripts/test_new_task.py` (new) — `--branch` names `feat/<NNN>-<slug>`, writes the
  `Branch:` line, and refuses off-mainline, dirty, and behind, git faked.

**`tests/integration/`** (real git in a temp repo, real ruff):
- `tests/integration/scripts/test_codex_seat.py` — `--dry-run` from inside a `git worktree`
  and from a subdirectory finds the top level (S9, S12).
- `tests/integration/scripts/test_new_task.py` — `--branch` creates and checks out the branch
  from a clean mainline and refuses a dirty one.
- `tests/integration/scripts/test_git_checkpoint.py` — `--only` leaves an undeclared changed
  file unstaged; `docs/adr/` is committed when the exclude file lacks the entry.
- `tests/integration/scripts/test_autoformat.py` (new) — a file with an unused import keeps it
  after the hook; import order is still fixed.
- `tests/integration/test_install_io.py` — `--status` exits 1 and prints `behind` when the
  source repo's owned file differs from the installed one.
  Command: `pytest tests/integration`.

**`e2e/`** — none runnable without Codex and a live session; these are interactive-only
checks recorded in the task doc: one real reviewer run producing a `REVIEW:` line; one
overnight run over a two-item backlog with `until` ten minutes out, ending with a morning
report and `overnight: off`; a parallel wave of two worktree nodes integrated by patch with
`--only`.

## The build, split for parallel worktrees

One task (020-build), four nodes with disjoint file sets (re-verified below), run as one wave
of four, integrated by git-workflow and verified by one loop. Two bootstrap notes for this task
only: until node B's `--only` lands, git-workflow gets the same effect by hand — `git add --
<declared files>`, then a refusal of its own if `git diff --cached --name-only` shows anything
else, then the plain checkpoint; and until node A lands, the reviewer has no `REVIEW:` line, so
KASPER applies the exit-code table by reading the reviewer's report itself.

**Old-behaviour tests** (class 3): developers cannot touch `tests/`, and the tester runs once
after integration, so each node declares the existing tests that assert the behaviour it
changes; the node's done-check deselects exactly those during the build; integration
checkpoints land under the narrow exception in §3 (a git-workflow gate run deselecting exactly
the listed tests, recorded in the task doc); the integrating tester's brief owns the list and
rewrites them to the new contract **before** running the plain gate — a red from a listed test
is the tester's to fix, never a developer re-entry. Known members now:
`tests/unit/scripts/test_codex_seat.py::test_reviewer_returns_codex_status_without_requiring_a_gate`
(A); any `tests/unit/scripts/test_debate.py` test that patches `launch`/`kill_group` on the
`debate` module (A — the developer lists the exact names in its digest); any
`tests/unit/scripts/test_git_checkpoint.py` test asserting `docs/adr/` is always refused (B);
any `tests/unit/scripts/test_project_dirs.py` test asserting the harness dir is removed (B).

| node | owner | files (each owned by exactly one node) | goal | done-check |
|---|---|---|---|---|
| A seat-loop | python-developer | `.claude/scripts/codex_seat.py`, `.claude/scripts/debate.py`, `.claude/codex/reviewer.md` | reviewer verdict line and exit codes; S9, S10, S11, S12; debate imports the process helpers | `ruff check . && mypy && pytest tests/unit --deselect <its old-behaviour tests>` |
| B overnight + git | python-developer | `.claude/scripts/overnight.py`, `.claude/settings.json`, `install.py`, `.claude/scripts/new_task.py`, `.claude/scripts/git_checkpoint.py`, `.claude/scripts/project_dirs.py`, `.claude/commands/new-task.md`, `.claude/commands/checkpoint.md`, `.claude/commands/overnight.md` | the Stop hook, `--branch` with its preconditions, `--only` by refusal, `adr_local_only` + `_entry_present` in `project_dirs.py` and the checkpoint importing it (one node, so the import and its target land together), S3 with collision-free names and the `Overnight:` print, `--status` behind, the three command files (S1 included) | same |
| C small-fixes | python-developer | `.claude/scripts/model_route.py`, `.claude/rules/model-routing.md`, `.claude/scripts/autoformat.py`, `.claude/scripts/notify.py` | S2 with its table row and S1 line, S6, S10-notify | same |
| D docs | documentor | `.claude/skills/kasper/SKILL.md`, `.claude/agents/triager.md`, `.claude/agents/*.md` (including the git-workflow step-1 exception), `.claude/rules/*.md` except `model-routing.md`, `CLAUDE.md`, `.claude/CLAUDE.md`, `README.md`, `.claude/commands/until-green.md`, `.claude/commands/permit.md` | the SKILL rewrite (loop table, task start, overnight and "end the night", waves, trigger, fallback, plain language, Ending step 0), the triager, the dedupe, D2, L8, S1 paths, the ADR-by-exclude wording, the integration-checkpoint exception | `ruff check . && mypy && pytest tests/unit` (docs change nothing the gate runs; the check proves the tree still passes) |

Disjointness, re-verified against the S1 list and every file in Modules touched: no path appears
in two rows; `commands/` splits B (`new-task`, `checkpoint`, `overnight`) / D (`until-green`,
`permit`); `rules/` splits C (`model-routing`) / D (the other seven); `install.py` is B's alone;
`project_dirs.py` moved from C to B because `git_checkpoint.py` imports the function it adds.
Ordering: none to build. D's text is written against this spec, not against A/B's code; the
integration tester run catches drift.

## Assumptions

- **A1 — verified** (researcher, 2026-10-22): a Stop hook blocks with
  `{"decision": "block", "reason": …}`; eight consecutive blocks with no tool call end the turn.
- **A2 — verified:** `/goal` as described in §2; weighed there.
- **A3 — verified, removed from the build:** `autoContinueAtUsageLimit` is on by default,
  user/managed scope only.
- **A4 — partly verified:** no "Fable limit" string is documented; the predicate in §5 keys on
  the harness's early-termination text and documented limit/overload wording, and requires the
  spawn's own tier. A new failure shape is added to the list by a documented source, never by a
  bare "limit" match.
- **A5:** `git rev-list --count HEAD..@{upstream}` presumes the mainline tracks a remote; a
  mainline with no upstream passes the "behind" check with a stderr note (nothing to be behind).

## Open questions

None remain for the human: the five from pass 1 were decided on 2026-10-22 and are folded in
above. Two choices this revision made that the human may still want to see: `overnight.py`
over `/goal` (§2, reasons in the table) and the declared old-behaviour-test list over a
tester-per-node (§The build: the old `codex_seat.py` refuses worktrees until node A lands, so a
per-node tester could not run in one).
