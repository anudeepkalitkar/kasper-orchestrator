# ADR-0004: Sessions clean up after themselves

- **Status:** accepted
- **Date:** 2026-09-10
- **Deciders:** @anudeepkalitkar (repository owner)

## Context

A working session leaves things behind. Scratch files pile up in the project's temp area,
git worktrees created for a piece of work outlive it, and tooling started along the way —
containers, applications, background processes, temporary services — keeps running long after
the reason for it is gone.

Each of those is harmless once and corrosive repeatedly. Scratch that accumulates stops being
scratch: a later session reading it cannot tell this week's evidence from last month's, and
stale material is worse than none because it is believed. Worktrees and running tooling
consume disk and memory whether or not anyone is still using them. And every leftover is a
difference between the machine's current state and its clean state, so the next problem is
diagnosed against a background of noise nobody remembers creating.

The cost of leaving is paid by whoever comes next, which is why it does not get paid.

## Decision

**A session leaves the machine as it found it.** When a session ends, it is responsible for
undoing what it created:

- the project's scratch area (`claude-temp/`) is emptied of that session's leavings;
- git worktrees created for the session's work are removed once they are no longer needed;
- external tooling the session started — containers, launched applications, background
  processes, temporary services — is stopped and cleaned up.

This is a policy about responsibility, not a procedure. What triggers cleanup, how it runs,
and precisely what "session end" means for a session that is killed rather than ended, are
all deferred to whoever builds it.

## Alternatives considered

- **Never clean; let the human sweep periodically** — rejected: it is the current situation,
  and the sweep does not happen until something breaks.
- **Clean aggressively on a schedule instead of at session end** — rejected: a timer does not
  know what is still in use, so it either deletes live work or waits so long it is useless.
- **Clean at session start instead of session end** — rejected: it leaves the machine dirty
  between sessions and destroys evidence exactly when someone might want to look at it.
- **Never create the mess: forbid scratch, worktrees, and started tooling** — rejected: they
  are how work gets done; the problem is that they outlive it.

## Consequences

- **Cleanup must be conservative — and that constraint outranks thoroughness.** A session
  removes what it created, never what it merely found. No blanket sweep of a shared directory,
  no stopping tooling because it looks unused. Destructive operations stay guarded and
  deliberate; the permission rules are not relaxed because the caller means well. A session
  that cannot tell whether something is its own leaves it alone and says so.
- **Durable artifacts have to be promoted before scratch is cleared.** Evidence, reports, and
  anything written to scratch that is still worth having belongs in its durable home — memory,
  the documentation, the task record — *before* the clearing happens. Cleanup that eats the
  audit trail is a worse failure than the mess it removes.
- **Not all scratch is disposable.** Some of it is deliberately long-lived across sessions:
  caches, prepared environments, anything expensive to rebuild. The policy therefore needs
  some notion of a keep-list, and what belongs on it is not decided here.
- **Cleanup responsibility gets harder with more than one session.** When disciplines run as
  their own sessions (ADR-0002), whose leavings are whose, and what a single session ending
  should tear down, becomes a real question rather than a bookkeeping detail.
- **Nothing in this repository implements any of this yet.** The scratch area is created and
  kept out of version control; nothing removes anything, and no worktree or tooling is
  tracked. This ADR records the intent; the mechanism is a later decision.
