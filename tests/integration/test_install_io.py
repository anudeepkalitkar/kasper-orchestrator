"""Integration tier for ``install.py``: real runs against temporary Claude homes.

The install source is always this repository; the destination is always a ``tmp_path``
home passed as ``--home``. Nothing here touches the user's real ``~/.claude``.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import install
from tests.helpers.cli import line_with, parse_kinds, run_cli
from tests.helpers.files import read_json
from tests.helpers.settings import hook_commands
from tests.helpers.source import make_source


def _run_install(
    repo_root: Path, home: Path, python: str = "python3", now: datetime | None = None
) -> tuple[int, str]:
    """Install into ``home`` and capture the report.

    Args:
        repo_root: The install source.
        home: The destination Claude home.
        python: The interpreter rendered into the hook commands.
        now: The instant that names any backup directory.

    Returns:
        The exit code and everything the run printed.
    """
    out = io.StringIO()
    code = install.install(repo_root, home, python, now=now, out=out)
    return code, out.getvalue()


def _settings(home: Path) -> dict[str, Any]:
    """The installed ``settings.json``."""
    return read_json(home / install.SETTINGS_NAME)


def _head_commit(repo_root: Path) -> str:
    """This checkout's HEAD commit, read independently of ``install.py`` (§7).

    Args:
        repo_root: The repository the install source came from.

    Returns:
        The full commit hash the manifest must record.
    """
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


# ---------------------------------------------------------------- owned_files


def test_owned_files_reads_every_owned_path_with_posix_keys(repo_root: Path) -> None:
    """§3: the owned tree keyed by POSIX relative path, ``__pycache__`` excluded."""
    files = install.owned_files(repo_root / ".claude")
    assert "CLAUDE.md" in files
    assert "agents/tester.md" in files
    assert "skills/kasper/SKILL.md" in files
    assert not any("__pycache__" in key for key in files)
    assert not any("\\" in key for key in files)


def test_owned_files_returns_the_bytes_on_disk(repo_root: Path) -> None:
    """The copy is verbatim, binary included."""
    files = install.owned_files(repo_root / ".claude")
    assert files["agents/tester.md"] == (repo_root / ".claude/agents/tester.md").read_bytes()
    assert files["sounds/notify.wav"] == (repo_root / ".claude/sounds/notify.wav").read_bytes()


def test_owned_files_excludes_paths_kasper_does_not_own(repo_root: Path) -> None:
    """Merged files are not owned files (§7)."""
    files = install.owned_files(repo_root / ".claude")
    assert install.SETTINGS_NAME not in files
    assert install.LEDGER_NAME not in files


# ---------------------------------------------------------------- fresh install


def test_fresh_install_writes_every_owned_file(repo_root: Path, home: Path) -> None:
    """Each owned file lands at the same relative path with identical bytes."""
    code, _ = _run_install(repo_root, home)
    assert code == 0
    for rel, content in install.owned_files(repo_root / ".claude").items():
        assert (home / rel).read_bytes() == content


def test_fresh_install_writes_settings_ledger_and_manifest(repo_root: Path, home: Path) -> None:
    """The two merged files and the manifest are created alongside the owned tree."""
    _run_install(repo_root, home)
    assert (home / install.SETTINGS_NAME).is_file()
    assert (home / install.LEDGER_NAME).is_file()
    assert (home / install.MANIFEST_NAME).is_file()


def test_fresh_install_creates_no_backup_directory(repo_root: Path, home: Path) -> None:
    """§6: nothing was overwritten, so there is nothing to back up."""
    _run_install(repo_root, home, now=None)
    assert list(home.glob("kasper-backup-*")) == []


def test_fresh_install_reports_every_owned_file_as_create(repo_root: Path, home: Path) -> None:
    """The report names one line per file (§8)."""
    _, out = _run_install(repo_root, home)
    kinds = parse_kinds(out)
    for rel in install.owned_files(repo_root / ".claude"):
        assert kinds[rel] == "create"


def test_installed_hooks_use_the_given_interpreter(repo_root: Path, home: Path) -> None:
    """§2/§11: the interpreter is rendered, ``$HOME`` stays literal."""
    _run_install(repo_root, home, python="python")
    kasper = [c for c in hook_commands(_settings(home)["hooks"]) if install.HOOK_MARKER in c]
    assert kasper, "no KASPER hooks were installed"
    for command in kasper:
        assert command.startswith("python ")
        assert '"$HOME/.claude/scripts/run_hook.py"' in command


def test_installed_ledger_matches_the_repository_seed(repo_root: Path, home: Path) -> None:
    """§5: absent at home, the seed is copied as is."""
    _run_install(repo_root, home)
    assert read_json(home / install.LEDGER_NAME) == read_json(
        repo_root / ".claude" / install.LEDGER_NAME
    )


# ---------------------------------------------------------------- manifest


def test_manifest_records_schema_source_and_interpreter(repo_root: Path, home: Path) -> None:
    """§7: schema 1, the source path, the commit or null, an ISO time, the interpreter."""
    _run_install(repo_root, home, python="python")
    manifest = read_json(home / install.MANIFEST_NAME)
    assert manifest["schema"] == 1
    assert manifest["source"] == str(repo_root)
    assert manifest["source_commit"] == _head_commit(repo_root)
    assert manifest["python"] == "python"
    datetime.fromisoformat(manifest["installed_at"])


def test_manifest_files_map_hashes_every_owned_file(repo_root: Path, home: Path) -> None:
    """The ``files`` map is exactly the owned tree, hashed."""
    _run_install(repo_root, home)
    owned = install.owned_files(repo_root / ".claude")
    files = read_json(home / install.MANIFEST_NAME)["files"]
    assert set(files) == set(owned)
    assert all(files[rel] == install.sha256_hex(content) for rel, content in owned.items())


def test_manifest_omits_the_merged_files(repo_root: Path, home: Path) -> None:
    """§7: settings and the ledger are merged, not owned, so they are not recorded."""
    _run_install(repo_root, home)
    files = read_json(home / install.MANIFEST_NAME)["files"]
    assert install.SETTINGS_NAME not in files
    assert install.LEDGER_NAME not in files


# ---------------------------------------------------------------- install over an existing home


def test_install_preserves_existing_settings_keys_and_hooks(
    repo_root: Path, home: Path, home_settings: dict[str, Any]
) -> None:
    """§4: the user's own keys and their non-KASPER handlers survive the merge."""
    home.mkdir(parents=True)
    (home / install.SETTINGS_NAME).write_text(json.dumps(home_settings), encoding="utf-8")
    _run_install(repo_root, home)
    merged = _settings(home)
    assert merged["model"] == "opus"
    assert merged["statusLine"] == {"type": "command", "command": "my-status.sh"}
    assert merged["permissions"]["additionalDirectories"] == ["/work"]
    assert "prettier --write" in hook_commands(merged["hooks"])


