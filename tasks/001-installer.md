# Task 001: Installer

- **Status:** in-progress
- **Branch:** feat/installer
- **PR:** n/a
- **Created:** 2026-09-10

## Goal
A fresh clone on macOS, Linux or Windows gets a working KASPER home install with one command
(`python3 install.py` / `python install.py`), can check it (`--status`), preview it
(`--dry-run`) and remove it (`--uninstall`); the README's hand-copy paragraph is gone; the
rules' gate (`ruff check . && mypy . && pytest tests/unit`) runs green in this repo for the
first time; the Windows path is verified on the UTM Windows 11 VM.

## Plan / Subtasks
- [x] 1. ADR-0006 installer design — commit: a6bea32
      goal:       docs/adr/0006-*.md records the decided spec (form, CLI, owned files, merges,
                  backup, manifest, status, uninstall, platform, tests); ADR-0001..0006 monotonic
      done-check: `ls docs/adr/ | grep -c '^000[1-6]-'` prints 6
      cap:        3
      owner:      documentor   verifier: code-reviewer (consistency vs install.py, after subtask 3)
- [x] 2. Gate tooling + tests first — commit: 8e6511e (82 tests red-by-design at authoring; 150 by the end of 3)
      goal:       root pyproject.toml (ruff, strict mypy, pytest testpaths); gitignored .venv with
                  ruff/mypy/pytest; tests/unit/ (pure functions, no I/O) and tests/integration/
                  (tmp homes via --home) written against the ADR-0006 contract; tests fail only
                  because install.py does not exist yet
      done-check: `.venv/bin/ruff check tests && .venv/bin/pytest tests --collect-only -q`
      cap:        5
      owner:      tester   verifier: python-developer (reads them before implementing; reports gaps)
- [x] 3. install.py — commit: 8e6511e (verified by tester 2026-09-10 after 3 review rounds: ruff 0, mypy ok, 47 unit + 103 integration = 150 passed, 0 skipped)
      goal:       install.py at repo root implements the ADR-0006 spec; all tests green
      done-check: `.venv/bin/ruff check . && .venv/bin/mypy . && .venv/bin/pytest tests/unit && .venv/bin/pytest tests/integration`
      cap:        5
      owner:      python-developer   verifier: tester (re-runs the gate; adds tests for gaps)
- [x] 4. Code review — commit: n/a — verdict after round 3: NITS ONLY (N-1 version guard + H-1 recorder ensure_ascii being fixed; N-2..N-5 doc nits to documentor). round 1: must-fix 2 (uninstall path containment; wrong-shape JSON uncaught) → fixed; round 2: B1 closed (no traversal bypass), must-fix 2 more (non-string ledger pattern → TypeError; `out=sys.stdout` bound at import breaks capture) → back to owner
      goal:       code-reviewer finds nothing above "nit" against code-standards/test-structure and
                  the ADR; findings routed back to owner until clean
      done-check: reviewer verdict recorded here
      cap:        3
      owner:      code-reviewer   verifier: n/a (review)
- [x] 5. README + CLAUDE.md — commit: 082ecbc (documentor 2026-09-10; reviewer: every claim backed by code)
      goal:       README "Use" describes the one-command install (+ --status/--dry-run/--uninstall,
                  Windows `python`); CLAUDE.md "Where things are" and the hooks section no longer
                  say the installer is absent; no stale "hand copy" / "python3→python by hand" claims
      done-check: `grep -rn -i 'hand copy\|no installer\|change them to .python.' README.md CLAUDE.md .claude/CLAUDE.md` is empty
      cap:        3
      owner:      documentor   verifier: code-reviewer (docs vs code)
- [x] 6. macOS real-home run — commit: n/a — 2026-09-10: 4 overwrites (CLAUDE.md + 3 reflowed scripts) with digest-verified backups, settings/ledger unchanged, --status exit 0, manifest source_commit=082ecbc
      goal:       `python3 install.py --dry-run` then `python3 install.py` against the real ~/.claude
                  reports only "unchanged"/"merge" (home already matches), `--status` exits 0
      done-check: `python3 install.py --status; echo exit=$?` prints exit=0
      cap:        3
      owner:      tester   verifier: KASPER (reads the report)
