# ADR-0006: A stdlib script installs KASPER into the Claude home

- **Status:** accepted
- **Date:** 2026-09-10
- **Deciders:** @anudeepkalitkar (repository owner)

## Context

ADR-0005 made the home install — the contents of `.claude/` copied into `~/.claude/` — the
mechanism by which KASPER is installed, and named its cost in the same breath: a hand copy
plus a hand merge of `settings.json` is error-prone, easy to leave half-done, and offers no
way to tell a stale home copy from a current one. ADR-0001 §2 listed the installer among the
components that are not here yet, and said so rather than implying one.

What is wanted now is that someone other than the author can install this configuration on
their own machine, Windows included — standalone and OS-independent, needing nothing but
Python and a logged-in `claude`. The repository neither blocks that nor helps it: there is
no Python package here, no tests, and no CI.

## Decision

**`install.py`, one stdlib-only Python script at the repository root, becomes the way KASPER
is installed, updated, checked, and removed.** The specification below is the whole decision,
because it is what the implementation and its tests are built against.

**1. Form.** `python3 install.py` after a clone (`python install.py` on Windows). No
packaging, no dependencies; functions-first, fully typed, docstringed, per the code
standards. Python 3.12 is the floor — the version the README already claims — enforced by
ruff's `target-version = "py312"` and mypy's `python_version = "3.12"` (§12), not by a
packaging pin: a repository that ships a script rather than a package carries no `[project]`
table in `pyproject.toml` to hold one. Pure logic — merging, rendering, hashing, planning — is
separated from I/O so the unit tier needs no disk.

**2. CLI.** `install.py [--dry-run] [--status] [--uninstall] [--home PATH] [--python NAME]`.
`--home` is the Claude home directory, defaulting to the user's `~/.claude`; it exists so
tests and the Windows VM can target any directory. `--python` is the interpreter name
rendered into the hook commands — `python` on Windows, `python3` elsewhere, by default.
Exit **0** clean, **1** drift or something left behind, **2** usage. An error — a corrupt or
malformed JSON file, a home that cannot be read — exits **1** as well, with the message on
stderr and no traceback: drift and error share the code, and only a usage error is 2.

**3. Owned files.** `agents/`, `commands/`, `rules/`, `scripts/`, `skills/`, `sounds/`, and
`CLAUDE.md` are copied verbatim from the repository's `.claude/`, recursively, `__pycache__`
excluded. These are KASPER's; every install overwrites them. An update also **prunes**: a
file the previous manifest (§7) recorded that the source no longer ships is backed up (§6),
deleted, and reported as `remove`, and a directory the removal leaves empty goes with it —
so a rename or a deletion upstream leaves no stale copy behind for Claude Code to read.

**4. `settings.json` is merged, never replaced.** The existing file is loaded, or taken as
empty. Across **every** event in it, not only the events KASPER defines, existing handlers
whose command contains `run_hook.py` — previous KASPER wiring, current or retired — are
removed, then KASPER's are appended with `python3` rendered to the chosen interpreter; a
matcher group left with an empty `hooks` list is removed with it, and an event left with no
groups is removed too. Handlers naming any other command are untouched. This is the same
strip rule uninstall applies (§10). `allow` and `deny` become the union, KASPER's entries
appended where absent and
existing order kept; other permission keys and every other top-level key — model, theme, the
rest — are left alone. Written atomically, temp file plus replace, two-space indent.

**5. The permission ledger is merged, never replaced.** Absent at home, the repository's seed
is copied as is. Present, keys absent from `allow_keys` are added, and `allow_patterns` and
`ask_patterns` entries absent by `pattern` are added; `grants`, `denials`, and `_doc` are
left exactly as found. Nothing is ever removed from a home ledger — grants are per-machine,
and only the seed is the portable part.

**6. Backup before overwrite.** Every existing owned file whose bytes differ from the
incoming one, and `settings.json` and the ledger when they exist and would change, are copied
first to `~/.claude/kasper-backup-<UTC timestamp>/<same relative path>`, the files an update
prunes (§3) among them. A run that changes nothing creates no backup directory. A directory
of that name that already exists gets a `-2`, `-3`, … suffix, so two runs inside the same
second cannot overwrite each other's copies.

