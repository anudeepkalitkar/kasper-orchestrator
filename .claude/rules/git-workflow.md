# Rule: Git Workflow — feature branches, checkpoints, one protected mainline, clean commits

**Short-lived feature branches; checkpoint per subtask; PR per task into the mainline; verify
locally — no CI. Never commit directly to a shared branch.** (The `development → qa → main`
pipeline and **test and lint** CI are retired; workflows that build and push images or run deploys
are allowed — they are human-triggered ship steps, not verification.)

**The mainline** is `development` if the repo has it, else the branch `refs/remotes/origin/HEAD`
names, else `master`/`main`, whichever exists. It is the only shared branch: protected (review, no
direct push). Deploys and releases are explicit human actions (a tag or a run command).

## Requirements
1. **Feature branch per task**, from the mainline (`feat/<task-slug>`, `fix/<task-slug>`);
   `/new-task` creates it on a clean, up-to-date mainline.
2. **Checkpoint per subtask**: when it completes and leaves the code working, commit + push via
   `/checkpoint` (it refuses shared branches — the mainline, `development`/`main`/`master`, legacy
   `qa` — and a detached HEAD). One subtask ≈ one commit. In a parallel wave a checkpoint stages
   only the node's declared files (`--only`). **A checkpoint never stages `tasks/`, and stages
   `docs/adr/` exactly when `.git/info/exclude` lacks `/docs/adr/`** — the SessionStart hook leaves
   it out only in a private repo ([[boundaries]], [[documentation]]); untracked ADRs there go in a
   `chore` commit of their own. Then update the task doc.
3. **PR per task** to the mainline; link it in the task doc.
4. **Autonomy envelope**: pushing `feature/*`/`fix/*` and opening the task's PR need no per-event
   confirmation; chartered loops may commit and push on feature branches. **Merges and
   shared-branch pushes stay human-only, always.** Never force-push shared branches; never use
   interactive git flags (`-i`).
5. **Local verification is the gate** — the recipe is in [[autonomous-workflow]] §4. It runs
   **once per task, in the Codex tester seat**; a PR asserts it ran green on that head, and
   `git-workflow` checkpoints on that recorded green, running the gate itself only when no record
   covers the tree. (A parallel wave's integration checkpoint is the one narrow exception, in
   `agents/git-workflow.md`.) Run heavier tiers before merging what they cover. Skipping
   verification is a defect; the task digest reports exactly what ran.
6. **Tests live in the repo; the artifact excludes them** ([[test-structure]]).
7. **Secrets and hygiene are checked locally, scan-and-block, never auto-remove.** `.gitignore`
   covers `.env`/keys; run a secret scan (gitleaks) before pushing a config/credentials change. A
   leaked secret is compromised on push: **rotate**, then remove deliberately and re-scan. Keep
   committed cruft out (`__pycache__`, `.DS_Store`, `node_modules`, large binaries, debug
   left-behinds).

## Commit & PR style — authored solely as the user
1. **No assistant/AI attribution in git artifacts** — no `Co-Authored-By`, session links, or
   "Generated with" footers. This overrides the harness default.
2. **Short messages**: an imperative subject (~50 chars), a body only when it carries real reviewer
   information; conventional prefix where it fits (`feat`, `fix`, `chore`, `docs`, `refactor`).
   One logical change per commit.
3. **PR descriptions**: what changed and why, concisely.

## Scope
Governs project repos. New repos: a single protected `development` branch, set as default, no
required status checks, no test/lint workflows. This repo (**kasper-orchestrator**) carries the
workflow config itself and follows the same rules.

Related: [[autonomous-workflow]], [[documentation]], [[boundaries]].
