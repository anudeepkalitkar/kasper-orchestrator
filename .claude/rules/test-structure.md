# Rule: Test Structure (where tests live, in tiers)

**Tests live in a `tests/` directory scoped to each package/service, split by tier
(`unit/ integration/ e2e/`), mirroring the source layout.** The physical split is load-bearing:
local verification runs the right tier at the right moment — tests run **directly** (`pytest`),
no Makefile and no CI (see [[git-workflow]]). The gate's other steps run directly too —
`ruff check .` and `mypy .`, or **bare `mypy`** where `pyproject.toml` names the files to check.

## Layout
```
<package>/                     # a service, app, or library
  src/ (or the package dir)
  tests/
    unit/            # fast, isolated — part of the gate, run before every PR
    integration/     # real-ish: DB/bus/storage/HTTP via fakes or compose — pre-merge when touched
    e2e/             # full stack / browser — run locally before merging changes it covers
    conftest.py      # shared fixtures (pytest) / setup
    helpers/         # shared test utilities + fakes, imported (never copy-pasted)
```
- **Scoped per package, not one repo-wide `tests/` and not co-located** next to source. Each unit
  stays self-contained and movable.
- **Cross-package / whole-system tests** get their own top-level area (e.g. `tests/integration/` or
  `integration/`) — they don't belong to any single package.
- A test file's path **mirrors the source it covers**, so location tells you what it tests.

## Tiers (the split is by **folder**, not just a marker)
| Tier | May touch | Runs when (locally) | Speed |
|---|---|---|---|
| `unit/` | nothing external — no network, DB, disk, clock, real subprocess; use fakes | the gate (`pytest tests/unit`), before every PR | milliseconds |
| `integration/` | real-ish collaborators (DB/bus/storage/another service) via fakes or `docker compose` | `pytest tests/integration`, before merging what it covers | seconds |
| `e2e/` | the full running stack / a real browser | before merging what it covers | slow |

If a "unit" test reaches for the network/DB/disk/clock, it is **not** a unit test — move it to
`integration/`. Keeping `unit/` pure is what makes the gate fast and reliable.

## Naming & shape
- **Names:** `test_*.py` (Python), `*.test.ts` / `*.spec.ts` (TS/JS). One coherent thing per file.
- **Small, single-purpose tests** with a clear assertion; descriptive names that state the behavior.
- **Fixtures** in `conftest.py`; **shared helpers/fakes** in `tests/helpers/` (or a `fakes` module) —
  imported, never duplicated across test files.

## Requirements
1. **Physical tier separation** so a command can target a path (the gate runs only
   `tests/unit`; `pytest tests/integration` the heavier tier). Markers/tags may *additionally*
   be used, but the folder is the source of truth.
2. **Tests live in the repo on every branch** — they are the safety net. They **must not ship**:
   exclude `tests/` from the deployable artifact via `.dockerignore` / a multi-stage build
   ([[git-workflow]] — tests stay, artifacts exclude), not by deleting them from git.
3. **No I/O in `unit/`.** Network/DB/disk/clock → use a fake or move the test up a tier.
4. **Mirror the source path** so a test is findable from the code and vice-versa.

## How to apply
- New package/service: create `tests/{unit,integration,e2e}/` from the start; put the first test in
  the right tier.
- Configure the runner to discover all tiers; the gate runs `tests/unit`, the heavier
  tiers run by path (`pytest tests/integration`, e2e) before merging what they cover.
- Reviewing a PR: a test that does I/O but sits in `unit/` is a defect — move it, don't merge it.

Related: [[git-workflow]] (local verification is the gate), [[autonomous-workflow]] (tests are the
proof of done), [[code-standards]] (small focused tests and helpers, not elaborate frameworks).
