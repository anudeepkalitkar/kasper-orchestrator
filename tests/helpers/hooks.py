"""Loading and running the ``.claude/scripts/`` hook scripts from a test.

``.claude/`` is not an importable package, so a hook script is loaded by path. A hook whose
``main()`` reads ``Path.home()`` — every one of them does, to find or guard the global
config — is driven as its own process with ``HOME`` and its Windows twin ``USERPROFILE``
pointed at a temporary directory, so no test can reach the user's real ``~/.claude``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

#: A hook that hangs is a failed test, not a hung suite.
_TIMEOUT_SECONDS = 60


def script_path(repo_root: Path, name: str) -> Path:
    """The path of one hook script in this checkout.

    Args:
        repo_root: The repository root.
        name: The script's stem, as ``run_hook.py`` names it (``project_dirs``).

    Returns:
        The path to ``<repo_root>/.claude/scripts/<name>.py``.
    """
    return repo_root / ".claude" / "scripts" / f"{name}.py"


def load_script(repo_root: Path, name: str) -> ModuleType:
    """Import a hook script by path, under a name of its own.

    Args:
        repo_root: The repository root.
        name: The script's stem.

    Returns:
        The executed module, ready to call functions on.
    """
    path = script_path(repo_root, name)
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_script(
    repo_root: Path,
    name: str,
    payload: object,
    home: Path,
) -> subprocess.CompletedProcess[str]:
    """Run a hook script end to end, with the user's home replaced by ``home``.

    The process is started *in* ``home`` as well, so a script that falls back to
    ``Path.cwd()`` when its payload names no directory still cannot reach the checkout.

    Args:
        repo_root: The repository root the script is read from.
        name: The script's stem.
        payload: The hook event — a mapping (or any object) is JSON-encoded onto stdin, a
            ``str`` is written verbatim so a malformed payload can be tested.
        home: The temporary directory ``HOME``/``USERPROFILE`` point at; created if absent.

    Returns:
        The finished process, with ``stdout``, ``stderr`` and ``returncode``.
    """
    home.mkdir(parents=True, exist_ok=True)
    stdin_text = payload if isinstance(payload, str) else json.dumps(payload)
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    return subprocess.run(
        [sys.executable, str(script_path(repo_root, name))],
        input=stdin_text,
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )
