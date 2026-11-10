# Rule: Model Routing — KASPER picks each spawn's tier from the agent's allowed set

**Every subagent spawn carries a `model` that KASPER picked, by judgment, from that agent's
allowed set below — never outside it, and never omitted.** A spawn with no `model` inherits the
session's own model, the most capable and most expensive one in play. The four tiers are
aliases — `haiku`, `sonnet`, `opus`, `fable` — each resolving to the newest model of its tier,
so a new model ships and nothing here needs editing.

## The allowed sets (authoritative)
| Agent | Allowed models |
|---|---|
| python-developer, typescript-developer, terraform-developer, devops-developer | opus \| fable |
| architect | opus \| fable |
| triager | sonnet \| opus |
| documentor | sonnet \| opus |
| git-workflow | haiku \| sonnet |
| researcher | haiku \| sonnet |

Each agent file carries the same set as one body line, `Allowed models: <a> | <b>`; its
frontmatter pins no model. The table and every agent file's line must agree — drift between
them is a defect.

## Choosing within a set
- The **lower** tier for routine, well-specified, small work.
- The **higher** tier for novel, ambiguous, risky or large work.
- **When unsure, pick the higher.**

## Overrides
- The human may fix a tier for a task; that outranks KASPER. When it differs from what KASPER
  would pick, KASPER records a one-line reason in the task doc ([[documentation]]).
- The human changes the sets by editing this table and the matching agent lines, together.

Related: [[delegation]] (every spawn carries a tier KASPER picked from the allowed set),
[[documentation]] (an override's reason lives in the task doc).
