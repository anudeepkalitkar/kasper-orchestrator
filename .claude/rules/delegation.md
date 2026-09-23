# Rule: Delegation — route work to the agent roster; owner never verifies own work

**Route focused work to the dedicated agents instead of doing everything in the main context,
and run independent agents in parallel.** The main session stays for decisions and integration.
Inside a KASPER session the chain is law — human → KASPER → agents, one hop: agents are
subagents of that session, they cannot spawn agents or message each other, and anything
cross-discipline routes back through KASPER.

## The roster (`.claude/agents/` — the canonical list; projects may add their own)
- **python-developer** — write/refactor/debug Python to the code standards; carries FastAPI,
  SQLAlchemy 2 + Alembic, Celery, PyTorch/OCR and boto3 as sections; never touches `tests/`,
  never commits.
- **typescript-developer** — the TypeScript counterpart: Node servers (Express/NestJS),
  React 19 + Vite, Next 16; same scope limits.
- **terraform-developer** — HCL, modules, one stack per environment, `tftest`, AWS. Plans and
  reports the plan; **never applies** and never edits state; same scope limits.
- **devops-developer** — Dockerfiles, docker compose, GitHub Actions, shell scripts. Builds and
  validates locally; **never pushes an image or deploys**; same scope limits.
- **architect** — turns a stated goal into a design spec in `docs/design/` and, when the decision
  is hard to reverse, a proposed ADR in `docs/adr/`. Docs only: never production code, never tests,
  never commits; a fork it cannot settle becomes an open question and a stop-and-report.
- **tester seat (Codex)** · **code-reviewer seat (Codex)** · **arch-reviewer seat (Codex)** — not
  subagents. KASPER runs `python3 .claude/scripts/codex_seat.py tester|reviewer|arch-reviewer
  --brief <file> --out <file> [--stack <name>...]`, with the seats' role prompts in
  `.claude/codex/` and one stack section from `.claude/codex/stacks/` per stack the task touches:
  the tester authors tier-correct tests and runs the gate, the reviewer judges the diff with ranked
  findings, and the arch-reviewer judges the architect's design before it is built. The reviewer
  and the arch-reviewer edit nothing at all; the tester writes only tests.
- **documentor** — owns the written truth (`docs/`, README, and the local-only `tasks/` docs);
  docs only, never production code or tests.
- **git-workflow** — the only agent that commits, pushes, or merges; checkpoints on the Codex
  tester seat's recorded green, and merges wait on the human's explicit yes.
- **researcher** — internet research → precise, cited summary; use whenever a question needs
  up-to-date or external information rather than guessing.

## How to route
1. Owner implements; verification is a different **vendor's** seat — one Codex tester pass per
   task, then one reviewer pass. The writer never grades its own work
   ([[autonomous-workflow]]).
2. Research/unknowns → `researcher` before guessing.
3. Work inline only when it is small and self-contained enough that delegation is pure overhead.
4. **Brief completely, then budget.** Every brief carries the facts the agent needs, the exact
   files to read, and a tool-call budget — context is re-read on every turn, so a fresh,
   well-briefed agent costs less than a long-lived one that has to go looking.
5. **Every spawn carries the tier the router printed** for that brief — no agent file pins a
   model, and a spawn that passes none inherits the session's own ([[model-routing]]).

## Parallelize independent work (fan out, then integrate)
- Before any multi-part task ask: which pieces are independent? **Batch those agent calls in a
  single message** so they run concurrently; never serialize work with no dependency.
- Serialize only true dependencies (one agent's output genuinely another's input).
- Parallel agents don't see each other's work — collect, dedup, and reconcile afterward.
- Don't parallelize file-mutating agents onto the same files unless each runs in an isolated
  worktree.

Related: [[autonomous-workflow]] (owner≠verifier, one item per iteration),
[[documentation]] (task docs), [[code-standards]] (tell subagents: functions-first, walk the
ladder, no unrequested abstraction).
