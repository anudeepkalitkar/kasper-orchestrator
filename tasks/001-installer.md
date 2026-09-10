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
- [x] 1. ADR-0006 installer design — commit: pending (checkpoint in flight)
      goal:       docs/adr/0006-*.md records the decided spec (form, CLI, owned files, merges,
                  backup, manifest, status, uninstall, platform, tests); ADR-0001..0006 monotonic
      done-check: `ls docs/adr/ | grep -c '^000[1-6]-'` prints 6
      cap:        3
      owner:      documentor   verifier: code-reviewer (consistency vs install.py, after subtask 3)
- [ ] 2. Gate tooling + tests first — commit: pending
      goal:       root pyproject.toml (ruff, strict mypy, pytest testpaths); gitignored .venv with
                  ruff/mypy/pytest; tests/unit/ (pure functions, no I/O) and tests/integration/
                  (tmp homes via --home) written against the ADR-0006 contract; tests fail only
                  because install.py does not exist yet
      done-check: `.venv/bin/ruff check tests && .venv/bin/pytest tests --collect-only -q`
      cap:        5
      owner:      tester   verifier: python-developer (reads them before implementing; reports gaps)
- [ ] 3. install.py — commit: pending
      goal:       install.py at repo root implements the ADR-0006 spec; all tests green
      done-check: `.venv/bin/ruff check . && .venv/bin/mypy . && .venv/bin/pytest tests/unit && .venv/bin/pytest tests/integration`
      cap:        5
      owner:      python-developer   verifier: tester (re-runs the gate; adds tests for gaps)
- [ ] 4. Code review — commit: n/a
      goal:       code-reviewer finds nothing above "nit" against code-standards/test-structure and
                  the ADR; findings routed back to owner until clean
      done-check: reviewer verdict recorded here
      cap:        3
      owner:      code-reviewer   verifier: n/a (review)
- [ ] 5. README + CLAUDE.md — commit: pending
      goal:       README "Use" describes the one-command install (+ --status/--dry-run/--uninstall,
                  Windows `python`); CLAUDE.md "Where things are" and the hooks section no longer
                  say the installer is absent; no stale "hand copy" / "python3→python by hand" claims
      done-check: `grep -rn -i 'hand copy\|no installer\|change them to .python.' README.md CLAUDE.md .claude/CLAUDE.md` is empty
      cap:        3
      owner:      documentor   verifier: code-reviewer (docs vs code)
- [ ] 6. macOS real-home run — commit: n/a
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
      note:       VM was NOT running at task start (192.168.64.2:22 closed); human starts it.
                  Key: the retired dev repo's claude-temp/vm/win11-vm-key (ledger grant 2026-09-09).
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
- Checkpoints are batched: after 1, after 2+3(+4 fixes), after 5 — three git-workflow calls.

## Review
<filled when done: outcome + verification>
