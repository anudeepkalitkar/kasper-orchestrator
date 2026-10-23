"""Task 022: inspect the shipped settings on disk, so these are integration tests."""

from pathlib import Path
from typing import Any

import pytest

import install
from tests.helpers.files import read_json
from tests.helpers.settings import ORIGINAL_ALLOW_RULES, ROUTINE_ALLOW_RULES


@pytest.fixture
def source_permissions(repo_root: Path) -> dict[str, list[str]]:
    """Parse the actual source JSON and return its permission block."""
    settings = read_json(repo_root / ".claude" / "settings.json")
    assert isinstance(settings, dict)
    permissions = settings["permissions"]
    assert isinstance(permissions, dict)
    for name in ("allow", "deny"):
        assert isinstance(permissions[name], list)
        assert all(isinstance(rule, str) for rule in permissions[name])
    return permissions


@pytest.mark.parametrize("rule", (*ORIGINAL_ALLOW_RULES, *ROUTINE_ALLOW_RULES))
def test_source_settings_allow_required_rule(
    source_permissions: dict[str, list[str]], rule: str
) -> None:
    """Each requested operation and all three original permissions must ship."""
    assert rule in source_permissions["allow"]


def test_source_settings_allow_only_the_narrow_requested_forms(
    source_permissions: dict[str, list[str]],
) -> None:
    """Reject broad wildcards, extra commands, and alternate permission forms."""
    assert set(source_permissions["allow"]) == set(ORIGINAL_ALLOW_RULES + ROUTINE_ALLOW_RULES)


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git merge",
        "git reset",
        "git clean",
        "git checkout",
        "git stash",
        "gh pr merge",
        "gh api",
    ],
)
def test_source_settings_do_not_allow_guarded_operations(
    source_permissions: dict[str, list[str]], command: str
) -> None:
    """Guarded operations must not gain an allow rule with any argument pattern."""
    assert not any(rule.startswith(f"Bash({command}") for rule in source_permissions["allow"])


def test_source_settings_deny_list_is_unchanged(
    source_permissions: dict[str, list[str]],
) -> None:
    """Pin the complete pre-task deny list, including shared branches and force pushes."""
    assert source_permissions["deny"] == [
        "Bash(git push --force*)",
        "Bash(git push -f *)",
        "Bash(git push origin development*)",
        "Bash(git push origin main*)",
        "Bash(git push origin master*)",
        "Bash(git push origin qa*)",
        "Bash(rm -fr *)",
        "Bash(rm -rf *)",
        "Bash(sudo *)",
        "Edit(~/.aws/**)",
        "Edit(~/.gnupg/**)",
        "Edit(~/.ssh/**)",
        "Edit(//etc/**)",
        "Edit(//tmp/**)",
        "Read(~/.aws/**)",
        "Read(~/.docker/config.json)",
        "Read(~/.gnupg/**)",
        "Read(~/.kube/**)",
        "Read(~/.netrc)",
        "Read(~/.ssh/**)",
        "Read(//etc/**)",
    ]


def test_actual_source_permissions_merge_with_home_permissions(
    source_permissions: dict[str, list[str]],
    home_settings: dict[str, Any],  # JSON settings permit arbitrary user-defined keys.
) -> None:
    """The shipped allow and deny rules join existing home entries on installation."""
    merged = install.merge_settings(home_settings, {"permissions": source_permissions}, "python3")
    for name in ("allow", "deny"):
        assert set(merged["permissions"][name]) == (
            set(home_settings["permissions"][name]) | set(source_permissions[name])
        )
