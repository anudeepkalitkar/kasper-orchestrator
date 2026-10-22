#!/usr/bin/env python3
"""Pick the model tier for one agent spawn from its brief — deterministic, stdlib only.

KASPER runs this before spawning a subagent: it scores the brief on four signals —
size, novelty, ambiguity, risk — maps the total to a tier, then clamps that tier to the
agent's bounds. Stdout carries exactly one tier (``haiku``, ``sonnet``, ``opus``, or
``fable``); ``--explain`` writes the score breakdown to stderr so stdout stays bare.

Every constant below is named so the rule doc can quote it; change the numbers here and
the doc together.

Usage:
    python3 model_route.py --agent python-developer --brief <brief.md>
    python3 model_route.py --agent architect --explain < brief.md

Exit codes:
    0  a tier was printed
    2  bad arguments or an unreadable brief
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Literal, NamedTuple

Tier = Literal["haiku", "sonnet", "opus", "fable"]

#: Tiers cheapest first; clamping works on positions in this tuple.
TIERS: tuple[Tier, ...] = ("haiku", "sonnet", "opus", "fable")

#: Highest total that still routes to each tier; anything above ``OPUS_MAX`` is fable.
HAIKU_MAX = 1
SONNET_MAX = 5
OPUS_MAX = 11

#: File extensions that mark a bare token (no ``/``) as a file path.
PATH_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".md",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".sh",
    ".sql",
    ".txt",
    ".cfg",
    ".ini",
    ".html",
    ".css",
)
#: Punctuation stripped from a token's ends before it is tested as a path.
TOKEN_TRIM = "`'\"()[]{}<>,;:.!?*"
#: (upper bound on distinct paths, size points) — first bound the count fits wins.
SIZE_STEPS: tuple[tuple[int, int], ...] = ((1, 0), (3, 1), (8, 2))
#: Size points for more distinct paths than the last step allows.
SIZE_MAX_POINTS = 3
#: A brief longer than this many characters earns one extra size point.
LONG_BRIEF_CHARS = 2500
LONG_BRIEF_POINTS = 1

NOVELTY_WORDS: tuple[str, ...] = (
    "new",
    "create",
    "design",
    "from scratch",
    "architecture",
    "introduce",
    "greenfield",
)
NOVELTY_POINTS = 2
ROUTINE_WORDS: tuple[str, ...] = (
    "fix",
    "rename",
    "typo",
    "bump",
    "docstring",
    "comment",
    "reword",
    "format",
)
#: Applied when routine words appear and no novelty word does.
ROUTINE_POINTS = -1

AMBIGUITY_WORDS: tuple[str, ...] = (
    "investigate",
    "debug",
    "root cause",
    "unclear",
    "decide",
    "choose",
    "options",
    "why does",
    "intermittent",
    "flaky",
)
AMBIGUITY_POINTS = 1
AMBIGUITY_CAP = 3

RISK_WORDS: tuple[str, ...] = (
    "migration",
    "auth",
    "security",
    "concurrency",
    "race",
    "async",
    "crypto",
    "payment",
    "delete",
    "data loss",
    "production",
    "terraform",
    "infra",
    "permission",
    "ledger",
)
RISK_POINTS = 2
RISK_CAP = 6

#: Outside the risk list (risk words are never discounted), a phrase is not counted only
#: inside an explicit exclusion: a prohibition, then an action verb, then (within
#: ``EXCLUSION_OBJECT_WORDS`` words) the phrase as that verb's object, all in one clause —
#: "never touch the ambiguity word". Anything looser ("no downtime change", "avoid flaky")
#: describes real work, and routing errs expensive.
PROHIBITIONS: tuple[str, ...] = ("do not", "don't", "never", "must not", "mustn't")
#: The verbs an exclusion forbids. A risk word that is itself the verb ("never delete files")
#: stays counted — it names the risky act rather than excluding one.
EXCLUSION_VERBS: tuple[str, ...] = ("touch", "edit", "change", "modify", "delete", "write", "run")
#: Words allowed between the verb and the phrase ("do not edit the shared auth module").
EXCLUSION_OBJECT_WORDS = 4
#: Clause ends that stop an exclusion from reaching a later phrase.
CLAUSE_END = re.compile(r"[.!?;:,\n]")
#: Conjunctions and sequencers that start new work, so they end an exclusion's object span:
#: "do not edit docs and implement the migration" still counts the migration.
SPAN_CONJUNCTIONS: tuple[str, ...] = ("and", "or", "but", "then", "also", "plus", "so", "while")
#: Action verbs beyond the scored lists that likewise start new work inside a clause.
ACTION_VERBS: tuple[str, ...] = (
    "add",
    "implement",
    "build",
    "update",
    "remove",
    "refactor",
    "migrate",
    "make",
    "use",
)
#: Any of these inside the object span ends the exclusion, keeping the phrase's score: the
#: conjunctions, every exclusion and action verb, and every word of the novelty, routine and
#: ambiguity lists — a stray noun among them only errs expensive.
SPAN_STOP_WORDS: frozenset[str] = frozenset(SPAN_CONJUNCTIONS + EXCLUSION_VERBS + ACTION_VERBS) | {
    word for phrase in NOVELTY_WORDS + ROUTINE_WORDS + AMBIGUITY_WORDS for word in phrase.split()
}


def _alternation(words: tuple[str, ...]) -> str:
    """A regex alternation of ``words``; spaces match any whitespace, ``'`` either apostrophe."""
    return "|".join(re.escape(w).replace(r"\ ", r"\s+").replace("'", "['’]") for w in words)