def test_install_preserves_existing_ledger_grants(
    repo_root: Path, home: Path, home_ledger: dict[str, Any]
) -> None:
    """§5: per-machine grants are never touched, and missing seed keys are added."""
    home.mkdir(parents=True)
    (home / install.LEDGER_NAME).write_text(json.dumps(home_ledger), encoding="utf-8")
    _run_install(repo_root, home)
    merged = read_json(home / install.LEDGER_NAME)
    assert merged["grants"] == home_ledger["grants"]
    assert merged["allow_keys"]["brew"] == "mine"
    assert "ls" in merged["allow_keys"]


def test_install_backs_up_settings_it_changes(
    repo_root: Path,
    home: Path,
    home_settings: dict[str, Any],
    fixed_now: datetime,
    backup_name: str,
) -> None:
    """§6: a settings file that would change is copied aside first, byte for byte."""
    home.mkdir(parents=True)
    original = json.dumps(home_settings)
    (home / install.SETTINGS_NAME).write_text(original, encoding="utf-8")
    _run_install(repo_root, home, now=fixed_now)
    assert (home / backup_name / install.SETTINGS_NAME).read_text(encoding="utf-8") == original


def test_second_identical_install_reports_everything_unchanged(
    repo_root: Path, home: Path, fixed_now: datetime
) -> None:
    """Re-running over an up-to-date home is a no-op."""
    _run_install(repo_root, home, now=fixed_now)
    _, out = _run_install(repo_root, home, now=fixed_now)
    kinds = parse_kinds(out)
    for rel in install.owned_files(repo_root / ".claude"):
        assert kinds[rel] == "unchanged"


def test_second_identical_install_creates_no_backup_directory(
    repo_root: Path, home: Path, fixed_now: datetime
) -> None:
    """§6: a run that changes nothing creates no backup directory."""
    _run_install(repo_root, home, now=fixed_now)
    _run_install(repo_root, home, now=fixed_now)
    assert list(home.glob("kasper-backup-*")) == []


def test_modified_owned_file_is_backed_up_then_overwritten(
    repo_root: Path, home: Path, fixed_now: datetime, backup_name: str
) -> None:
    """§6: the old bytes go to the timestamped backup, the new bytes go home."""
    _run_install(repo_root, home, now=fixed_now)
    target = home / "agents/tester.md"
    target.write_bytes(b"local edit")
    _, out = _run_install(repo_root, home, now=fixed_now)
    assert (home / backup_name / "agents/tester.md").read_bytes() == b"local edit"
    assert target.read_bytes() == (repo_root / ".claude/agents/tester.md").read_bytes()
    assert parse_kinds(out)["agents/tester.md"] == "overwrite"


# ---------------------------------------------------------------- dry run


def test_dry_run_writes_nothing_at_all(repo_root: Path, home: Path) -> None:
    """§8: not one file, not even the home directory."""
    out = io.StringIO()
    code = install.install(repo_root, home, "python3", dry_run=True, out=out)
    assert code == 0
    assert not home.exists()


def test_dry_run_prints_the_plan(repo_root: Path, home: Path) -> None:
    """§8: the plan it would carry out is still reported in full."""
    out = io.StringIO()
    install.install(repo_root, home, "python3", dry_run=True, out=out)
    kinds = parse_kinds(out.getvalue())
    for rel in install.owned_files(repo_root / ".claude"):
        assert kinds[rel] == "create"


def test_dry_run_over_an_installed_home_changes_nothing(
    repo_root: Path, installed_home: Path
) -> None:
    """A dry run never repairs — or backs up — a home it finds drifted."""
    (installed_home / "agents/tester.md").write_bytes(b"local edit")
    install.install(repo_root, installed_home, "python3", dry_run=True, out=io.StringIO())
    assert (installed_home / "agents/tester.md").read_bytes() == b"local edit"
    assert list(installed_home.glob("kasper-backup-*")) == []


# ---------------------------------------------------------------- status


def test_status_of_a_clean_install_is_zero(installed_home: Path) -> None:
    """§9: no drift, exit 0."""
    out = io.StringIO()
    assert install.status(installed_home, out=out) == 0


def test_status_reports_ok_for_every_recorded_file(installed_home: Path) -> None:
    """Each manifest entry is judged, not just counted."""
    out = io.StringIO()
    install.status(installed_home, out=out)
    kinds = parse_kinds(out.getvalue())
    assert kinds["agents/tester.md"] == "ok"


