"""Hook trust decisions with an in-memory set of existing files."""

from pathlib import Path
from types import ModuleType

import pytest


@pytest.mark.parametrize(
    ("manifest", "trusted"),
    [
        (None, False),
        ('{"source": "/elsewhere"}', False),
        ('{"source": "/project"}', True),
        ('{"source": "/alias"}', True),
        ('{broken', False),
        ('[]', False),
        ('{}', False),
        ('{"source": ""}', False),
        ('{"source": 42}', False),
    ],
)
@pytest.mark.parametrize("installer", [False, True])
@pytest.mark.parametrize("skill", [False, True])
@pytest.mark.parametrize("local_hook", [False, True])
@pytest.mark.parametrize("home_hook", [False, True])
def test_project_hook_requires_trusted_home_manifest(
    run_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    manifest: str | None,
    trusted: bool,
    installer: bool,
    skill: bool,
    local_hook: bool,
    home_hook: bool,
) -> None:
    """Only the home install record authorizes a project hook, regardless of markers."""
    project, home = Path("/project"), Path("/home")
    local = project / ".claude/scripts/permission_recorder.py"
    fallback = home / ".claude/scripts/permission_recorder.py"
    files = {
        path
        for path, exists in [
            (project / "install.py", installer),
            (project / ".claude/skills/kasper/SKILL.md", skill),
            (local, local_hook),
            (fallback, home_hook),
        ]
        if exists
    }
    monkeypatch.setattr(Path, "is_file", lambda path: path in files)
    def read_text(path: Path, encoding: str = "utf-8") -> str:
        assert path == home / ".claude/kasper-manifest.json"
        if manifest is None:
            raise FileNotFoundError(path)
        return manifest

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "resolve", lambda path: project if path == Path("/alias") else path)
    assert run_hook.is_kasper_source(project, home) is trusted
    expected = local if trusted and local_hook else fallback if home_hook else None
    assert run_hook.hook_script("permission_recorder", str(project), home) == expected
    assert run_hook.hook_script("permission_recorder", None, home) == (
        fallback if home_hook else None
    )
