# Design: debate seats for the researcher and architect roles

- **Status:** proposed design, 2026-10-14, revised the same day on the human's decisions and the
  arch-reviewer's findings — not built
- **ADR:** [ADR-0014](../adr/0014-debate-seats.md) (proposed)
- **Scope:** `researcher` and `architect` only; Claude and Codex only; a seam for a third
  vendor is named, not built

## What and why

The human's goal, in their words: when KASPER summons the researcher or the architect it should
"summon multiple agents that will bring different ideas and debate among themselves and give us a
final idea that will work — 1 Claude Fable, multiple Sonnets, 1 Codex GPT-6, multiple lower
GPTs."

Today each of those roles is one Claude subagent (`.claude/agents/researcher.md`,
`.claude/agents/architect.md`) spawned once with the Agent tool. One model, one view.

The 2026-10-14 vendor survey (session scratch, see Cost) reports the debate evidence as mixed:
heterogeneous model pools improve answers consistently; extra debate rounds do not. The design
therefore spends its calls on **vendor diversity**, keeps the **round count fixed and small**, and
**measures** the one round whose value the evidence doubts.

**Done looks like:** KASPER runs one ledger-allowed Bash call,
`python3 .claude/scripts/debate.py architect --brief <file> --out <report>`, and gets back one
report whose first lines it can parse (`DEBATE:` / `ROLE:` / `SEATS:` / `CALLS:` / `REASON:`), with
the final answer, the recorded dissent, and every seat's own output on disk beside it. A debate
never hangs, never exceeds its call cap, never leaves a seat process behind, never runs a hook, and
degrades to fewer seats with a note rather than failing when one proposer dies. Owner ≠ verifier
still holds: a debate-produced design is authored by the architect agent, judged by the Codex
arch-reviewer, and the judge that wrote the debate's answer never shares that reviewer's vendor.

## Decisions recorded (the human, to KASPER, 2026-10-14)

The first draft's open questions were put to the human; the answers are now part of the design.

1. **Author of record:** after an architect debate the architect agent is spawned with the debate
   report as its primary input and authors `docs/design/<slug>.md` (and any ADR), checking the
   report's `## Claims to verify against the code` against the code. Seats stay read-only.
2. **Critique round:** kept, and measured over the first three debates by the criterion in
   §Critique measurement; it stays or goes on that result.
3. **Roster:** judge `claude:fable`, which never proposes; proposers two `claude:sonnet`, one
   top-tier Codex model, two cheap-tier Codex models.
4. **Rollout:** `architect` first.
5. **Spend guard:** no per-debate permission prompt; `--max-calls` and the task-doc line.
6. **Claude seat settings:** the saved sign-in (non-`--bare`) with limited settings. **Hook
   isolation is a precondition**, verified by the spike and re-checked on every run (§Isolation);
   if it cannot be verified, debates stay disabled.
7. **Trigger:** KASPER debates only when it judges the question contested — criteria in
   §Trigger, as the paragraph the build task adds to SKILL.md.

## The decisions, one line each (chosen first, then rejected)

| Point | Chosen | Rejected |
|---|---|---|
| **Orchestration locus** | One script, `.claude/scripts/debate.py`, runs every call headless and hands KASPER one report — the `codex_seat.py` shape. Claude seats are `claude -p` calls on the CLI's saved sign-in. | KASPER driving rounds with batched Agent spawns and seat calls: at best three fan-out turns, but every proposal and critique (tens of thousands of tokens) lands in KASPER's context and is re-read on every later turn of the session; the script lets only the header and digest in. `claude --bare -p`: skips hooks but needs an API key — metered spend, the cost ADR-0009 removed. |
| **Protocol** | Three fixed phases: propose (every proposer, independently, same brief) → critique (every surviving proposer reads all proposals, anonymised) → judge (one seat writes the answer with dissent recorded). Calls = 2·P + 1. No loop. | Open-ended rounds: no evidence they help; unbounded cost. Propose → judge only: the comparison arm of the measurement, not the default. |
| **Judge** | Never proposes; `claude:fable`. For an `architect` debate a Codex judge is refused (exit 2): the arch-reviewer is Codex, and the author of the final answer must not share its vendor. | Codex judge: collides with the Codex reviewer. Judge also proposes: a judge grading its own entry. |
| **Roster** | `claude:sonnet` ×2, `codex:<top>` ×1, `codex:<cheap>` ×2; judge `claude:fable`. `--seat vendor:model` (repeatable) replaces the proposer list; `--judge vendor:model` replaces the judge. | A roster file or env config: a knob nobody asked for. |
| **Third vendor seam** | One `dict[str, Callable]` from vendor name to an argv-building function (`claude_argv`, `codex_argv`), plus one readiness function per vendor. Adding a vendor is one function and one entry. | A `Protocol`/ABC per vendor: single-implementation abstraction × 2. A universal wrapper CLI: a new dependency between KASPER and both vendors it already drives directly. |
| **Inputs / outputs** | Brief in the seat-brief style; outputs under `<out>.d/`; fixed header lines the script (not a model) writes, on every path that passed pre-flight. | Letting the judge write the headers: `codex_seat.py` already learned that a seat's formatting is not a verdict (`read_gate_verdict`). |
| **Sandbox** | Read-only for both roles: Codex `--sandbox read-only` with `features.multi_agent=false`; Claude `--permission-mode dontAsk --tools <read-only list> --setting-sources <isolated>` — no `Bash`, `Write`, `Edit`, or `Agent` — and a hook canary around every Claude call. | A write-enabled judge: decided against (decision 1). Accepting hook residue as a fallback: a hook (`project_dirs.py`) writes outside scratch — an envelope violation, not residue. |
| **Concurrency** | One sequential lane per vendor; lanes run concurrently. Every subprocess in its own process group, killed as a group on timeout. | Fully parallel: every seat of a vendor bursts into that vendor's shared rate pool at once. `subprocess.run(timeout=…)`: kills the shim, not the node process behind it. |
| **Rollout** | `architect` first (decision 4): both CLIs already read a repository read-only. `researcher` once Codex web search in `exec` is confirmed. | `researcher` first: `codex exec --help` shows no web-search flag on this machine. |

