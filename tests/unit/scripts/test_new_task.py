"""Branch-start preconditions without Git, disk, or clock access."""

from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from tests.helpers.hooks import load_script


@pytest.fixture
def new_task(repo_root: Path) -> ModuleType:
    """Load the task CLI without calling main."""
    return load_script(repo_root, "new_task")


@pytest.mark.parametrize(
    ("current", "dirty", "fetch", "behind", "reason"),
    [
        ("feat/old", "", 0, "0", "not on the mainline"),
        ("", "", 0, "0", "detached HEAD"),
        ("main", " M app.py", 0, "0", "dirty baseline"),
        ("main", "", 0, "1", "mainline behind"),
        ("main", "", 1, "0", "git fetch failed"),
        ("main", "", 0, "0", None),
    ],
)
def test_branch_preconditions_before_switch(
    new_task: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    current: str,
    dirty: str,
    fetch: int,
    behind: str,
    reason: str | None,
) -> None:
    """A branch is created only from a clean, refreshed mainline."""
    monkeypatch.setattr(new_task, "mainline", lambda: "main")

    def git(*args: str) -> Mock:
        output = {"branch": current, "status": dirty, "rev-list": behind}.get(args[0], "")
        return Mock(stdout=output, stderr="", returncode=fetch if args[0] == "fetch" else 0)

    run = Mock(side_effect=git)
    monkeypatch.setattr(new_task, "_git", run)
    result = new_task.start_branch("feat/020-build")
    if reason is None:
        assert result is None
        assert run.call_args.args == ("switch", "-c", "feat/020-build")
    else:
        assert reason in result
        assert all(call.args[0] != "switch" for call in run.call_args_list)
    if current != "main" or dirty:
        assert all(call.args[0] != "fetch" for call in run.call_args_list)
