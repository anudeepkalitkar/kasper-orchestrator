# ADR-0002: Multi-session managers

- **Status:** accepted
- **Date:** 2026-09-10
- **Deciders:** @anudeepkalitkar (repository owner)

## Context

KASPER's shape is one session the human talks to, which hands focused work to disciplines —
implementation, testing, documentation, version control. Today those disciplines are
subagents: spawned for a task, given a brief, and gone when they report back. Everything
they learned goes with them.

That costs something real. A discipline that has been living with a codebase knows things a
freshly briefed helper does not, and re-establishing that understanding at the start of every
task is wasted work. Delegation is also serialised in practice: one session can attend to one
thing at a time, so work that could genuinely run side by side does not. At the same time,
two properties are worth keeping exactly as they are — the human talks to **one** thing, not
to a committee, and the discipline that writes something is never the one that judges it.

This record fixes the direction, not the machinery.

## Decision

**KASPER will run as multiple Claude sessions.** One main session remains the human's single
point of contact. The disciplines become **manager sessions** of their own — implementation,
testing, documentation, version control — each a real, long-lived session with its own
context, rather than a helper created and discarded per task.

The main session delegates to them and integrates what comes back. It stays the one voice
the human deals with: the disciplines do not speak to the human directly and do not speak to
each other.

The separation of duties survives the change intact: the session that writes work is never
the session that verifies it.

How sessions are started, addressed, and kept in step is deliberately **not** decided here.
This ADR records the architecture KASPER is being built toward; the mechanism is a later
decision, made when it is built.

## Alternatives considered

- **Stay single-session with per-task subagents forever** — rejected: no discipline memory
  between tasks, and no genuine parallelism.
- **One session per task instead of per discipline** — rejected: the context worth keeping is
  the discipline's, not the task's; tasks end, disciplines do not.
- **Let the human talk to each discipline directly** — rejected: it makes the human the
  integrator, which is the job KASPER exists to do.

## Consequences

- **More sessions cost more than one session.** Several long-lived sessions consume more than
  a single one, whether or not they are busy, and idle context is not free.
- **Coordination becomes the hard problem.** Handing work out, collecting results, and
  keeping several contexts consistent with one another is the difficult part of this design,
  and it is unsolved here by intention.
- **Permission handling gets harder.** A confirmation that a discipline needs has to reach
  the human, who is only looking at the main session. Any mechanism that cannot do that is
  not a candidate.
- **Discipline context becomes persistent state**, with the upkeep that implies: it can go
  stale, and it has to be ended deliberately rather than evaporating on its own.
- **The shipped config today is single-session with per-task subagents**, exactly as ADR-0001
  describes what is present. Nothing in this repository implements the above yet; this is a
  direction, and the documentation will say so until the code says otherwise.
