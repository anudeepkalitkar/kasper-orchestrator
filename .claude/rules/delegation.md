# Rule: Delegation — route work to the agent roster; owner never verifies own work

**Route focused work to the dedicated agents and run independent agents in parallel.** The main
session stays for decisions and integration. Chain law: human → KASPER → agents, one hop; agents
cannot spawn agents, message each other, or ask the human anything.

## The roster (`.claude/agents/` — canonical; projects may add their own)
- **python-developer** (carries FastAPI, SQLAlchemy 2 + Alembic, Celery, PyTorch/OCR, boto3),
  **typescript-developer** (Node servers, React 19 + Vite, Next 16), **terraform-developer**
  (HCL, `tftest`, AWS — plans, never applies), **devops-developer** (Dockerfiles, compose, GitHub
  Actions, shell — builds locally, never pushes an image or deploys). None touches `tests/` or
  commits.
- **triager** — turns a goal into a plan tree (nodes with files, dependencies, done-checks) and
  says whether an architect is needed; plans only.
- **architect** — a design spec in `docs/design/` and, for a hard-to-reverse decision, a proposed
  ADR; docs only. Spawned only for a new data model, a new external contract, three or more
  components, or on the human's ask.
- **tester**, **code-reviewer**, **arch-reviewer** — Codex seats, not subagents:
  `python3 "$HOME/.claude/scripts/codex_seat.py" tester|reviewer|arch-reviewer --brief <file> --out
  <file> [--stack <name>...]` (role prompts in `.claude/codex/`, one stack section per `--stack`).
  The tester writes only tests; the reviewer and arch-reviewer edit nothing.
- **debate (Claude + Codex)** — only for a contested question (the `/kasper` skill's step 3):
  `python3 "$HOME/.claude/scripts/debate.py" architect --brief <file> --out <file>`; its report
  feeds the architect.
- **documentor** — `docs/`, README and the local-only `tasks/` docs; docs only.
- **git-workflow** — the only agent that commits, pushes, or merges; merges wait on the human's yes.
- **researcher** — external facts, cited; reads only.

(In the KASPER source repo the scripts run as `python3 .claude/scripts/<x>.py`.)

## How to route
1. The owner implements; verification is a different **vendor's** seat, in a bounded loop
   ([[autonomous-workflow]]). The writer never grades its own work.
2. Research or unknowns → `researcher` before guessing; trivial self-contained work inline.
3. **Brief completely, then budget:** the facts, the exact files to read, the scratch dir, a
   tool-call budget — turns are what cost money, so a well-briefed fresh agent beats one that
   goes looking.
4. **Every spawn carries the routed tier** ([[model-routing]]) — a spawn with none inherits the
   session's own, the most expensive.

## Parallelize independent work
Batch independent agent calls in **one message**; serialize only true dependencies. Parallel
agents don't see each other's work — reconcile afterward. File-mutating agents never share a tree:
each runs in its own worktree, and only its declared files are staged.

Related: [[autonomous-workflow]], [[documentation]], [[code-standards]] (tell subagents:
functions-first, walk the ladder).