def test_status_names_a_modified_file_and_exits_one(installed_home: Path) -> None:
    """§9: any drift exits 1 and says which file drifted."""
    (installed_home / "agents/tester.md").write_bytes(b"local edit")
    out = io.StringIO()
    assert install.status(installed_home, out=out) == 1
    assert line_with(out.getvalue(), "agents/tester.md", "modified") is not None


def test_status_names_a_missing_file_and_exits_one(installed_home: Path) -> None:
    """A deleted owned file is drift too."""
    (installed_home / "agents/tester.md").unlink()
    out = io.StringIO()
    assert install.status(installed_home, out=out) == 1
    assert line_with(out.getvalue(), "agents/tester.md", "missing") is not None


def test_status_reports_the_kasper_hooks_are_present(installed_home: Path) -> None:
    """§9 also answers whether ``settings.json`` still carries the hooks — here, ``ok``."""
    out = io.StringIO()
    install.status(installed_home, out=out)
    assert line_with(out.getvalue(), f"ok  hooks in {install.SETTINGS_NAME}") is not None


def test_status_flags_settings_that_lost_the_kasper_hooks(installed_home: Path) -> None:
    """§9: hooks gone from settings means KASPER is not in force — drift, exit 1.

    The ADR requires status to report "whether settings.json carries the KASPER hooks"
    and to exit 1 on "any drift" without saying which side of the line this is; read here
    as drift, the reading that stops status calling an inert install clean.
    """
    settings_path = installed_home / install.SETTINGS_NAME
    data = _settings(installed_home)
    data["hooks"] = {}
    settings_path.write_text(json.dumps(data), encoding="utf-8")
    assert install.status(installed_home, out=io.StringIO()) == 1


def test_status_without_a_manifest_says_not_installed(home: Path) -> None:
    """§9: no manifest means not installed, exit 1."""
    home.mkdir(parents=True)
    out = io.StringIO()
    assert install.status(home, out=out) == 1
    assert "not installed" in out.getvalue().lower()


# ---------------------------------------------------------------- uninstall


def test_uninstall_of_a_clean_install_removes_every_owned_file(
    repo_root: Path, installed_home: Path, fixed_now: datetime
) -> None:
    """§10: an untouched file is deleted."""
    out = io.StringIO()
    assert install.uninstall(installed_home, now=fixed_now, out=out) == 0
    for rel in install.owned_files(repo_root / ".claude"):
        assert not (installed_home / rel).exists()


def test_uninstall_removes_the_manifest_and_the_emptied_directories(
    installed_home: Path, fixed_now: datetime
) -> None:
    """§10: directories left empty go, and the manifest goes with them."""
    install.uninstall(installed_home, now=fixed_now, out=io.StringIO())
    assert not (installed_home / install.MANIFEST_NAME).exists()
    for owned in install.OWNED_PATHS:
        assert not (installed_home / owned).exists()


def test_uninstall_strips_the_kasper_hooks_from_settings(
    installed_home: Path, fixed_now: datetime
) -> None:
    """§10: the same strip rule as §4, applied to the home file."""
    install.uninstall(installed_home, now=fixed_now, out=io.StringIO())
    commands = hook_commands(_settings(installed_home)["hooks"])
    assert not any(install.HOOK_MARKER in command for command in commands)


def test_uninstall_keeps_the_permissions_block_and_the_ledger(
    installed_home: Path, fixed_now: datetime
) -> None:
    """§10: both stay behind by design — the ledger holds the user's own grants."""
    install.uninstall(installed_home, now=fixed_now, out=io.StringIO())
    assert "Read" in _settings(installed_home)["permissions"]["allow"]
    assert (installed_home / install.LEDGER_NAME).is_file()


def test_uninstall_backs_up_settings_before_stripping(
    installed_home: Path, fixed_now: datetime, backup_name: str
) -> None:
    """§10: the settings file is backed up before it is rewritten."""
    original = (installed_home / install.SETTINGS_NAME).read_text(encoding="utf-8")
    install.uninstall(installed_home, now=fixed_now, out=io.StringIO())
    backup = installed_home / backup_name / install.SETTINGS_NAME
    assert backup.read_text(encoding="utf-8") == original


def test_uninstall_keeps_a_modified_file_and_exits_one(
    installed_home: Path, fixed_now: datetime
) -> None:
    """§10: anything modified is kept and reported, and the run exits 1."""
    target = installed_home / "agents/tester.md"
    target.write_bytes(b"local edit")
    out = io.StringIO()
    assert install.uninstall(installed_home, now=fixed_now, out=out) == 1
    assert target.read_bytes() == b"local edit"
    assert line_with(out.getvalue(), "agents/tester.md", "keep") is not None


# ---------------------------------------------------------------- main


def test_main_installs_into_the_home_it_is_given(home: Path) -> None:
    """The CLI end to end: ``--home`` targets any directory (§2)."""
    assert run_cli(install.main, ["--home", str(home)]) == 0
    assert (home / install.MANIFEST_NAME).is_file()
    assert (home / "agents/tester.md").is_file()


def test_main_renders_the_interpreter_it_is_given(home: Path) -> None:
    """``--python`` reaches the written hook commands."""
    run_cli(install.main, ["--home", str(home), "--python", "python"])
    kasper = [c for c in hook_commands(_settings(home)["hooks"]) if install.HOOK_MARKER in c]
    assert all(command.startswith("python ") for command in kasper)


