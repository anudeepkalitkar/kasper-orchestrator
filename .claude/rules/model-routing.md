# Rule: Model Routing — the tier is picked per spawn, never pinned in a file

**Every subagent spawn carries a model tier that a script picked from that spawn's brief —
agent files pin no model at all.** Cost then tracks task complexity instead of the habit of
whoever wrote the agent definition, and because the four tiers are aliases — `haiku`,
`sonnet`, `opus`, `fable`, each resolving to the newest model of its tier — a new model ships
and nothing here needs editing.

## The command
Before every Agent-tool spawn, KASPER runs:

```bash
python3 .claude/scripts/model_route.py --agent <name> --brief <file>   # or: … --agent <name> < brief
```

Stdout is exactly one tier; pass it as the Agent tool's `model` override. `--explain` adds six
lines on **stderr** — `size`, `novelty`, `ambiguity`, `risk`, `total: N -> <tier>`,
`bounds: <agent> [floor, ceiling] -> <tier>` — so stdout stays bare. Exit 0 means a tier was
printed; exit 2 means bad arguments or an unreadable brief.

## The rubric — one table, quoting the script's constants
| What it scores | The exact values | Effect |
|---|---|---|
| **size** — distinct file paths named in the brief (a token holding `/`, or ending `.py .md .json .toml .yaml .yml .ts .tsx .js .jsx .sh .sql .txt .cfg .ini .html .css`) | ≤ 1 path · ≤ 3 · ≤ 8 · more | 0 · 1 · 2 · 3 |
| **size** — brief length | longer than 2500 characters | +1 |
| **novelty** | new, create, design, from scratch, architecture, introduce, greenfield | +2 when any appears |
| **novelty** — routine work, only when no novelty word appeared | fix, rename, typo, bump, docstring, comment, reword, format | −1 |
| **ambiguity** | investigate, debug, root cause, unclear, decide, choose, options, why does, intermittent, flaky | +1 each, capped at 3 |
| **risk** | migration, auth, security, concurrency, race, async, crypto, payment, delete, data loss, production, terraform, infra, permission, ledger | +2 each, capped at 6 |
| **total → tier** (total is floored at 0) | ≤ 1 · ≤ 5 · ≤ 11 · above 11 | haiku · sonnet · opus · fable |
| **bounds** — `architect` | floor opus, ceiling fable | tier raised to opus if lower |
| **bounds** — `git-workflow`, `researcher` | floor haiku, ceiling sonnet | tier lowered to sonnet if higher |
| **bounds** — every other agent | floor haiku, ceiling fable | unchanged |

Each phrase counts **once**, matched case-insensitively on word boundaries — so `async` does
not match `asyncio`, and a word repeated ten times still scores once.

## Overrides
- **A spawn never omits the tier.** A spawn with no `model` override inherits the session's own
  model — the most capable and the most expensive one in play. Forgetting the router is the
  expensive failure mode, not misrouting.
- KASPER may spawn at a different tier than the script printed, but only with a one-line reason
  recorded in the task doc ([[documentation]]).
- The human may fix a tier for a task; that instruction outranks both the script and KASPER.

## Tuning and the seam
The rubric is a first heuristic and its accuracy is unmeasured. Tune it by editing the
constants in `model_route.py` and mirroring the table above **in the same commit** — a table
that disagrees with the constants is a defect, not a rounding error. `route()` is the single
function the whole rubric hangs off: a learned classifier (Laya) can replace that one function,
keep its signature, and leave the CLI and its callers untouched — per ADR-0013, whose decision
record is local-only in this public repo.

Related: [[delegation]] (every spawn carries the routed tier), [[autonomous-workflow]] (briefs
carry a done-check and a budget — the same text the router scores), [[documentation]] (an
override's reason lives in the task doc).
