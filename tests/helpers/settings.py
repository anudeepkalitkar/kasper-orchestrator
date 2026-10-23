"""Helpers for inspecting settings hook blocks (pure — no I/O)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Task 022's contract, independent of the settings document being tested.
ROUTINE_ALLOW_RULES = tuple(
    f"Bash({command} *)"
    for command in (
        "git status",
        "git log",
        "git diff",
        "git show",
        "git fetch",
        "git rev-parse",
        "git add",
        "git commit",
        "git switch",
        "git pull",
        "git branch",
        "git worktree",
        "git apply",
        "gh pr create",
        "gh pr view",
        "gh pr list",
    )
)
ORIGINAL_ALLOW_RULES = ("Read", "WebFetch", "WebSearch")


def hook_commands(hooks: Mapping[str, Any]) -> list[str]:
    """Every handler command in a hook block, in document order.

    Args:
        hooks: A settings hook block — event -> matcher groups -> handlers.

    Returns:
        The ``command`` string of every handler found.
    """
    return [
        handler["command"]
        for groups in hooks.values()
        for group in groups
        for handler in group["hooks"]
    ]
