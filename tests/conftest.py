"""Shared fixtures for the ``install.py`` suite (ADR-0006).

Every fixture here is a plain data structure or a computed path — nothing reads the
disk — so the unit tier can use them without breaking its no-I/O rule.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

#: The repository root: ``tests/`` sits directly beneath it, and its ``.claude/`` is the
#: install source.
REPO_ROOT = Path(__file__).resolve().parent.parent

#: A frozen instant so backup directory names are deterministic in tests.
FIXED_NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)

#: The backup directory :data:`FIXED_NOW` must produce (ADR-0006 §6).
FIXED_BACKUP = "kasper-backup-20260910T120000Z"

_GATE = 'python3 "$HOME/.claude/scripts/run_hook.py" bash_permission_gate'
_NOTIFY = 'python3 "$HOME/.claude/scripts/run_hook.py" notify'
_RETIRED = 'python3 "$HOME/.claude/scripts/run_hook.py" retired_cleanup'


@pytest.fixture
def repo_root() -> Path:
    """The repository root, used as ``source_root`` in the integration tier."""
    return REPO_ROOT


@pytest.fixture
def fixed_now() -> datetime:
    """A frozen UTC instant, so a run's backup directory has a predictable name."""
    return FIXED_NOW


@pytest.fixture
def backup_name() -> str:
    """The backup directory name :func:`fixed_now` must produce (ADR-0006 §6)."""
    return FIXED_BACKUP


@pytest.fixture
def kasper_hooks() -> dict[str, list[dict[str, Any]]]:
    """KASPER's own hook block, shaped exactly like ``.claude/settings.json``."""
    return {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": _GATE, "timeout": 600}],
            }
        ],
        "Stop": [{"hooks": [{"type": "command", "command": _NOTIFY}]}],
    }


@pytest.fixture
def home_hooks() -> dict[str, list[dict[str, Any]]]:
    """A home hook block mixing user handlers with stale KASPER wiring.

    ``PreToolUse`` holds one of each (the KASPER group must be pruned, the user group
    kept); ``SessionEnd`` holds a retired KASPER handler under an event KASPER no longer
    defines (the whole event must go); ``UserPromptSubmit`` is purely the user's.
    """
    return {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": _GATE}]},
            {"matcher": "Write", "hooks": [{"type": "command", "command": "prettier --write"}]},
        ],
        "SessionEnd": [{"hooks": [{"type": "command", "command": _RETIRED}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "my-logger.sh"}]}],
    }


@pytest.fixture
def kasper_settings(kasper_hooks: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """The repository's ``settings.json`` in miniature."""
    return {
        "hooks": kasper_hooks,
        "permissions": {
            "allow": ["Read", "WebFetch"],
            "deny": ["Bash(sudo *)", "Bash(rm -rf *)"],
        },
    }


@pytest.fixture
def home_settings(home_hooks: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """An existing ``~/.claude/settings.json`` carrying keys KASPER knows nothing about."""
    return {
        "model": "opus",
        "theme": "dark",
        "statusLine": {"type": "command", "command": "my-status.sh"},
        "hooks": home_hooks,
        "permissions": {
            "allow": ["Bash(ls*)", "Read"],
            "deny": ["Bash(sudo *)"],
            "additionalDirectories": ["/work"],
        },
    }


@pytest.fixture
def seed_ledger() -> dict[str, Any]:
    """The repository's portable ledger seed, shaped like the real one."""
    return {
        "_doc": "seed documentation",
        "allow_keys": {"cd": "navigation", "ls": "read-only", "pytest": "tests"},
        "allow_patterns": [{"pattern": r"\bgit\s+push\b.*feat/", "note": "feature push"}],
        "ask_patterns": [
            {"pattern": r"\brm\s+-[a-zA-Z]*[rf]", "note": "recursive delete — confirm"},
            {"pattern": r"\bgh\s+pr\s+merge\b", "note": "merges are human-only"},
        ],
        "grants": [],
        "denials": [],
    }


@pytest.fixture
def home_ledger() -> dict[str, Any]:
    """An existing home ledger with the user's own notes, grants, and denials."""
    return {
        "_doc": "the user's own documentation",
        "allow_keys": {"cd": "the user's own note", "brew": "mine"},
        "allow_patterns": [],
        "ask_patterns": [{"pattern": r"\brm\s+-[a-zA-Z]*[rf]", "note": "my own wording"}],
        "grants": [{"command": "npm test", "note": "approved 2026-09-01"}],
        "denials": [{"pattern": r"\bcurl\b", "note": "no outbound writes"}],
    }