**7. Manifest.** `~/.claude/kasper-manifest.json`, schema 1: source path, source git commit
or null when unavailable, install time as UTC ISO, interpreter name, and a `files` map from
each owned file's relative path to its SHA-256. `settings.json` and the ledger are absent
from `files` — they are merged, not owned. It is also what the next install reads to find
the files it should prune (§3): what the previous run recorded and the source no longer
ships.

**8. `--dry-run`** prints the plan — create, overwrite, unchanged, merge, remove — names
every file it would back up, and writes nothing.

**9. `--status`** reads the manifest and reports each recorded file ok, modified, or missing,
and whether `settings.json` carries the KASPER hooks; "not installed" when there is no
manifest. Any drift exits 1.

**10. `--uninstall`** deletes each manifest file whose hash still matches, keeps and reports
any that were modified, removes directories left empty, strips handlers containing
`run_hook.py` from `settings.json` after backing it up — emptied matcher groups and emptied
events going with them, as in §4 — and deletes the manifest. The
permissions block and the ledger stay — harmless, and the ledger holds the user's own grants
— and the report says so. Anything kept exits 1.

**11. Platform.** The interpreter name is the only platform-specific rendering. `$HOME` in
the hook commands stays literal, because Claude Code passes them to Git Bash in the ordinary
Windows case (ADR-0005, whose PowerShell fallback remains a known wrinkle). Windows is
verified on a real Windows 11 VM before the task closes, not assumed.

**12. Tests.** `tests/unit/` for the pure functions with no I/O, `tests/integration/` for
real runs against temporary home directories through `--home`. Ruff, mypy, and pytest are
configured in a root `pyproject.toml` and installed into a gitignored virtualenv — no CI, per
the git-workflow rule. The gate here is **`ruff check . && mypy && pytest tests/unit`**:
`mypy` takes no path, because its directory crawl skips dot-directories and would never see
the hook scripts — `pyproject.toml` names `.claude/scripts` in its `files` list instead.

## Alternatives considered

- **A Python package with a CLI (`pipx install`, `kasper install`)** — rejected: packaging, a
  version number, and a second artifact to keep in step, for one script's worth of behaviour.
  It can be grown into later.
- **A shell installer** — rejected: not runnable on native Windows, which is the reason
  `run_hook.py` exists at all (its D11 rationale).
- **Never overwrite existing home files, report only** — rejected: it turns every update back
  into a manual step; the backup plus the manifest buy the same safety with repeatability.
- **Prompt per conflicting file** — rejected: interactive only, and a scripted or remote
  setup has nobody to answer.
- **Replace `settings.json` outright** — rejected: it destroys the user's own keys — the
  model pin, the theme, their other hooks.

## Consequences

- **One command installs, updates, checks, and removes.** The README's hand-copy paragraph
  goes away, as a separate documentation change in this same task.
- **A stale home copy becomes detectable.** `--status` against the manifest closes exactly
  the gap ADR-0005 named.
- **A user's own `~/.claude/CLAUDE.md` is an owned path**: backed up, then replaced. Personal
  global instructions have to move elsewhere or be re-added. That is a real cost, accepted for
  a single source of truth.
- **Uninstall does not leave a clean home.** The permissions block and the ledger stay behind
  by design; removing them is a manual step.
- **The installed ledger is not always the one in force.** The gate prefers a project's own
  `.claude/permissions-ledger.json` when one exists and falls back to the home copy only
  otherwise, so a project carrying its own ledger is unaffected by the install and `--status`
  speaks only for the home copy. Reconciling the two is outside this decision.
- **Tests and gate tooling enter this repository for the first time.** The rules' gate, in
  this repo's form `ruff check . && mypy && pytest tests/unit` (§12), becomes literally
  runnable here.
- **Windows verification depends on a VM outside the repository** — a manual, slow gate. It
  also has something specific to catch: the default `--home` comes from `Path.home()`, which
  on Windows reads `USERPROFILE` and, since Python 3.8, never `HOME`, while the `$HOME` in the
  installed hook commands is whatever Git Bash sets. The two agree in the ordinary case, and
  the VM is where that is confirmed rather than assumed.
