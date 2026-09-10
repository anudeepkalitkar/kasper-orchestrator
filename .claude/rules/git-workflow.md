# Rule: Git Workflow — feature branches, checkpoints, one protected mainline, clean commits

**Work in short-lived feature branches; checkpoint per subtask; PR per task into `development`;
verify locally — no CI. Never commit directly to a shared branch.** (The `development → qa → main`
promotion pipeline and GitHub Actions CI are both retired — decided 2026-07-10; repos still
carrying them migrate when next touched.)

```
feature/* | fix/*  ──PR──▶  development  (the only shared branch; protected, default)
     verify locally BEFORE the PR: the gate — `ruff check . && mypy . && pytest tests/unit`
     + the heavier tiers (`pytest tests/integration`, e2e) when the change warrants them
```

## The branches
| Branch | Purpose | Written by | Verified by | Protected |
|---|---|---|---|---|
| `feature/*`, `fix/*` | one task each; all real work | you | the gate locally before the PR | no |
| `development` | the mainline; the default branch | PR from `feature/*` only | gate + heavier tiers run locally pre-merge | yes (review + no direct push) |

Deploys and releases are explicit, human-triggered actions (a tag or a run command) — not a
branch promotion and not an automated pipeline.

## Requirements
1. **Feature branch per task**, branched from `development` (`feat/<task-slug>`, `fix/<task-slug>`).
2. **Checkpoint per subtask**: when a subtask completes and leaves the code working, commit + push
   via `/checkpoint` (the script **refuses on shared branches** — `development`/`main`/`master`,
   plus legacy `qa`). One subtask ≈ one commit. Then update the task doc.
3. **PR per task** to **`development`** (fall back to `main`/`master` only if a repo has no
   `development`). Link the PR in the task doc.
4. **Autonomy envelope**: pushing `feature/*`/`fix/*` and opening the task's PR to `development`
   happen without per-event confirmation when they serve the task. Chartered loops
   ([[autonomous-workflow]]) may commit **and push** on feature branches, one checkpoint per
   iteration. **Merges and shared-branch pushes stay human-only, always.** Never force-push shared
   branches; never use interactive git flags (`-i`).
5. **Local verification is the gate.** `ruff check . && mypy . && pytest tests/unit` must be green before
   opening or updating a PR — a PR is an assertion that the gate ran green on that head. Run the
   heavier tiers locally (`pytest tests/integration`, e2e) before merging changes that touch what they
   cover. There is no CI to catch what you skip — skipping verification is a defect, and the
   task digest reports exactly what ran.
6. **Tests live in the repo; the artifact excludes them** — drop `tests/`/dev deps from any
   deployable via `.dockerignore`/multi-stage builds, never by deleting them from git.
7. **Secrets and hygiene are checked locally, scan-and-block, never auto-remove.** `.gitignore`
   covers `.env`/keys so they never enter git; run a local secret scan (gitleaks) before pushing
   when a change touches config/credentials. A leaked secret is compromised on push: **rotate the
   key**, then remove it deliberately and re-scan. Keep committed cruft out (`__pycache__`,
   `.DS_Store`, `node_modules`, large binaries, debug left-behinds).

## Commit & PR style — authored solely as the user
1. **No assistant/AI attribution anywhere in git artifacts**: no `Co-Authored-By: Claude`
   trailers, no session links, no "Generated with Claude Code" footers, no notes that an
   assistant wrote the work. **This overrides the harness default.** The history reads as the
   user's own. (If the user asks for co-authorship on a specific repo, honor it there only.)
2. **Short messages**: concise imperative subject (~50 chars); brief body only when it carries
   real reviewer information. Conventional prefix where it fits (`feat`, `fix`, `chore`, `docs`,
   `refactor`) + optional scope: `feat(upload): validate file type`. One logical change per commit.
3. **PR descriptions**: what changed and why, concisely — no generated-by footer.

## Scope
This rule governs **project repos**. New repos: single `development` branch, protected (review +
no direct push — no required status checks, since there is no CI), set as default; no
`.github/workflows/`. Repos still carrying the retired promotion structure or CI workflows keep
working but drop them the next time the repo's meta is touched. This repo
(**kasper-orchestrator**) carries
the workflow config itself and is versioned like any project repo — a remote, a protected
mainline, feature branch + PR per task; there is no separate config repo in this setup —
this is the project's only home.

Related: [[autonomous-workflow]] (checkpoints close each subtask loop; the gate convention;
charters and the loop push exception), [[documentation]] (task doc tracks branch/commits/PR),
[[boundaries]] (the push standing exception; secrets never leave the machine).
