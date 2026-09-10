"""Shared helpers for reading what ``install.py`` prints and returns.

Deliberately free of any ``install`` import so it stays usable from both tiers.
"""

from __future__ import annotations

from collections.abc import Callable

#: Report verbs ADR-0006 uses across install (§8), status (§9), and uninstall (§10).
KINDS = frozenset(
    {"create", "overwrite", "unchanged", "merge", "ok", "modified", "missing", "delete", "keep"}
)


def run_cli(main: Callable[[list[str]], int], argv: list[str]) -> int:
    """Call an argparse ``main`` and normalise its exit code.

    Args:
        main: The entry point under test.
        argv: The argument vector to pass.

    Returns:
        The value ``main`` returned, or the code it raised via ``SystemExit`` (argparse
        exits rather than returning on a usage error).
    """
    try:
        return main(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1


def parse_kinds(text: str) -> dict[str, str]:
    """Parse ``<kind>  <relpath>`` report lines into a mapping.

    Args:
        text: Captured report output.

    Returns:
        Relative path -> the kind word that introduced its line. Lines that do not start
        with a known kind are ignored.
    """
    kinds: dict[str, str] = {}
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) >= 2 and tokens[0].strip(":").lower() in KINDS:
            kinds[tokens[1]] = tokens[0].strip(":").lower()
    return kinds


def line_with(text: str, *needles: str) -> str | None:
    """The first line containing every needle, or None when there is none.

    Args:
        text: Captured report output.
        needles: Substrings that must all appear on one line.

    Returns:
        The matching line, or None.
    """
    return next((line for line in text.splitlines() if all(n in line for n in needles)), None)