_PROHIBITED_VERB = re.compile(
    rf"\b(?:{_alternation(PROHIBITIONS)})\s+(?:{_alternation(EXCLUSION_VERBS)})\b",
    re.IGNORECASE,
)

#: (floor, ceiling) per agent; an agent not listed gets ``DEFAULT_BOUNDS``.
DEFAULT_BOUNDS: tuple[Tier, Tier] = ("haiku", "fable")
AGENT_BOUNDS: dict[str, tuple[Tier, Tier]] = {
    "architect": ("fable", "fable"),
    "git-workflow": ("haiku", "sonnet"),
    "researcher": ("haiku", "sonnet"),
}


class Scores(NamedTuple):
    """The per-signal breakdown of one brief; ``total`` is floored at 0."""

    size: int
    novelty: int
    ambiguity: int
    risk: int
    total: int


def count_paths(text: str) -> int:
    """Count the distinct file paths a brief mentions.

    A path is a whitespace-separated token that, once its surrounding punctuation is
    trimmed, contains ``/`` or ends in one of :data:`PATH_EXTENSIONS`.

    Args:
        text: The brief.

    Returns:
        The number of distinct path tokens.
    """
    paths: set[str] = set()
    for raw in text.split():
        token = raw.strip(TOKEN_TRIM)
        if token.strip("/") and ("/" in token or token.lower().endswith(PATH_EXTENSIONS)):
            paths.add(token)
    return len(paths)


def _is_excluded(text: str, start: int) -> bool:
    """Say whether the phrase at ``start`` is the object of an explicit exclusion.

    Args:
        text: The brief.
        start: Index where the matched phrase begins.

    Returns:
        True when the phrase's clause, up to ``start``, holds a :data:`PROHIBITIONS` entry and
        an :data:`EXCLUSION_VERBS` entry, followed by at most :data:`EXCLUSION_OBJECT_WORDS`
        words, none of them in :data:`SPAN_STOP_WORDS`, then whitespace. Anything else keeps
        the score — routing errs expensive.
    """
    clause = CLAUSE_END.split(text[:start])[-1]
    # The exclusion nearest the phrase is the only one that can govern it.
    last = next(reversed(list(_PROHIBITED_VERB.finditer(clause))), None)
    if last is None:
        return False
    tail = clause[last.end() :]
    span = [word.strip(TOKEN_TRIM).lower() for word in tail.split()]
    return (
        tail[-1:].isspace()
        and len(span) <= EXCLUSION_OBJECT_WORDS
        and not SPAN_STOP_WORDS.intersection(span)
    )


def count_phrases(text: str, phrases: tuple[str, ...], *, honor_exclusions: bool = True) -> int:
    """Count how many distinct phrases occur in the text, each at most once.

    Matching is case-insensitive on word boundaries, so ``async`` does not match
    ``asyncio`` and a phrase repeated ten times still counts once. A match that is the
    object of an explicit exclusion (:func:`_is_excluded`) is skipped unless
    ``honor_exclusions`` is false.

    Args:
        text: The brief.
        phrases: The phrases to look for; multi-word phrases match any whitespace run.
        honor_exclusions: Whether an excluded match is skipped. Risk words pass False: a
            prohibition still names the risky area, so risk is never discounted.

    Returns:
        The number of phrases found.
    """
    return sum(
        1
        for phrase in phrases
        if any(
            not (honor_exclusions and _is_excluded(text, match.start()))
            for match in re.finditer(
                rf"\b{r'\s+'.join(map(re.escape, phrase.split()))}\b", text, re.IGNORECASE
            )
        )
    )


def size_points(text: str) -> int:
    """Score the brief's size from its distinct paths and its length.

    Args:
        text: The brief.

    Returns:
        The size signal.
    """
    paths = count_paths(text)
    points = next((pts for bound, pts in SIZE_STEPS if paths <= bound), SIZE_MAX_POINTS)
    return points + (LONG_BRIEF_POINTS if len(text) > LONG_BRIEF_CHARS else 0)