## Modules touched

| Path | Change |
|---|---|
| `.claude/scripts/debate.py` | **New.** The whole mechanism, functions only (listed below). Imports `NO_SUBAGENTS`, `RUN_LOG_SUFFIX`, `describe_argv` from the sibling `codex_seat.py` — the sibling import `permission_recorder.py` already does from `bash_permission_gate` (`permission_recorder.py:32`). It does **not** reuse `codex_seat.is_logged_in` (`codex_seat.py:195`), which launches the bare head `"codex"` with no resolved path; readiness here goes through the resolved executable (§Windows). |
| `.claude/debate/propose.md`, `critique.md`, `judge.md` | **New.** Phase contracts, vendor-neutral: what to produce and the exact mandatory headings (§Validation) the next phase and the script parse. |
| `.claude/debate/researcher.md`, `architect.md` | **New.** The role's discipline for a debate seat, distilled from `.claude/agents/researcher.md` §Method/§Output format and `.claude/agents/architect.md` §Standards. Not the agent files themselves — those carry Agent-tool turn discipline and file-writing a read-only seat has no use for. |
| `install.py` `OWNED_PATHS` (line 53) | **Change.** Add `"debate"` so the prompts install to `~/.claude/debate/` like `codex/`. A ninth owned path, as ADR-0009's first amendment added the eighth. |
| `.claude/skills/kasper/SKILL.md` step 3 | **Change (build task).** Insert the §Trigger paragraph. |
| `.claude/rules/delegation.md`, `CLAUDE.md` + `.claude/CLAUDE.md` | **Change (documentor).** Name the debate call beside the seat call. |
| `.claude/scripts/codex_seat.py` | **Unchanged.** Imported only. Its bare-head argv and argv-borne prompt are reported under §Windows, not fixed here. |
| `.claude/scripts/model_route.py` | **Unchanged.** Debate seats are not Agent spawns; the architect spawn that follows still routes (fable by its bounds). |
| `.claude/permissions-ledger.json` | **Unchanged** — see Ledger. |
| `tests/unit/scripts/test_debate.py`, `tests/integration/scripts/test_debate.py`, a `debate` fixture in `tests/unit/scripts/conftest.py` | **New (tester seat).** Mirrors `test_codex_seat.py` in both tiers; §Test plan. |

### `debate.py` — the functions

```
Role        = Literal["researcher", "architect"]
Phase       = Literal["propose", "critique", "judge"]
CallOutcome = Literal["ok", "timeout", "empty", "invalid", "failed"]
Status      = Literal["complete", "degraded", "failed"]
Seat        = NamedTuple(vendor: str, model: str, label: str)        # label "A".."Z", roster order
CallResult  = NamedTuple(outcome: CallOutcome, detail: str)          # detail: missing heading, exit code, seconds

parse_seat(value: str) -> tuple[str, str]           # "vendor:model"; argparse type; unknown vendor → ArgumentTypeError
default_proposers() -> list[tuple[str, str]]        # the constants below
resolve_executables(vendors: set[str]) -> dict[str, Path]      # shutil.which per vendor; a miss is reported by the caller as exit 3
vendor_ready(vendor: str, exe: Path) -> bool        # codex: `<exe> login status`; claude: `<exe> auth status`; both under READINESS_TIMEOUT_SECONDS
load_deny_rules(home: Path, project: Path) -> list[str]        # permissions.deny from both settings.json files, verbatim; empty → caller exits 2
build_prompt(phase, role, brief: Path, proposals: dict[str, str], critiques: dict[str, str], own: str | None) -> str
claude_argv(exe: Path, seat: Seat, role: Role) -> list[str]
codex_argv(exe: Path, seat: Seat, out: Path) -> list[str]
VENDOR_ARGV: dict[str, ...] = {"claude": ..., "codex": ...}  # the third-vendor seam
validate_output(text: str, phase: Phase, role: Role) -> str | None   # None when every mandatory section is present and non-empty, else the first offending heading
run_call(argv, prompt, out, log, timeout, writes_out: bool) -> CallResult   # one headless call; prompt on stdin; process group; distinct outcomes
hook_canary_snapshot(project: Path) -> tuple[frozenset[str], float | None]  # names under claude-temp/sessions/ + mtime of .git/info/exclude
hook_canary_tripped(before, after) -> bool          # pure comparison
run_phase(phase, seats, ...) -> dict[str, CallResult]   # one lane per vendor, lanes concurrent (ThreadPoolExecutor); sequential inside a lane
write_report(out, role, status, seats, results, calls_made, calls_cap, reason: str | None, body: str) -> None
run_debate(role, brief, out, proposers, judge, max_calls, timeout, dry_run) -> int
parse_args(argv) -> argparse.Namespace
main(argv) -> int                                    # prints `out` last on every path, like codex_seat.main
```

