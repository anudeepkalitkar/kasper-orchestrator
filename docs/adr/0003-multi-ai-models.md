# ADR-0003: Multi-AI models

- **Status:** accepted
- **Date:** 2026-09-10
- **Deciders:** @anudeepkalitkar (repository owner)

## Context

Every KASPER role today runs on Claude — the session the human talks to, and each discipline
it delegates to. That is a coherent starting point, but it puts two things in tension with
the design.

The first is verification. KASPER's rule is that the writer never grades its own work, and a
different session honours that. A reviewer that shares the writer's habits and blind spots is
still a weaker check than one that does not — independence of judgement is worth more than
independence of context alone.

The second is proportion. The roles differ enormously in what they demand. Some require the
strongest reasoning available; others are routine, well-specified, and would be served just
as well by something far cheaper. Paying the same rate for every role means either overpaying
for the routine ones or underpowering the hard ones.

Behind both sits an ordinary structural concern: a system whose every part depends on one
vendor inherits that vendor's outages, price changes, and deprecations wholesale.

## Decision

**KASPER will orchestrate multiple AI models, not only Claude.** Roles are matched to models
rather than all sharing one: a role whose value is independent judgement — reviewing or
testing what another role implemented — can be filled by a different model from the one that
did the work, and lower-stakes roles can be filled by cheaper models.

Which models, and how they are reached, is deliberately **not** decided here. This ADR fixes
the direction only.

The role structure itself is unchanged by this: the roles are the disciplines of ADR-0002,
and matching models to them is a separate axis from how those roles are run.

## Alternatives considered

- **Stay single-vendor** — rejected: every role inherits one model's blind spots, one
  vendor's availability, and one price.
- **Use the same model everywhere but vary its settings** — rejected: it tunes effort, not
  judgement; a model's blind spots are not a setting.
- **Let each role be configured to any model from the start, with no common seam** — rejected:
  that is a configuration option pretending to be an architecture, and it pushes every
  difference between models onto whoever writes the role.

## Consequences

- **A model-adapter seam is required, and does not exist yet.** Roles are currently written
  against one model's interface; something has to stand between a role and the model filling
  it before this is possible at all.
- **Behaviour stops being uniform.** Instruction-following, output shape, tool use, and
  refusal behaviour all vary between models. A role definition that only ever ran on one model
  is not guaranteed to hold on another, and each role has to be judged on the model that
  fills it.
- **Permission and safety semantics vary too.** The guarantees KASPER's boundaries rely on are
  not identical across models; the strictest reading has to win, or the boundary is only as
  strong as the most permissive participant.
- **Cost becomes a design variable rather than a constant** — a benefit, and one more thing to
  reason about when a role is defined.
- **Today's configuration is Claude-only**, and every role in this repository names a Claude
  model. Nothing here implements the above yet; this is a direction, and the documentation
  will say so until the code says otherwise.