def test_main_dry_run_writes_nothing(home: Path) -> None:
    """``--dry-run`` through the CLI is still write-free."""
    assert run_cli(install.main, ["--home", str(home), "--dry-run"]) == 0
    assert not home.exists()


def test_main_status_follows_the_install(home: Path) -> None:
    """``--status`` is 0 on a clean home and 1 once a file drifts."""
    run_cli(install.main, ["--home", str(home)])
    assert run_cli(install.main, ["--home", str(home), "--status"]) == 0
    (home / "agents/tester.md").write_bytes(b"local edit")
    assert run_cli(install.main, ["--home", str(home), "--status"]) == 1


def test_main_uninstall_returns_zero_on_a_clean_home(home: Path) -> None:
    """``--uninstall`` through the CLI removes what it installed."""
    run_cli(install.main, ["--home", str(home)])
    assert run_cli(install.main, ["--home", str(home), "--uninstall"]) == 0
    assert not (home / install.MANIFEST_NAME).exists()


def test_main_rejects_status_and_uninstall_together(home: Path) -> None:
    """§2: a usage error exits 2."""
    assert run_cli(install.main, ["--home", str(home), "--status", "--uninstall"]) == 2


def test_main_rejects_an_unknown_flag(home: Path) -> None:
    """An unrecognised option is a usage error, not a silent install."""
    assert run_cli(install.main, ["--home", str(home), "--nope"]) == 2


# ---------------------------------------------------------------- corrupt JSON at home


def test_install_refuses_a_corrupt_settings_file_and_writes_nothing(
    repo_root: Path, home: Path
) -> None:
    """A settings file that cannot be parsed stops the run — it is never overwritten (§4)."""
    home.mkdir(parents=True)
    settings_path = home / install.SETTINGS_NAME
    settings_path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        _run_install(repo_root, home)
    assert settings_path.read_text(encoding="utf-8") == "{ not json"
    assert not (home / install.MANIFEST_NAME).exists()
    assert not (home / "agents").exists()


def test_install_refuses_settings_that_are_not_a_json_object(repo_root: Path, home: Path) -> None:
    """Valid JSON of the wrong shape is refused just as loudly."""
    home.mkdir(parents=True)
    (home / install.SETTINGS_NAME).write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must hold a JSON object"):
        _run_install(repo_root, home)