Constants: `DEFAULT_CLAUDE_PROPOSER = "sonnet"`, `DEFAULT_JUDGE = ("claude", "fable")`,
`DEFAULT_CODEX_TOP`, `DEFAULT_CODEX_CHEAP` (the ids the vendor survey reports as the current top
and cheap Codex tiers — **assumption**; the spike confirms them against the CLI),
`DEFAULT_MAX_CALLS = 11`, `DEFAULT_TIMEOUT_SECONDS = 900`, `READINESS_TIMEOUT_SECONDS = 30`,
`QUORUM_PROPOSALS = 2`, `QUORUM_VENDORS = 2`, `ROLE_TOOLS = {"architect": "Read,Grep,Glob",
"researcher": "WebSearch,WebFetch,Read"}`, `SEAT_DIR_SUFFIX = ".d"`,
`CLAUDE_SETTING_SOURCES: str | None = None` (§Isolation — the build task sets it to the value the
spike verified; `None` means debates are disabled), `REQUIRED_SECTIONS` (§Validation).

## Data and flows

### The command

```
python3 .claude/scripts/debate.py researcher|architect --brief <brief.md> --out <report.md>
        [--seat vendor:model ...] [--judge vendor:model] [--max-calls N] [--timeout SECONDS]
        [--no-critique] [--dry-run]
```

Run from the project root, like `codex_seat.py`; a cwd without `.git` is refused (exit 2). `--brief`
and `--out` are relative to it. `--dry-run` prints every planned call (`describe_argv` form, one
per line, phase by phase) and writes nothing. `--no-critique` exists for the measurement in
§Critique measurement only and is removed by the task that closes it.

### Pre-flight (before any model call; nothing is written yet)

1. cwd has `.git`; role/seat/judge arguments parse; `architect` with a `codex:` judge → exit 2;
   roster calls `2·P + 1` (or `P + 1` with `--no-critique`) ≤ `--max-calls` → else exit 2.
2. `CLAUDE_SETTING_SOURCES is None` and any Claude seat in the roster → exit 2,
   "debates disabled: Claude seat isolation not verified". `load_deny_rules()` finds no
   `permissions.deny` entry in `~/.claude/settings.json` or the project's `.claude/settings.json`
   → exit 2, "no sensitive-read denies to pass to the Claude seat" (§Isolation).
3. `--out` is writable: create `<out>.d/`, open `<out>` for writing and truncate it (a leftover
   report from an earlier run must never certify this one — the `codex_seat` lesson), and delete
   the previous run's per-call files in `<out>.d/` (`propose-*.md`, `critique-*.md`, `judge.md`,
   their `.log`s) so the directory holds only what this run writes. Any `OSError` → exit 2 with
   the path and error on stderr.
4. `resolve_executables` for every vendor in the roster; `vendor_ready` for each with the
   `READINESS_TIMEOUT_SECONDS` deadline. A miss, a non-zero status, or a readiness timeout → exit 3
   naming the vendor, **report written** (`DEBATE: failed`, `REASON: vendor <name> not resolved |
   not signed in | readiness timed out after 30s`). A vendor missing at pre-flight is never worked
   around by a single-vendor debate: heterogeneity is the one thing the evidence says pays, and
   the fix (sign in) is cheap and the human's.

**One contract:** once step 3 has succeeded, every exit writes a report (§The report) — readiness
failure, canary trip, quorum failure, judge failure, degraded and complete alike. Before step 3
nothing has been probed, so nothing is written and `main` only prints `out`.

### The brief (KASPER writes it, seat-brief style)

Same shape as a seat brief: the question or goal in the human's words, what "done" looks like,
the exact files or sources to read, constraints, and the scratch path. It never names `--out`
(a lesson from the seat briefs). The script passes it verbatim under `## Brief`.

### Prompt per phase

```
<phase file>                          .claude/debate/<phase>.md
## Role: <role>                       .claude/debate/<role>.md
## Brief                              the brief, verbatim
## Proposals        (critique, judge) ### Seat A … ### Seat E — anonymised; the critic's own is marked "(yours)"
## Critiques        (judge only)      ### Seat A on the proposals …   (valid critiques only)
```

Vendor and model names never appear inside a prompt; seats are `Seat A`…`Seat E` so a critic
judges content, not provenance. The script appends the label → `vendor:model` mapping to the
report after the judge has written.

### The call, per vendor

| Vendor | argv (prompt on **stdin**, never argv; head is the `shutil.which`-resolved path) | Output |
|---|---|---|
| `codex` | `<codex> exec -c features.multi_agent=false --sandbox read-only -m <model> -o <out>` | final message at `<out>` (Codex writes it); stdout+stderr to `<out>.log` |
| `claude` | `<claude> -p --model <model> --output-format text --permission-mode dontAsk --tools <ROLE_TOOLS[role]> --no-session-persistence --setting-sources <CLAUDE_SETTING_SOURCES> --disallowedTools <every permissions.deny rule, verbatim>` | stdout is the final message; the script writes it to `<out>`; stderr to `<out>.log` |

