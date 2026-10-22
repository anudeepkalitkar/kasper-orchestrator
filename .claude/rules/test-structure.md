# Rule: Test Structure — where tests live, in tiers

**Tests live in a `tests/` directory scoped to each package, split by tier (`unit/ integration/
e2e/`), mirroring the source layout.** Tests run **directly** (`pytest`) — no Makefile, no CI; the
gate recipe is in [[autonomous-workflow]] §4.

```
<package>/
  tests/
    unit/          # fast, isolated — part of the gate, before every PR
    integration/   # real-ish collaborators via fakes or compose — before merging what it covers
    e2e/           # full stack / browser — before merging what it covers
    conftest.py    # shared fixtures
    helpers/       # shared utilities and fakes, imported, never copy-pasted
```
- **Per package**, not one repo-wide `tests/` and not co-located with source. Cross-package tests
  get their own top-level area.
- A test file's path **mirrors the source it covers**.
- **Unit** touches nothing external — no network, DB, disk, clock, real subprocess; use fakes. A
  "unit" test that does I/O belongs in `integration/`; in review it is a defect, move it.
- The split is by **folder** (a command can target a path); markers may additionally be used.

## Naming and shape
`test_*.py` (Python), `*.test.ts` / `*.spec.ts` (TS/JS); one coherent thing per file; small,
single-purpose tests with descriptive names and a clear assertion.

## Requirements
1. Tests live in the repo on every branch but **must not ship** — exclude `tests/` from the
   deployable via `.dockerignore`/multi-stage build, never by deleting them ([[git-workflow]]).
2. New package: create `tests/{unit,integration,e2e}/` from the start and put the first test in the
   right tier; configure the runner to discover all tiers.

Related: [[git-workflow]], [[autonomous-workflow]], [[code-standards]].
