"""Helpers for inspecting settings hook blocks (pure — no I/O)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


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