Flags verified against the CLIs on this machine (`codex exec --help`: `-c`, `-m`, `-s read-only`,
`-o`, prompt from stdin when no positional; `claude --help`: `-p`, `--model`, `--output-format`,
`--permission-mode` with choice `dontAsk`, `--tools`, `--no-session-persistence`,
`--setting-sources`, `--disallowedTools`). Whether `--disallowedTools` accepts permission-rule
syntax such as `Read(~/.ssh/**)` is an **assumption** until the spike's denied-read check
(§Isolation). What `dontAsk` does at runtime ("denies anything that would prompt") is the
vendor survey's reading of the headless docs — **assumption** until the spike runs one call.

### `run_call` — the call contract

Every call is a `subprocess.Popen` in its own process group (`start_new_session=True` on POSIX,
`creationflags=CREATE_NEW_PROCESS_GROUP` on Windows), stdin = the prompt, stdout/stderr as the
table says, driven by `communicate(input=prompt, timeout=timeout)`.

**Fresh output per call.** Immediately before each call the script deletes that call's `<out>` and
`<out>.log` (`unlink(missing_ok=True)` — the same move `codex_seat.py` makes before each run,
`out.unlink(missing_ok=True)` at line 325) and records the call's start time. After the call, a
file that is missing, blank, or whose mtime predates that start is `empty` — a Codex call that
exits 0 without writing can therefore never be certified by a file an earlier call or an earlier
run left behind, and a reused `<out>.d/` can never supply a proposal, a critique, or an answer.

Outcomes are distinct and exhaustive, classified **in this order** — the first rule that applies
wins, so no combination is ambiguous:

| Order | Outcome | Rule | `detail` |
|---|---|---|---|
| 1 | `failed` | `Popen` raised `OSError` (not launchable) | the `OSError` text |
| 2 | `timeout` | `communicate` raised `TimeoutExpired`; the **whole process group** was killed (`os.killpg(…, SIGKILL)` on POSIX; `<taskkill> /T /F /PID <pid>` via the resolved `taskkill` on Windows) and drained; partial transcript stays in `.log` | `"<timeout>s"` |
| 3 | `failed` | exit code non-zero — **regardless of what the output file holds**; a seat that reports failure is not trusted on content | the exit code |
| 4 | `empty` | exit 0, output file missing, whitespace-only, or not rewritten since the pre-call delete (mtime < call start) | `"no output"` / `"stale output"` |
| 5 | `invalid` | exit 0, output present, `validate_output` named a missing, duplicated, or empty mandatory section | the heading |
| 6 | `ok` | exit 0, fresh output, `validate_output` returned `None` | elapsed seconds |

For the judge this means: rule 2 → exit 4; rules 1, 3, 4, 5 → exit 5 with the `detail` in
`REASON:`. Nothing about judge failure is left to invent at build time.

Timeout kills the group, not the direct child, because both CLIs are reached through npm shims
(a `.cmd` on Windows, a node wrapper elsewhere): killing only the shim is what leaves a seat
running after the debate has moved on. The upper bound on wall-clock is therefore
`(calls in the longest lane) × --timeout` plus readiness; the design makes no softer promise.

### Validation — mandatory sections

`validate_output` splits the text at lines beginning `## `, requires each heading in
`REQUIRED_SECTIONS[phase]` (plus the role-specific one) to appear **exactly once** as a whole
line, and requires at least one non-blank line in its body before the next `## ` heading.
The first offending heading is the `detail`; checks run in table order.

| Phase | Mandatory headings (exact lines) |
|---|---|
| propose | `## Proposal` · `## Why this works` · `## Risks and unknowns` · researcher adds `## Sources` |
| critique | `## Critique` (one `### Seat <L>` sub-heading per other proposal) · `## Revised position` |
| judge | `## Answer` · `## Why this over the alternatives` · `## Dissent` · `## Critique effect` · researcher adds `## Sources`, architect adds `## Claims to verify against the code` |

