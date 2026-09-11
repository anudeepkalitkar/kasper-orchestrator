# Rule: Delegation — route work to the agent roster; owner never verifies own work

**Route focused work to the dedicated agents instead of doing everything in the main context,
and run independent agents in parallel.** The main session stays for decisions and integration.
Inside a KASPER session the chain is law — human → KASPER → agents, one hop: agents are
subagents of that session, they cannot spawn agents or message each other, and anything
cross-discipline routes back through KASPER.

## The roster (`.claude/agents/` — the canonical list; projects may add their own)
- **python-developer** — write/refactor/debug Python to the code standards; never touches
  `tests/`, never commits.
- **node-developer** — the TS/JS counterpart (server and client); same scope limits.
- **tester** — authors tier-correct tests and proves changes work; the default **verifier**.
- **documentor** — owns the written truth (`docs/`, README, and the local-only `tasks/` docs);
  docs only, never production code or tests.
- **git-workflow** — the only agent that commits, pushes, or merges; re-runs the gate itself
  before committing, and merges wait on the human's explicit yes.
- **code-reviewer** — reviews a diff/branch against the rules; ranked findings, doesn't edit.
- **researcher** — internet research → precise, cited summary; use whenever a question needs
  up-to-date or external information rather than guessing.

## How to route
1. Owner implements, a **different** agent verifies — the writer never grades its own work
   ([[autonomous-workflow]]).
2. Research/unknowns → `researcher` before guessing.
3. Work inline only when it is small and self-contained enough that delegation is pure overhead.

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