def novelty_points(text: str) -> int:
    """Score whether the brief builds something new or makes a routine touch-up.

    Args:
        text: The brief.

    Returns:
        :data:`NOVELTY_POINTS` when a novelty word appears; otherwise
        :data:`ROUTINE_POINTS` when a routine word appears; otherwise 0.
    """
    if count_phrases(text, NOVELTY_WORDS):
        return NOVELTY_POINTS
    return ROUTINE_POINTS if count_phrases(text, ROUTINE_WORDS) else 0


def score_brief(text: str) -> Scores:
    """Score a brief on size, novelty, ambiguity, and risk.

    Args:
        text: The brief.

    Returns:
        The breakdown, with the total floored at 0.
    """
    size = size_points(text)
    novelty = novelty_points(text)
    ambiguity = min(count_phrases(text, AMBIGUITY_WORDS) * AMBIGUITY_POINTS, AMBIGUITY_CAP)
    risk = min(count_phrases(text, RISK_WORDS, honor_exclusions=False) * RISK_POINTS, RISK_CAP)
    return Scores(size, novelty, ambiguity, risk, max(0, size + novelty + ambiguity + risk))


def tier_for(total: int) -> Tier:
    """Map a score total to a tier by the ``*_MAX`` thresholds.

    Args:
        total: The brief's total score.

    Returns:
        The unclamped tier.
    """
    if total <= HAIKU_MAX:
        return "haiku"
    if total <= SONNET_MAX:
        return "sonnet"
    if total <= OPUS_MAX:
        return "opus"
    return "fable"


def bounds_for(agent: str) -> tuple[Tier, Tier]:
    """Return an agent's (floor, ceiling); unknown agents get :data:`DEFAULT_BOUNDS`.

    Args:
        agent: The agent name, e.g. ``python-developer``.

    Returns:
        The (floor, ceiling) tier pair.
    """
    return AGENT_BOUNDS.get(agent, DEFAULT_BOUNDS)


def clamp(tier: Tier, agent: str) -> Tier:
    """Clamp a tier into the agent's bounds.

    Args:
        tier: The tier the score picked.
        agent: The agent being spawned.

    Returns:
        The tier, raised to the agent's floor or lowered to its ceiling as needed.
    """
    floor, ceiling = bounds_for(agent)
    index = min(max(TIERS.index(tier), TIERS.index(floor)), TIERS.index(ceiling))
    return TIERS[index]


def route(agent: str, text: str) -> tuple[Tier, Scores]:
    """Pick the tier for one spawn of an agent on a brief.

    This is the routing seam: a future learned classifier (Laya) replaces this one
    function and keeps its signature, so the CLI and its callers stay unchanged.

    Args:
        agent: The agent being spawned.
        text: The brief it will receive.

    Returns:
        The clamped tier and the score breakdown behind it.
    """
    scores = score_brief(text)
    return clamp(tier_for(scores.total), agent), scores


def explain(agent: str, tier: Tier, scores: Scores) -> str:
    """Render the score breakdown for ``--explain``.

    Args:
        agent: The agent being spawned.
        tier: The clamped tier.
        scores: The breakdown from :func:`score_brief`.

    Returns:
        One line per signal, then the total with its unclamped tier, then the bounds
        applied with the final tier.
    """
    floor, ceiling = bounds_for(agent)
    return "\n".join(
        [
            f"size: {scores.size}",
            f"novelty: {scores.novelty}",
            f"ambiguity: {scores.ambiguity}",
            f"risk: {scores.risk}",
            f"total: {scores.total} -> {tier_for(scores.total)}",
            f"bounds: {agent} [{floor}, {ceiling}] -> {tier}",
        ]
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line for :func:`main`."""
    parser = argparse.ArgumentParser(description="Pick the model tier for an agent spawn.")
    parser.add_argument("--agent", required=True, help="The agent being spawned.")
    parser.add_argument(
        "--brief", type=Path, help="Brief file to score; read from stdin when omitted."
    )
    parser.add_argument(
        "--explain", action="store_true", help="Write the score breakdown to stderr."
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Print the routed tier for an agent and brief.

    Args:
        argv: Command-line arguments, without the program name.

    Returns:
        0 when a tier was printed; 2 when the brief could not be read.
    """
    args = parse_args(argv)
    try:
        text = args.brief.read_text(encoding="utf-8") if args.brief else sys.stdin.read()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"model_route: cannot read brief: {exc}", file=sys.stderr)
        return 2
    tier, scores = route(args.agent, text)
    if args.explain:
        print(explain(args.agent, tier, scores), file=sys.stderr)
    print(tier)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