`## Dissent` may legitimately record convergence, but it must say so in words ("No dissent
recorded: every seat converged on …") — an empty dissent is `invalid`, because recording
disagreement is what the debate exists to do. `## Critique effect` is the judge naming which
proposal errors or omissions the critiques caught and the answer incorporates, or "none" in words;
it is the raw material of §Critique measurement. The phase prompt files list these headings
verbatim; a seat that cannot follow its one formatting rule has not shown it did the work
(`codex_seat.py`'s reasoning for `GATE:`, kept).

### Sequence of one debate

```mermaid
sequenceDiagram
    participant K as KASPER (one Bash call)
    participant D as debate.py
    participant C as claude lane (sequential; canary around each call)
    participant X as codex lane (sequential)
    K->>D: role --brief --out [--seat…] [--judge]
    D->>D: pre-flight 1–3 (args, isolation configured, out writable) → exit 2 on failure
    D->>D: pre-flight 4 (resolve + readiness, 30 s deadline) → exit 3 on failure
    par propose
        D->>C: Seat A, Seat B (propose.md + role + brief)
        D->>X: Seat C, Seat D, Seat E
    end
    D->>D: canary tripped → failed/exit 3 · < 2 ok proposals or < 2 vendors → failed/exit 5
    par critique (ok proposers only; skipped with --no-critique)
        D->>C: A, B read A…E
        D->>X: C, D, E read A…E
    end
    D->>C: judge (fable): judge.md + role + brief + ok proposals + ok critiques
    D->>D: judge timeout → failed/exit 4 · judge empty/invalid/failed → failed/exit 5
    D->>D: write_report (always): header + body + ## Seats
    D-->>K: stdout = header lines + first lines of the answer, then the report path
```

### Output layout

```
<scratch>/reports/<task>-debate.md          the report KASPER reads — always present after pre-flight
<scratch>/reports/<task>-debate.md.d/       every seat's own output and transcript
    propose-A.md  propose-A.md.log  …  propose-E.md
    critique-A.md … critique-E.md
    judge.md      judge.md.log
```

`<out>.d/` is never trusted across runs: pre-flight step 3 removes the previous run's per-call
files, and each file is deleted again immediately before its own call (§`run_call`). What the
directory holds after a run is exactly what this run's calls wrote.

### The report (header written by the script, never by a model)

```
DEBATE: complete | degraded | failed
ROLE: architect
SEATS: proposed 5/5 · critiqued 4/5 · judge claude:fable ok
CALLS: 10/11
REASON: <one line — present only when DEBATE: failed>

<judge body — the mandatory sections above — or, when failed, the line "No answer: see REASON."
 and the list of seat files that do exist>

## Seats
- A claude:sonnet — propose ok (212s) · critique ok (301s)
- B claude:sonnet — propose ok · critique timeout (900s)
- C codex:<top> — propose invalid (missing `## Risks and unknowns`) · critique not run
- …
```

The judge field of `SEATS:` is one of `ok | timeout | empty | invalid | failed | not run`.
KASPER parses line 1 and, when it is `failed`, line 5. `write_report` runs on every path after
pre-flight step 3; if writing the report itself raises `OSError` mid-run, the error and the
`<out>.d/` path go to stderr, the exit code is 2, and `main` still prints `out` last.

### Exit codes (parallel to `codex_seat.py`)

| Code | `DEBATE:` | Meaning |
|---|---|---|
| 0 | complete | every seat `ok`, judge `ok` |
| 1 | degraded | quorum met, judge `ok`, at least one proposer not `ok` in some phase — `## Seats` says which and why |
| 2 | — (no report) or failed | bad arguments: unknown vendor, roster over `--max-calls`, `architect` with a `codex` judge, unreadable prompt/brief, cwd not a repository, `--out` unwritable, isolation not configured; or a report write failed mid-run |
| 3 | failed | a vendor unusable: not on PATH, readiness non-zero or past its 30 s deadline (report written, `REASON: vendor <name> not resolved / not signed in / readiness timed out after 30s`); **or** the hook canary tripped after a Claude call (report written, `REASON: claude seat executed hooks — isolation broken`) |
| 4 | failed | the judge call timed out and its process group was killed — proposals and critiques stay on disk |
| 5 | failed | quorum not met after propose (`REASON: <n> valid proposals from <m> vendors`), or the judge outcome was `empty`, `invalid` (`REASON: judge output missing <heading>`) or `failed` |

### Failure path, in prose

- A **proposer** with any outcome other than `ok` is dropped from the critique phase and from the
  judge's inputs; the loss and its outcome are recorded in `## Seats`; the debate continues.
  Nothing is retried — a retry into a rate-limited pool is the hang this design forbids.
- A **critic** with any outcome other than `ok` keeps its proposal in the judge's inputs; only its
  critique is absent. Status becomes `degraded`.
- **Quorum** is two `ok` proposals from two vendors. Below it there is no debate to judge: exit 5,
  report written with `REASON:`, no critique or judge call made. KASPER takes the single-agent
  path for the role.
- The **judge** is the one call with no substitute: `timeout` → exit 4; `empty`/`invalid`/`failed`
  → exit 5; in both cases the report carries the header, the reason, and the list of seat files
  that exist. KASPER may hand the surviving `propose-*.md`/`critique-*.md` to a normal spawn — they
  are ordinary files — but a `failed` debate is never retried by the script.
- The **hook canary** (§Isolation) is checked after every Claude call. Tripped → no further calls,
  report written as `failed`, exit 3. Debates stay disabled until the spike re-verifies isolation.
- Every call runs with stdin = prompt, a per-call `--timeout`, its own process group, and a
  transcript streamed to disk so a killed run leaves evidence (`codex_seat.py`'s reasoning, kept).

## Isolation — the precondition for any Claude seat

A non-`--bare` `claude -p` loads settings, and settings carry the hooks. This machine's
SessionStart hook (`project_dirs.py`) writes `claude-temp/sessions/<id>/`, edits
`.git/info/exclude`, and wires the harness memory path under the home directory — a write outside
the session's scratch, which the boundaries rule forbids for anything a debate does. Running a
seat that executes hooks is therefore not residue to accept; it is an envelope violation. The
design makes isolation a precondition in three layers:

1. **Configured.** `CLAUDE_SETTING_SOURCES` is the `--setting-sources` value under which no hook
   runs. It ships as `None`; the **spike** finds the value (candidates: an empty list, or a source
   set that excludes both the user and project settings files) and the build task sets the
   constant only once the spike has shown, for one real `claude -p` call from this project root:
   no new entry under `claude-temp/sessions/`, `.git/info/exclude` unchanged (mtime and content),
   no change under `~/.claude/projects/<this project's slug>/` (mtime; a read of the home directory
   is allowed, a write is what is being ruled out), **and a denied read**: in the same call the
   seat is asked to read a canary file the human has placed by hand under a directory the deny
   list covers (an empty `~/.ssh/kasper-deny-canary`, removed by the human afterwards), and the
   transcript must show that read refused. If no value achieves all four, the constant stays
   `None`, pre-flight step 2 refuses every roster with a Claude seat, and **debates stay disabled**
   — the human decided this explicitly.
2. **Checked at runtime.** `hook_canary_snapshot` before each Claude call and after it;
   `hook_canary_tripped` compares the `claude-temp/sessions/` name set and the exclude file's
   mtime. A trip is terminal (exit 3) — the configured value has stopped working (a CLI update,
   a settings change) and the script must not make the next call.
3. **Recorded.** The spike's evidence (the value, the three checks, the CLI version) goes in the
   build task's doc and in a comment beside the constant, so the next CLI version is re-verified
   deliberately, not discovered by a canary trip mid-debate.

4. **Denies travel with the seat.** Narrowing `--setting-sources` drops not only the hooks but the
   `permissions.deny` backstop those same settings files carry — the credential paths (`~/.ssh`,
   `~/.aws`, and the rest) a seat's `Read`/`Grep`/`Glob` must never reach. `load_deny_rules()`
   therefore reads `permissions.deny` from `~/.claude/settings.json` and the project's
   `.claude/settings.json` (stdlib `json`; either file may be absent, both absent is exit 2) and
   passes every rule verbatim as `--disallowedTools` on each Claude call, so the seat enforces the
   same list the session does without a second copy that could drift. Rules for tools the seat does
   not have (`Bash(…)`) are harmless passengers. The spike's denied-read check (point 1) is what
   proves the flag honours rule syntax before any seat is enabled.

What `--setting-sources` does to CLAUDE.md and auto-memory loading is recorded by the spike but
is not part of the precondition: those are reads into a seat's context, the seat's output stays in
local scratch, and the author of record (the architect agent) writes `docs/`. The spike notes
whether the chosen value also drops them.

## Trigger — the paragraph the build task adds to SKILL.md step 3

> **Debate only when the question is contested — never by default.** Before an `architect` spawn
> (and a `researcher` spawn once that role is enabled), run a debate —
> `python3 .claude/scripts/debate.py <role> --brief <file> --out <scratch>/reports/<task>-debate.md`
> — only when at least **two** of these hold: you can name two or more plausible answers a
> competent engineer would defend; the decision is hard to reverse (it would get an ADR); a
> previous single-agent answer to this question was rejected by the human or drew a P1 from a
> seat; the human asked for a debate. Never debate a one-file obvious design, a lookup with one
> authoritative source, or a follow-up to a debate already run on the same question. Pass the
> report path as the primary input of the spawn that follows; copy the report's header line into
> the task doc; a `DEBATE: failed` report means the single-agent path, not a retry.

## Boundaries

- **Writes:** only `--out`, `<out>.log`, and `<out>.d/*` — under the session scratch dir KASPER
  names. Seats write nothing: Codex is `read-only`; Claude has no `Write`/`Edit`/`Bash`, runs no
  hooks, and carries the session's `permissions.deny` list as `--disallowedTools` so the
  credential-path reads stay refused with the settings narrowed (§Isolation).
- **Network:** the researcher's Claude seats read the web through `WebSearch`/`WebFetch`; Codex
  seats need `codex exec` web search (unconfirmed — rollout note). Those are reads. No seat can
  POST: no `Bash`, no `curl`, Codex sandboxed. Both vendors' calls use the CLIs' saved sign-ins —
  the "calls the project's own code makes with already-configured credentials" exception.
- **Secrets:** nothing is passed on argv; prompts go over stdin; transcript logs stay in scratch,
  which is git-excluded.
- **One hop:** seats never address each other; the script passes files. Codex cannot spawn
  sub-threads (`features.multi_agent=false`); the Claude tool list excludes `Agent`.
- **Owner ≠ verifier:** architect debate → the architect agent authors `docs/design/<slug>.md`
  from the report (decision 1) → the Codex arch-reviewer judges it. The judge (Claude) and the
  reviewer (Codex) differ by vendor; the script enforces this with the architect/codex-judge
  refusal. A researcher debate has no reviewer seat today; its report is cited, the critique phase
  instructs every critic to open and check the other seats' citations, and KASPER treats the
  result exactly as it treats a researcher digest — a claim, until a different agent verifies it.
- **Permissions the project does not already have:** none — see Ledger.

## Config surface

Exactly the flags in §The command. No roster file, no env variables, no per-role overrides. The
defaults are constants at the top of `debate.py`; changing a default is a code change with the
table in this spec mirrored in the same commit, as `model_route.py` does with its rule.
`--no-critique` is temporary (§Critique measurement).

## Critique measurement — how the five critique calls earn their place

The evidence says rounds may not help; the human kept the round on condition that it is measured.
Over the **first three architect debates**:

1. KASPER runs each debate twice on the same brief, same roster: once as designed, once with
   `--no-critique` (P + 1 calls). Both reports land in scratch.
2. KASPER hands the two `## Answer` sections — labelled only `Answer 1`/`Answer 2`, order chosen by
   coin flip per debate and recorded in the task doc — to the **arch-reviewer seat** with the
   brief and one question: rank the defects in each answer (`P1`/`P2`/`P3`, the seat's normal
   shape), judging each against the code and the rules as it would a design.
3. **Criterion:** the critique round **stays** if, in at least two of the three debates, the
   with-critique answer has strictly fewer `P1 + P2` findings than the without-critique answer. A
   tie in a debate counts against the round (it cost five calls and changed nothing the reviewer
   could see). Otherwise the round **goes**: the critique phase and `--no-critique` are deleted,
   the protocol becomes propose → judge, and this spec and ADR-0014 are amended in that commit.
   If the round stays, `--no-critique` is deleted and the judge's `## Critique effect` remains the
   standing record of what the round contributed.
4. Cost of the measurement: three extra propose-only runs (6 calls each) and three reviewer seat
   calls, recorded in the task doc like any debate.

The reviewer is Codex and the judge is Claude, so the measurement never asks a vendor to grade
its own answer.

## Cost

- Calls per debate: `2·P + 1`; default roster P = 5 → **11 calls**: 5 Claude (2 Sonnets × propose
  and critique, plus the Fable judge) and 6 Codex (3 seats × two phases). `--max-calls` (default
  11) is checked before the first call; a roster that would exceed it is exit 2. KASPER lowers it
  to shrink a debate (`--max-calls 7` fits P = 3) and names the roster accordingly.
- Both vendors' calls draw on the CLIs' saved sign-ins, the same pools the Codex seats and
  KASPER's own session already use — the Claude seats share **KASPER's** pool. One lane per vendor
  keeps the burst to one call at a time per pool.
- **Task doc, one line per debate**, copied from the header:
  `- debate(architect): 5 proposers (2 claude, 3 codex) · judge claude:fable · CALLS 10/11 · DEBATE: degraded · <report path>`
- The pricing and rate-pool reasoning is deliberately not here (public repo, ADR-0003): it is in
  the session scratch report `claude-temp/sessions/c5a3943f-469f-48e5-aa32-d3c9366c65e0/reports/015-researcher-multi-vendor.md`, local-only.

## Windows

Both CLIs run on Windows. The script must:

- resolve `claude`, `codex`, and (on Windows) `taskkill` with `shutil.which()` **once, in
  pre-flight**, and run the resolved paths everywhere — readiness checks, seat calls, and the
  timeout kill alike. npm installs `.cmd` shims there, and `CreateProcess` does not apply
  `PATHEXT` the way a shell does; never `shell=True`;
- pass prompts on **stdin**: a critique prompt carrying five proposals is far past Windows' 32,767-
  character command-line limit;
- start every call in its own process group and kill the group on timeout (`taskkill /T /F` on
  Windows, `os.killpg` elsewhere) — a `.cmd` shim killed alone leaves node running;
- open every file with `encoding="utf-8"` (the default there is a legacy code page);
- use `pathlib`, build `<out>.d` and `<out>.log` by string suffix as `codex_seat.py` does, write
  only under the project root — no `/tmp`, no `$TMPDIR`;
- use threads (`concurrent.futures`) for the lanes, never `fork`; no ANSI escape assumptions in
  what it prints; no shell operators anywhere in an argv.

Reported, not fixed here: `codex_seat.py` passes the composed prompt as an argv element
(`codex_argv`, prompt last), runs the bare head `"codex"` in both `codex_argv` and `is_logged_in`
(`codex_seat.py:195`), and uses `subprocess.run(timeout=…)`, which kills the shim only. Nothing
on Windows has been run by a human since the port (known debt).

## Ledger and permissions — none proposed (decision 5)

- `python3` is a seeded `allow_keys` entry ("dev toolchain"), so
  `python3 .claude/scripts/debate.py …` auto-runs today, exactly as `codex_seat.py` does. The
  `claude` and `codex` subprocesses are not Bash-tool commands and the gate never sees them; the
  `claude`/`codex` heads in this machine's `grants` were recorded for `--version` checks and are
  not needed.
- No widening and no new guard: the human decided against a per-debate prompt. The spend record
  is `--max-calls` plus the task-doc line.

## Test plan by tier

| Tier | Proves | Command |
|---|---|---|
| `tests/unit/scripts/test_debate.py` — no I/O; `subprocess.Popen`, `shutil.which`, and `Path` methods faked as `test_codex_seat.py` fakes `subprocess.run` | `parse_seat` accepts `vendor:model`, refuses unknown vendors; `build_prompt` orders phase → role → brief → anonymised proposals, marks the critic's own "(yours)", never includes a vendor name, includes only `ok` proposals/critiques; `claude_argv`/`codex_argv` use the resolved path as head, carry the read-only flags, `--tools` per role, `--setting-sources`, and no prompt on argv; `validate_output` accepts the full set of mandatory headings per phase/role and names the first missing, duplicated, or empty one; `hook_canary_tripped` on a new session name, on an exclude mtime change, and on no change; `run_call` deletes the per-call file before launching and maps each fake-process behaviour to its outcome in precedence order (`OSError` on start → `failed`; `TimeoutExpired` → `timeout` with a group kill called; non-zero exit even with a valid-looking file → `failed`; exit 0 + no file, or a file whose mtime predates the call → `empty`; exit 0 + missing heading → `invalid`; exit 0 + fresh valid file → `ok`); `load_deny_rules` merges both settings files, tolerates one missing, and an empty result is exit 2 at pre-flight; `claude_argv` carries every deny rule as `--disallowedTools`; a readiness failure after `--out` was probed produces a `failed` report with the vendor in `REASON:`; pre-flight: `architect` + `codex` judge → 2, roster over cap → 2, `CLAUDE_SETTING_SOURCES is None` with a Claude seat → 2, unwritable `--out` → 2, vendor not resolved/not ready → 3 before any call; one proposer non-`ok` → `degraded`/exit 1 with `## Seats` naming the outcome; one `ok` proposal → `failed`/exit 5 with `REASON:`; judge `timeout` → 4, judge `invalid` → 5 with the heading in `REASON:`; canary trip → `failed`/exit 3 and no further `Popen`; header lines exact; `main` prints `out` last on every path | `ruff check . && mypy && pytest tests/unit` |
| `tests/integration/scripts/test_debate.py` — real temp project (`.git/`, `claude-temp/sessions/`), real files, **controlled real subprocesses** (small Python children standing in for the CLIs) | `--dry-run` prints every planned call for the default roster in phase order and leaves `<out>`, `<out>.d/` absent; a full run with children that echo valid sections produces the `<out>.d/` layout, a `.log` per call, the report header, and the label mapping; a child that spawns a grandchild sleeping past `--timeout` → outcome `timeout`, **and the grandchild is gone** (poll its pid) — the group kill works; a readiness child that sleeps past `READINESS_TIMEOUT_SECONDS` → exit 3 within the deadline and no seat process started; a child that creates `claude-temp/sessions/<new>/` → canary trip, `failed`, exit 3, no further children launched; a leftover `<out>` from an earlier run is truncated at pre-flight and cannot certify a run that writes nothing; **a reused seat directory** pre-seeded with a previous run's valid `propose-*.md`, `critique-*.md` and `judge.md`, run with children that exit 0 without writing, yields `empty` for every call, no quorum, `failed`/exit 5 and a `<out>.d/` holding none of the stale files — never a stale answer | `pytest tests/integration/scripts/test_debate.py` |
| e2e | The spike's isolation run (§Isolation step 1) and one real architect debate on a small question against both signed-in CLIs; exit code and header checked by hand. Interactive-only: it spends real calls and needs both sign-ins. | run by the human, not a gate step |

## Rollout

1. **Spike** (python-developer, `--dry-run` + one real call per vendor): find and record the
   `--setting-sources` value that passes the three isolation checks (§Isolation) — **no value, no
   debates**; confirm the Codex model ids; confirm `codex exec` with the prompt on stdin and `-o`
   behaves as `codex_seat` expects; confirm what `claude -p … --permission-mode dontAsk` does when
   a tool would prompt; confirm the group kill leaves no `node` process behind on this machine.
2. **`architect` debates** go live (decision 4). The architect agent authors from the report; the
   arch-reviewer judges as today. The first three run the measurement (§Critique measurement).
3. **`researcher` debates** once Codex web search in `exec` is confirmed; until then a researcher
   debate's Codex seats would argue from memory while the Claude seats fetch sources.
4. Docs in the same change: SKILL.md (§Trigger paragraph), the delegation rule, both CLAUDE.md
   twins, and `install.py`'s owned path.

## Assumptions (unverified by this design — stated so a reviewer can check them)

- The Codex model ids and tiers are those the 2026-10-14 vendor survey reports; the CLI's `-m`
  accepts them.
- `--permission-mode dontAsk` denies rather than hangs when a tool would prompt (survey reading).
- `claude -p --output-format text` writes only the final message to stdout.
- `codex exec` can reach the web from `exec` via a config key; no flag appears in `--help`.
- Some `--setting-sources` value drops every hook while keeping the saved sign-in. **Not assumed
  true** — it is the spike's precondition; if false, debates stay disabled.
- `--disallowedTools` accepts the permission-rule syntax `permissions.deny` uses (`Read(~/.ssh/**)`)
  — proved or disproved by the spike's denied-read check; disproved means debates stay disabled.
- npm's Windows shims for both CLIs are `.cmd` files `shutil.which` resolves, and `taskkill /T`
  reaches the node process behind a shim.

## Open questions

None. The seven forks of the first draft were decided by the human on 2026-10-14 and are recorded
under §Decisions recorded; the arch-reviewer's findings on that draft are settled in the sections
they named (§Isolation, §`run_call`, §The report, §Validation, §Windows, §Test plan) and in
ADR-0014's Alternatives.
