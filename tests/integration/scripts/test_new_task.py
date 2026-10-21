"""Task titles received through real stdin without shell interpolation."""

from pathlib import Path

import pytest

from tests.helpers.hooks import run_script


def test_stdin_title_preserved(repo_root: Path, tmp_path: Path) -> None:
    """The scaffold retains quotes and shell syntax as literal title text."""
    title = 'Build "quotes" $(touch dollar-marker) `touch tick-marker`'
    result = run_script(repo_root, "new_task", title + "\n", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    docs = list((tmp_path / "tasks").glob("*.md"))
    assert len(docs) == 1
    assert title in docs[0].read_text(encoding="utf-8")
    assert not (tmp_path / "dollar-marker").exists()
    assert not (tmp_path / "tick-marker").exists()


@pytest.mark.parametrize("text", ["", " \n\t"])
def test_empty_stdin_creates_no_task(repo_root: Path, tmp_path: Path, text: str) -> None:
    """Missing title is a usage error with no directory creation."""
    result = run_script(repo_root, "new_task", text, tmp_path)
    assert result.returncode == 2
    assert "title is required" in result.stderr
    assert not (tmp_path / "tasks").exists()