- [ ] 7. Windows VM verification (exit gate) — commit: n/a
      goal:       on the UTM Windows 11 VM: clone/copy the repo, `python install.py --home <tmp>`
                  then real home; `--status` 0; hooks rendered with `python`; `--uninstall` clean
      done-check: tester's transcript from the VM shows exit=0 for install and status
      cap:        3
      owner:      tester   verifier: KASPER (reads the transcript)
      note:       VM address and SSH key live outside this repo (the machine's own temp area,
                  ledger-granted 2026-09-09). Probed 2026-09-10 once the human started it: Python
                  3.12.10 (= the floor), Git 2.55, Git Bash at its standard path, SSH shell is
                  PowerShell 5.1 (no `&&`), `claude` not installed. Its ~/.claude already holds
                  an OLDER hand copy (agents/commands/rules/scripts/skills/CLAUDE.md/ledger/
                  settings, no sounds, no manifest) — so the gate is an upgrade-over-existing run:
                  expect backups + manifest, then --status 0, then --uninstall.
- [ ] 8. PR to master — commit: n/a
      goal:       PR open, gate green on head, task doc linked; merge on the human's yes
      done-check: `gh pr view --json state` = OPEN → MERGED after human yes
      cap:        2
      owner:      git-workflow   verifier: human

## Decisions & Notes
- 2026-09-10 question round (owner): single stdlib script `install.py` (not a package);
  overwrite owned files with backup + manifest; ship install/--dry-run/--status/--uninstall;
  verify Windows on the UTM VM. Full spec → ADR-0006.
- Repo ledger reverted to the committed seed (0 grants) 2026-09-10; the global copy keeps this
  machine's grants. Consequence: inside this repo the gate prefers the thin project ledger, so
  approvals re-dirty `.claude/permissions-ledger.json` — never commit that diff.
- Gate tooling did not exist on this machine (Python 3.14.7, no ruff/mypy/pytest); installed
  into a gitignored `.venv` in subtask 2. Commands in done-checks use `.venv/bin/...`.
- ADR-0006 amendments (2026-09-10, KASPER rulings on documentor flags): install strips
  `run_hook.py` handlers from EVERY event (same as uninstall); empty matcher groups/events are
  pruned; Python floor 3.12; a project's own ledger shadows the home one (gate order) so
  `--status` speaks only for the home copy — out of scope. README subtask must drop the ledger
  from the copy list (it is merged, not copied).
- Gate rulings (2026-09-10, after subtask 2): vendored `.claude/skills/webapp-testing/` excluded
  from ruff; the 9 ruff findings in `.claude/scripts/` fixed in subtask 3; mypy skips dot-dirs
  so pyproject sets `files = [".", ".claude/scripts"]` — this repo's gate is
  `ruff check . && mypy && pytest tests/unit` (bare `mypy`, README to say so). Tester's
  protective readings stand: `--status` exits 1 when settings.json lost the KASPER hooks;
  merges never overwrite an existing note/value. ADR D1's `requires-python` line is wrong
  (needs a `[project]` table this script repo does not have) — documentor fixes the wording
  in subtask 5; floor enforced via ruff target-version + mypy python_version.
- Tester notes (subtask 3): the Edit/Write autoformat hook strips a just-added import as unused
  before its test body exists — add imports with their uses. Uninstall leaves `"hooks": {}`
  rather than dropping the key (ADR silent; harmless).
- Review rulings (2026-09-10): updates PRUNE owned files no longer in the source (backup first,
  `remove` line) — ADR D3/D7 wording to say so (documentor, subtask 5); error exit code is 1
  with stderr message (ADR silent; module docstring says so); backup dir gets `-N` suffix on
  collision; dry-run lists backups; symlinked owned paths are replaced, never written through.
- Round 3 (2026-09-10): non-string ledger `pattern` → ValueError; `out` resolved at call time;
  unreadable previous manifest → warn + install proceeds (escaping key stays fatal); symlinked
  owned dirs skipped in prune. Declined: renaming the `backup dir` report line (cosmetic,
  blocked by a green test). install.py = 977 lines; reviewer: nothing must be cut.
- Follow-ups NOT in this task: `.claude/rules/git-workflow.md` + `autonomous-workflow.md` still
  teach `mypy .` and a `development` mainline; this repo uses bare `mypy` and `master` — open
  ruling with the human (pre-existing). ADR-0001 §2 context still says installer/tests absent —
  append-only history, superseded in practice by ADR-0006.
- Follow-up found at checkpoint (2026-09-10): `permission_recorder.py` wrote the ledger with
  `ensure_ascii=True`, mangling every em-dash to `\u2014` on each recorded grant — fixed in this
  branch as a one-word change (reviewer H-1) since the branch already touches that file.
- Checkpoints are batched: after 1, after 2+3(+4 fixes), after 5 — three git-workflow calls.

## Review
<filled when done: outcome + verification>
