"""The real formatter sorts imports while preserving newly added unused imports."""

from pathlib import Path

from tests.helpers.hooks import run_script


def test_import_sorting_keeps_unused_imports(repo_root: Path, tmp_path: Path) -> None:
    """The import-only fix must not silently remove imports before their uses are written."""
    source = tmp_path / "edited.py"
    source.write_text('import sys\nimport os\n\nprint("ready")\n', encoding="utf-8")
    result = run_script(
        repo_root, "autoformat", {"tool_input": {"file_path": str(source)}}, tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert source.read_text(encoding="utf-8") == 'import os\nimport sys\n\nprint("ready")\n'
    assert "autoformatted edited.py" in result.stdout