def test_install_refuses_a_corrupt_ledger_and_writes_nothing(repo_root: Path, home: Path) -> None:
    """§5: a home ledger that cannot be parsed is left alone, not replaced by the seed."""
    home.mkdir(parents=True)
    ledger_path = home / install.LEDGER_NAME
    ledger_path.write_text("{ nope", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        _run_install(repo_root, home)
    assert ledger_path.read_text(encoding="utf-8") == "{ nope"
    assert not (home / install.MANIFEST_NAME).exists()


def test_main_reports_a_corrupt_settings_file_and_exits_one(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§2: through the CLI the same corruption is a named error on stderr, exit 1."""
    home.mkdir(parents=True)
    (home / install.SETTINGS_NAME).write_text("{ not json", encoding="utf-8")
    assert run_cli(install.main, ["--home", str(home)]) == 1
    assert install.SETTINGS_NAME in capsys.readouterr().err


def test_status_refuses_a_corrupt_manifest(installed_home: Path) -> None:
    """§9: an unreadable manifest is an error, not a clean report."""
    (installed_home / install.MANIFEST_NAME).write_text("{ truncated", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        install.status(installed_home, out=io.StringIO())


def test_status_rejects_a_manifest_without_a_files_map(installed_home: Path) -> None:
    """A JSON object that is not a KASPER manifest is refused by name."""
    (installed_home / install.MANIFEST_NAME).write_text('{"schema": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="not a KASPER manifest"):
        install.status(installed_home, out=io.StringIO())


def test_main_status_on_a_corrupt_manifest_exits_one(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI turns that error into exit 1 naming the manifest."""
    run_cli(install.main, ["--home", str(home)])
    (home / install.MANIFEST_NAME).write_text("{ truncated", encoding="utf-8")
    assert run_cli(install.main, ["--home", str(home), "--status"]) == 1
    assert install.MANIFEST_NAME in capsys.readouterr().err


def test_uninstall_refuses_a_corrupt_settings_file_and_removes_nothing(
    installed_home: Path,
) -> None:
    """§10: a settings file it cannot parse stops uninstall before it deletes anything."""
    (installed_home / install.SETTINGS_NAME).write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        install.uninstall(installed_home, out=io.StringIO())
    assert (installed_home / install.MANIFEST_NAME).is_file()
    assert (installed_home / "agents/tester.md").is_file()


# ---------------------------------------------------------------- absent pieces


def test_status_exits_one_when_settings_json_is_gone(installed_home: Path) -> None:
    """§9: no settings file at all means the hooks are not in force — drift, exit 1."""
    (installed_home / install.SETTINGS_NAME).unlink()
    out = io.StringIO()
    assert install.status(installed_home, out=out) == 1
    assert line_with(out.getvalue(), "hooks", "missing") is not None


def test_uninstall_without_a_manifest_says_not_installed(home: Path) -> None:
    """§10: nothing recorded means nothing to remove, exit 1."""
    home.mkdir(parents=True)
    out = io.StringIO()
    assert install.uninstall(home, out=out) == 1
    assert "not installed" in out.getvalue().lower()


# ---------------------------------------------------------------- the source checkout


def test_manifest_commit_is_null_when_git_is_unavailable(
    repo_root: Path, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7: a machine without git records null and installs anyway — no exception."""
    toolless = tmp_path / "no-tools"
    toolless.mkdir()
    monkeypatch.setenv("PATH", str(toolless))
    code, _ = _run_install(repo_root, home)
    assert code == 0
    assert read_json(home / install.MANIFEST_NAME)["source_commit"] is None


def test_manifest_commit_is_null_when_the_source_is_not_a_repository(
    home: Path, tmp_path: Path, kasper_settings: dict[str, Any], seed_ledger: dict[str, Any]
) -> None:
    """§7: an unpacked tarball is an ordinary source — null commit, install proceeds."""
    source = make_source(tmp_path / "source", kasper_settings, seed_ledger)
    code, _ = _run_install(source, home)
    assert code == 0
    assert read_json(home / install.MANIFEST_NAME)["source_commit"] is None


def test_owned_files_names_the_missing_path_in_a_partial_checkout(
    tmp_path: Path, kasper_settings: dict[str, Any], seed_ledger: dict[str, Any]
) -> None:
    """§3: an owned path absent from the source is an error that names the path."""
    source = make_source(tmp_path / "source", kasper_settings, seed_ledger, omit="sounds")
    with pytest.raises(FileNotFoundError, match="sounds"):
        install.owned_files(source / ".claude")


# ---------------------------------------------------------------- writes and non-writes


def test_install_backs_up_a_ledger_it_changes(
    repo_root: Path,
    home: Path,
    home_ledger: dict[str, Any],
    fixed_now: datetime,
    backup_name: str,
) -> None:
    """§6: the ledger, like settings, is copied aside before it is merged."""
    home.mkdir(parents=True)
    original = json.dumps(home_ledger)
    (home / install.LEDGER_NAME).write_text(original, encoding="utf-8")
    _run_install(repo_root, home, now=fixed_now)
    assert (home / backup_name / install.LEDGER_NAME).read_text(encoding="utf-8") == original


def test_install_leaves_no_temporary_files_behind(repo_root: Path, home: Path) -> None:
    """§4's atomic write replaces its temp file; none may survive the run."""
    _run_install(repo_root, home)
    assert list(home.rglob("*.kasper-tmp")) == []


def test_dry_run_leaves_the_merged_files_byte_identical(
    repo_root: Path, installed_home: Path
) -> None:
    """§8: a dry run that would re-render the hooks still writes neither merged file."""
    names = (install.SETTINGS_NAME, install.LEDGER_NAME, install.MANIFEST_NAME)
    before = {name: (installed_home / name).read_bytes() for name in names}
    install.install(repo_root, installed_home, "python", dry_run=True, out=io.StringIO())
    assert {name: (installed_home / name).read_bytes() for name in names} == before


def test_uninstall_keeps_an_owned_directory_holding_a_user_file(
    installed_home: Path, fixed_now: datetime
) -> None:
    """§10: only *emptied* directories go — one holding the user's own file stays."""
    mine = installed_home / "agents" / "mine.md"
    mine.write_text("my own agent\n", encoding="utf-8")
    install.uninstall(installed_home, now=fixed_now, out=io.StringIO())
    assert mine.read_text(encoding="utf-8") == "my own agent\n"


def test_uninstall_leaves_directories_kasper_does_not_own(
    installed_home: Path, fixed_now: datetime
) -> None:
    """A directory outside the owned paths is never the uninstaller's business."""
    mine = installed_home / "projects" / "notes.md"
    mine.parent.mkdir()
    mine.write_text("mine\n", encoding="utf-8")
    install.uninstall(installed_home, now=fixed_now, out=io.StringIO())
    assert mine.is_file()


# ================================================================== round 2
# The review fixes: manifest-key containment, shape checking, stale pruning,
# backup naming, symlinked owned paths, and the atomic-write guarantees.


def _record_manifest_key(home: Path, rel: str, digest: str) -> None:
    """Add one entry to the home manifest's ``files`` map, as a tampered manifest would."""
    manifest = read_json(home / install.MANIFEST_NAME)
    manifest["files"][rel] = digest
    (home / install.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")


def _plant_victim(tmp_path: Path) -> Path:
    """A file next to — never inside — the Claude home, which nothing may touch."""
    victim = tmp_path / "victim.txt"
    victim.write_text("not yours\n", encoding="utf-8")
    return victim


def _escape_key(shape: str, victim: Path) -> str:
    """A manifest key that points at ``victim``, outside the home, in one of three ways."""
    return str(victim) if shape == "absolute" else shape


#: Manifest keys that escape the home: two lexical, one absolute (review B1).
ESCAPING_KEYS = ("../victim.txt", "a/../../victim.txt", "absolute")


# ---------------------------------------------------------------- manifest key containment


@pytest.mark.parametrize("shape", ESCAPING_KEYS)
def test_install_refuses_a_manifest_key_outside_the_home(
    repo_root: Path, installed_home: Path, tmp_path: Path, shape: str
) -> None:
    """A manifest recording a path outside the home is refused, not partly obeyed."""
    victim = _plant_victim(tmp_path)
    _record_manifest_key(installed_home, _escape_key(shape, victim), install.sha256_hex(b""))
    with pytest.raises(ValueError, match="outside"):
        _run_install(repo_root, installed_home)
    assert victim.read_text(encoding="utf-8") == "not yours\n"


@pytest.mark.parametrize("shape", ESCAPING_KEYS)
def test_status_refuses_a_manifest_key_outside_the_home(
    installed_home: Path, tmp_path: Path, shape: str
) -> None:
    """§9 never hashes a path the manifest should not be able to name."""
    victim = _plant_victim(tmp_path)
    _record_manifest_key(installed_home, _escape_key(shape, victim), install.sha256_hex(b""))
    with pytest.raises(ValueError, match="outside"):
        install.status(installed_home, out=io.StringIO())
    assert victim.read_text(encoding="utf-8") == "not yours\n"


@pytest.mark.parametrize("shape", ESCAPING_KEYS)
def test_uninstall_refuses_a_manifest_key_outside_the_home_and_deletes_nothing(
    installed_home: Path, tmp_path: Path, shape: str
) -> None:
    """The hash gate is worthless if a relpath can escape: uninstall stops before deleting."""
    victim = _plant_victim(tmp_path)
    digest = install.sha256_hex(victim.read_bytes())
    _record_manifest_key(installed_home, _escape_key(shape, victim), digest)
    with pytest.raises(ValueError, match="outside"):
        install.uninstall(installed_home, out=io.StringIO())
    assert victim.read_text(encoding="utf-8") == "not yours\n"
    assert (installed_home / install.MANIFEST_NAME).is_file()
    assert (installed_home / "agents/tester.md").is_file()


# ---------------------------------------------------------------- malformed shapes


#: Settings documents that parse as JSON but not as anything this script can merge.
MALFORMED_SETTINGS: tuple[dict[str, Any], ...] = (
    {"hooks": []},
    {"hooks": {"PreToolUse": [{"matcher": "Bash"}]}},
    {"hooks": {"Stop": {}}},
    {"hooks": {"Stop": ["not a group"]}},
    {"hooks": {"Stop": [{"hooks": [{"command": 7}]}]}},
    {"permissions": {"allow": "Read"}},
    {"permissions": []},
)

#: Ledgers whose containers are not the containers §5 merges. The last two carry a
#: ``pattern`` that is not a string — it becomes a set element in ``merge_ledger``, so an
#: unhashable one used to crash past ``main`` with a ``TypeError``.
MALFORMED_LEDGERS: tuple[dict[str, Any], ...] = (
    {"allow_keys": []},
    {"ask_patterns": [1, 2]},
    {"allow_patterns": {}},
    {"ask_patterns": [{"pattern": ["x"], "note": "a list is not a pattern"}]},
    {"allow_patterns": [{"pattern": 7}]},
)


@pytest.mark.parametrize("document", MALFORMED_SETTINGS)
def test_install_refuses_malformed_settings_and_writes_nothing(
    repo_root: Path, home: Path, document: dict[str, Any]
) -> None:
    """Valid JSON of the wrong shape is one clear error before any byte is written."""
    home.mkdir(parents=True)
    settings_path = home / install.SETTINGS_NAME
    original = json.dumps(document)
    settings_path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected shape"):
        _run_install(repo_root, home)
    assert settings_path.read_text(encoding="utf-8") == original
    assert not (home / install.MANIFEST_NAME).exists()
    assert not (home / "agents").exists()


@pytest.mark.parametrize("document", MALFORMED_LEDGERS)
def test_install_refuses_a_malformed_ledger_and_writes_nothing(
    repo_root: Path, home: Path, document: dict[str, Any]
) -> None:
    """§5's containers are checked at the boundary, not half way through the merge."""
    home.mkdir(parents=True)
    ledger_path = home / install.LEDGER_NAME
    original = json.dumps(document)
    ledger_path.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected shape"):
        _run_install(repo_root, home)
    assert ledger_path.read_text(encoding="utf-8") == original
    assert not (home / install.MANIFEST_NAME).exists()


def test_status_refuses_a_manifest_hash_that_is_not_a_string(installed_home: Path) -> None:
    """A ``files`` map is path -> hex digest; anything else is not a KASPER manifest."""
    manifest = read_json(installed_home / install.MANIFEST_NAME)
    manifest["files"]["agents/tester.md"] = 7
    (installed_home / install.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a string"):
        install.status(installed_home, out=io.StringIO())


def test_main_reports_malformed_settings_and_exits_one(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§2: the CLI turns a shape error into one stderr line and exit 1, not a traceback."""
    home.mkdir(parents=True)
    (home / install.SETTINGS_NAME).write_text('{"hooks": []}', encoding="utf-8")
    assert run_cli(install.main, ["--home", str(home)]) == 1
    captured = capsys.readouterr()
    assert "unexpected shape" in captured.err
    assert install.SETTINGS_NAME in captured.err


# ---------------------------------------------------------------- stale owned files


def _record_stale_file(home: Path, rel: str, content: bytes) -> Path:
    """Plant a file the previous manifest recorded but the source no longer ships."""
    target = home / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    _record_manifest_key(home, rel, install.sha256_hex(content))
    return target


def test_update_removes_and_backs_up_a_file_the_source_no_longer_ships(
    repo_root: Path, installed_home: Path, fixed_now: datetime, backup_name: str
) -> None:
    """An upstream rename must not leave a stale agent behind that Claude Code still reads."""
    stale = _record_stale_file(installed_home, "agents/retired.md", b"old agent")
    _, out = _run_install(repo_root, installed_home, now=fixed_now)
    assert line_with(out, "remove", "agents/retired.md") is not None
    assert not stale.exists()
    assert (installed_home / backup_name / "agents/retired.md").read_bytes() == b"old agent"


def test_update_stops_recording_the_file_it_removed(
    repo_root: Path, installed_home: Path, fixed_now: datetime
) -> None:
    """The new manifest describes the new source, so status no longer looks for it."""
    _record_stale_file(installed_home, "agents/retired.md", b"old agent")
    _run_install(repo_root, installed_home, now=fixed_now)
    assert "agents/retired.md" not in read_json(installed_home / install.MANIFEST_NAME)["files"]
    assert install.status(installed_home, out=io.StringIO()) == 0


def test_update_prunes_a_directory_its_removal_emptied(
    repo_root: Path, installed_home: Path, fixed_now: datetime
) -> None:
    """The emptied directory goes; a sibling that still holds owned files stays."""
    _record_stale_file(installed_home, "skills/gone/SKILL.md", b"retired skill")
    _run_install(repo_root, installed_home, now=fixed_now)
    assert not (installed_home / "skills/gone").exists()
    assert (installed_home / "skills/kasper/SKILL.md").is_file()


def test_update_keeps_a_user_file_in_the_directory_it_prunes(
    repo_root: Path, installed_home: Path, fixed_now: datetime
) -> None:
    """A directory is only pruned when it is empty — the user's own file keeps it alive."""
    _record_stale_file(installed_home, "skills/gone/SKILL.md", b"retired skill")
    mine = installed_home / "skills/gone/mine.md"
    mine.write_text("my own skill\n", encoding="utf-8")
    _run_install(repo_root, installed_home, now=fixed_now)
    assert mine.read_text(encoding="utf-8") == "my own skill\n"


def test_dry_run_lists_a_stale_file_without_removing_it(
    repo_root: Path, installed_home: Path
) -> None:
    """§8: the removal is planned aloud and nothing is deleted or backed up."""
    stale = _record_stale_file(installed_home, "agents/retired.md", b"old agent")
    out = io.StringIO()
    install.install(repo_root, installed_home, "python3", dry_run=True, out=out)
    assert line_with(out.getvalue(), "remove", "agents/retired.md") is not None
    assert stale.read_bytes() == b"old agent"
    assert list(installed_home.glob("kasper-backup-*")) == []


# ---------------------------------------------------------------- backups


def test_two_backups_in_the_same_second_do_not_overwrite_each_other(
    repo_root: Path, installed_home: Path, fixed_now: datetime, backup_name: str
) -> None:
    """§6: a second run in the same second gets its own directory, so nothing is lost."""
    target = installed_home / "agents/tester.md"
    target.write_bytes(b"edit one")
    _run_install(repo_root, installed_home, now=fixed_now)
    target.write_bytes(b"edit two")
    _run_install(repo_root, installed_home, now=fixed_now)
    assert (installed_home / backup_name / "agents/tester.md").read_bytes() == b"edit one"
    assert (installed_home / f"{backup_name}-2" / "agents/tester.md").read_bytes() == b"edit two"


def test_dry_run_names_every_file_it_would_back_up(repo_root: Path, installed_home: Path) -> None:
    """§8's plan is "overwrite *with backup*" — the dry run says which files that means."""
    (installed_home / "agents/tester.md").write_bytes(b"local edit")
    out = io.StringIO()
    install.install(repo_root, installed_home, "python", dry_run=True, out=out)
    assert line_with(out.getvalue(), "backup", "agents/tester.md") is not None
    assert line_with(out.getvalue(), "backup", install.SETTINGS_NAME) is not None
    assert list(installed_home.glob("kasper-backup-*")) == []


def test_uninstall_names_the_settings_backup_and_its_directory(
    installed_home: Path, fixed_now: datetime, backup_name: str
) -> None:
    """§10's report names the file it copied aside and the directory it went to."""
    out = io.StringIO()
    install.uninstall(installed_home, now=fixed_now, out=out)
    assert line_with(out.getvalue(), "backup", install.SETTINGS_NAME) is not None
    assert line_with(out.getvalue(), "backup dir", backup_name) is not None


# ---------------------------------------------------------------- symlinks and file modes


def test_install_replaces_a_symlinked_owned_path_and_leaves_its_target_alone(
    repo_root: Path, installed_home: Path, tmp_path: Path, fixed_now: datetime, backup_name: str
) -> None:
    """An owned path symlinked into a dotfiles repo is replaced, never written through."""
    outside = tmp_path / "dotfiles" / "CLAUDE.md"
    outside.parent.mkdir()
    outside.write_text("my own global instructions\n", encoding="utf-8")
    linked = installed_home / "CLAUDE.md"
    linked.unlink()
    linked.symlink_to(outside)
    _run_install(repo_root, installed_home, now=fixed_now)
    assert not linked.is_symlink()
    assert linked.read_bytes() == (repo_root / ".claude/CLAUDE.md").read_bytes()
    assert outside.read_text(encoding="utf-8") == "my own global instructions\n"
    assert (installed_home / backup_name / "CLAUDE.md").read_text(encoding="utf-8") == (
        "my own global instructions\n"
    )


def test_a_merge_keeps_the_settings_files_permission_bits(
    repo_root: Path, installed_home: Path
) -> None:
    """A ``chmod 600`` settings file must not come back world-readable (review NB-4)."""
    settings_path = installed_home / install.SETTINGS_NAME
    settings_path.chmod(0o600)
    _run_install(repo_root, installed_home, python="python")
    assert settings_path.stat().st_mode & 0o777 == 0o600


def test_a_failed_atomic_write_leaves_no_temp_file(
    repo_root: Path, installed_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temp file never survives a failure in the user's home (review NB-5)."""

    def boom(src: object, dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        _run_install(repo_root, installed_home, python="python")
    assert list(installed_home.rglob(f"*{install.SETTINGS_NAME}.kasper-tmp")) == []
    assert list(installed_home.rglob("*.kasper-tmp")) == []


# ---------------------------------------------------------------- remaining review gaps


def test_owned_files_rejects_a_source_directory_that_does_not_exist(tmp_path: Path) -> None:
    """§3: no ``.claude/`` at all fails on the first owned path, by name."""
    with pytest.raises(FileNotFoundError, match="agents"):
        install.owned_files(tmp_path / "nowhere" / ".claude")


def test_reinstall_with_a_different_interpreter_rewrites_the_hook_commands(
    repo_root: Path, installed_home: Path
) -> None:
    """The marker is interpreter-independent, so a re-render replaces rather than doubles."""
    before = [c for c in hook_commands(_settings(installed_home)["hooks"]) if "run_hook.py" in c]
    _run_install(repo_root, installed_home, python="python")
    after = [c for c in hook_commands(_settings(installed_home)["hooks"]) if "run_hook.py" in c]
    assert len(after) == len(before)
    assert all(command.startswith("python ") for command in after)
    assert not any(command.startswith("python3 ") for command in after)


def test_uninstall_without_a_manifest_exits_one_from_a_real_process(
    repo_root: Path, home: Path
) -> None:
    """§10 through the actual command line: exit 1 and the report on stdout, no traceback.

    A real process, not :func:`install.main` in this one: it is the only form that proves
    the shipped script exits 1 from `` __main__`` with the report on the process's own
    stdout and nothing on stderr. The in-process twin is
    :func:`test_main_status_report_reaches_in_process_capture`.
    """
    home.mkdir(parents=True)
    completed = subprocess.run(
        [sys.executable, str(repo_root / "install.py"), "--home", str(home), "--uninstall"],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "not installed" in completed.stdout.lower()
    assert completed.stderr == ""


# ================================================================== round 3
# A resolved-at-call-time report stream, the tolerant repair path, the last
# shape check, and symlinked owned *directories*.


def test_main_status_report_reaches_in_process_capture(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report stream is chosen when the function runs, so a test can capture it."""
    home.mkdir(parents=True)
    assert run_cli(install.main, ["--home", str(home), "--status"]) == 1
    assert "not installed" in capsys.readouterr().out.lower()


def test_main_reports_a_ledger_pattern_that_is_not_a_string_and_writes_nothing(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unhashable ``pattern`` is refused at the boundary, not deep inside the merge."""
    home.mkdir(parents=True)
    ledger_path = home / install.LEDGER_NAME
    original = json.dumps({"ask_patterns": [{"pattern": ["x"]}]})
    ledger_path.write_text(original, encoding="utf-8")
    assert run_cli(install.main, ["--home", str(home)]) == 1
    captured = capsys.readouterr()
    assert "unexpected shape" in captured.err
    assert install.LEDGER_NAME in captured.err
    assert ledger_path.read_text(encoding="utf-8") == original
    assert not (home / install.MANIFEST_NAME).exists()
    assert not (home / "agents").exists()


# ---------------------------------------------------------------- the repair path


def test_install_over_an_unreadable_manifest_repairs_the_home(
    repo_root: Path, installed_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An install is the repair path: a truncated manifest costs the prune, not the run."""
    (installed_home / "agents/tester.md").write_bytes(b"local edit")
    (installed_home / install.MANIFEST_NAME).write_text("{ not json", encoding="utf-8")
    code, _ = _run_install(repo_root, installed_home)
    assert code == 0
    assert "ignoring an unreadable manifest" in capsys.readouterr().err
    assert (installed_home / "agents/tester.md").read_bytes() == (
        repo_root / ".claude/agents/tester.md"
    ).read_bytes()
    assert install.status(installed_home, out=io.StringIO()) == 0


def test_install_over_a_manifest_that_is_not_a_kasper_manifest_repairs_the_home(
    repo_root: Path, installed_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A foreign JSON object where the manifest should be is the same tolerated case."""
    (installed_home / install.MANIFEST_NAME).write_text('{"schema": 1}', encoding="utf-8")
    code, _ = _run_install(repo_root, installed_home)
    assert code == 0
    assert "ignoring an unreadable manifest" in capsys.readouterr().err
    assert read_json(installed_home / install.MANIFEST_NAME)["files"]
    assert install.status(installed_home, out=io.StringIO()) == 0


def test_main_install_over_a_manifest_key_outside_the_home_still_exits_one(
    installed_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Tolerating an unreadable manifest must not tolerate an escaping key: still fatal."""
    victim = _plant_victim(tmp_path)
    _record_manifest_key(installed_home, "../victim.txt", install.sha256_hex(b""))
    assert run_cli(install.main, ["--home", str(installed_home)]) == 1
    assert "outside" in capsys.readouterr().err
    assert victim.read_text(encoding="utf-8") == "not yours\n"


def test_uninstall_still_refuses_an_unreadable_manifest(installed_home: Path) -> None:
    """§10 repairs nothing, so it stays strict where an install is forgiving."""
    (installed_home / install.MANIFEST_NAME).write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        install.uninstall(installed_home, out=io.StringIO())
    assert (installed_home / "agents/tester.md").is_file()


# ---------------------------------------------------------------- symlinked owned directories


def test_uninstall_leaves_a_symlinked_owned_directory_and_its_target_alone(
    installed_home: Path, tmp_path: Path, fixed_now: datetime
) -> None:
    """A symlinked owned directory is the user's arrangement: never walked, never pruned."""
    outside = tmp_path / "media"
    outside.mkdir()
    (outside / "mine.wav").write_bytes(b"mine")
    sounds = installed_home / "sounds"
    for child in sorted(sounds.iterdir()):
        child.unlink()
    sounds.rmdir()
    sounds.symlink_to(outside, target_is_directory=True)
    out = io.StringIO()
    assert install.uninstall(installed_home, now=fixed_now, out=out) == 0
    assert not (installed_home / install.MANIFEST_NAME).exists()
    assert sounds.is_symlink()
    assert (outside / "mine.wav").read_bytes() == b"mine"
