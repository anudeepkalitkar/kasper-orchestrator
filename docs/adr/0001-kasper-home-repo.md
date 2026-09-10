# ADR-0001: KASPER is built in this repository

- **Status:** accepted
- **Date:** 2026-09-10
- **Deciders:** @anudeepkalitkar (repository owner)

## Context

KASPER is a Claude Code configuration — a skill, a set of agent definitions, standing rules,
commands, hook scripts, and a permission ledger. It was previously worked on in an earlier
private development repository, now retired.

This repository was created to be the one place KASPER is built. Standing it up settled
several things at once, and they are recorded together because they were decided together:
where the work happens, how the mainline is protected, what the shipped permission ledger and
`settings.json` contain, and what is here today as against what arrives later.

## Decision

**1. KASPER is built here from here on out.** Branches, pull requests, and the records of the
decisions behind them all live in this repository. It is not fed from anywhere else, and it
is not a copy of anything.

**2. What is here today is what this repository claims.** It holds `.claude/` — the skill,
the agent definitions, the rules, the commands, the hook scripts, `settings.json`, and the
permission ledger — plus `README.md`, `CLAUDE.md`, and this decision record. The installer,
the Python package, the architecture notes, and the tests are **not** here. Further
components land as development continues; until one is here, the documentation says so
rather than describing it.

**3. `master` is the protected mainline.** An active repository ruleset scoped to
`refs/heads/master` blocks non-fast-forward updates (force-push) and branch deletion, and
requires a pull request (zero required approvals). The repository-admin role holds an
always-on bypass, so the owner can push directly.

**4. The permission ledger is universal, not personal.** Machine- and project-specific
approvals are stripped — the `grants` list is empty and the allow entries are trimmed to
forms that are safe anywhere — while every guard, all nineteen `ask_patterns` that force a
confirmation prompt, is carried over byte-identical. The polarity rule applied throughout:
**in doubt about a guard, keep it; in doubt about an allow, drop it.**

**5. `settings.json` is portable, not personal.** It carries only the hook wiring and the
permission block; the credential deny paths are written in `~/` form rather than one
machine's absolute home; the model pin, theme, and notification toggles are removed. Every
hook resolves through `$CLAUDE_PROJECT_DIR/.claude/scripts/run_hook.py`, so the tree works
in place in any project that carries it — no install step stands between a clone and a
working config.

*Section 5 superseded by ADR-0005 (2026-09-10): hooks resolve through
`$HOME/.claude/scripts/run_hook.py`, and the home install is the installation mechanism.
The rest of this record stands.*

## Alternatives considered

- **Build elsewhere and copy releases in** — rejected: two places to change, two histories to
  keep in step, and every document has to explain which copy the reader is holding.
- **Make this a read-only mirror** — rejected: the same duplication, and a change made here
  would have nowhere to land.
- **Bring everything across at once, history included** — rejected: much of it is working
  material (task records, machine-specific configuration) that a reader of the configuration
  does not need; components arrive as they are made fit for general use.
- **Keep the permission ledger as it is used day to day** — rejected: its grants encode one
  machine's paths and habits; noise at best, over-permissive at worst.
- **Relax the ledger's guards for a permissive default** — rejected: the guards are the
  property most worth keeping.
- **Keep `settings.json` verbatim** — rejected: absolute home paths and personal preferences
  do not transfer to another machine.
- **Classic branch protection instead of a ruleset** — rejected: a ruleset names its bypass
  actors explicitly, which is what a single-owner repository needs.

## Consequences

What this makes easy: one place to change, one history, one copy to trust; the configuration
can be used straight from a clone; the defaults a newcomer inherits are conservative; the
mainline cannot be force-pushed away or deleted by accident.

The trade-offs accepted, plainly:

- **The entry documents describe a smaller system than KASPER's full design.** `README.md`
  and `CLAUDE.md` cover only what is present, so the installer, the package and the
  architecture notes are absent from them rather than promised.
- **History does not come along.** Work brought in from before arrives without its original
  commits, and the reasoning behind earlier decisions survives only where it was written
  down. Decisions from here on are recorded in this repository.
- **The ruleset names `refs/heads/master` literally.** Renaming the default branch does not
  carry the protection with it; the renamed branch is unprotected until the condition is
  updated.
- **The admin bypass covers every rule in the set**, including the force-push and deletion
  blocks. The pull-request requirement is a convention for contributors and a speed bump for
  the owner, not an absolute.
- **The `~/` deny paths are not yet runtime-verified.** The absolute-path form is what has
  been exercised in practice; the tilde form was chosen for portability and has not been
  confirmed to match the same files on every platform. If it fails to expand, those
  credential paths lose that particular backstop — the ledger's guards still apply.
- **The shipped hook commands name `python3`.** There is no installer here to render them per
  platform, so on Windows they must be changed to `python` by hand.

*Appended 2026-09-10: ADR-0006 brought the installer and its tests into this repository, so
§2's listing of them as "not here" and the consequence above that the entry documents omit
the installer both stopped being true that day; `install.py` also renders the hook
interpreter per platform, so Windows no longer needs the hand edit named just above.*
