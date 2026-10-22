"""Overnight state transitions with an in-memory filesystem and explicit clock."""

import datetime
import io
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from tests.helpers.hooks import load_script

NOW = datetime.datetime(2026, 10, 22, 12)
ROOT = Path("/project")
BACKLOG = ROOT / "tasks/BACKLOG.md"
SCRATCH = ROOT / "claude-temp/sessions/session"
HEADER = "overnight: on\nsession: session\n"


@pytest.fixture
def overnight(repo_root: Path) -> ModuleType:
    """Load the hook without running its entry point."""
    return load_script(repo_root, "overnight")


@pytest.fixture
def files(monkeypatch: pytest.MonkeyPatch, overnight: ModuleType) -> dict[Path, str]:
    """Keep all hook reads, writes, and directory creation in memory."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(overnight, "project_root", lambda cwd: ROOT)
    monkeypatch.setattr(overnight.os, "access", Mock(return_value=False))
    contents = {BACKLOG: HEADER + "- [ ] first\n"}

    def read(path: Path, encoding: str = "utf-8") -> str:
        return contents[path]

    def write(path: Path, text: str, encoding: str = "utf-8") -> int:
        contents[path] = text
        return len(text)

    monkeypatch.setattr(Path, "exists", lambda path: path in contents)
    monkeypatch.setattr(Path, "is_file", lambda path: path in contents)
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(Path, "mkdir", Mock())
    monkeypatch.setattr(Path, "open", Mock(side_effect=FileNotFoundError("no transcript")))
    return contents


@pytest.mark.parametrize("active", [True, False])
def test_sixtieth_block_ends_then_stays_silent(
    overnight: ModuleType, files: dict[Path, str], active: bool
) -> None:
    """Stop-hook recursion must not bypass the final report opportunity."""
    payload = {"cwd": str(ROOT), "session_id": "session", "stop_hook_active": active}
    for count in range(1, 61):
        decision = json.loads(overnight.decide(payload, NOW))
        assert decision["decision"] == "block"
        assert files[SCRATCH / "overnight.count"] == str(count)
        if count < 60:
            assert "first open" in decision["reason"]
            assert SCRATCH / "overnight.ended" not in files
        else:
            assert "cap reached" in decision["reason"]
            assert "append tasks/morning-2026-10-22.md" in decision["reason"]
            assert "overnight: off" in decision["reason"]
    del files[BACKLOG]
    assert overnight.decide(payload, NOW) is None


@pytest.mark.parametrize("count", ["60", "61"])
def test_terminal_counter_after_ownership_precedes_ending_rules(
    overnight: ModuleType, files: dict[Path, str], monkeypatch: pytest.MonkeyPatch, count: str
) -> None:
    """An owned capped session must still request the final morning report."""
    files[SCRATCH / "overnight.count"] = count
    files[BACKLOG] = HEADER + "until: unreadable\n"
    original = Path.read_text
    reads: list[Path] = []

    def read(path: Path, encoding: str = "utf-8") -> str:
        reads.append(path)
        return original(path, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", read)
    payload = {"cwd": str(ROOT), "session_id": "session"}
    decision = json.loads(overnight.decide(payload, NOW))
    assert decision["decision"] == "block"
    assert "append tasks/morning-2026-10-22.md" in decision["reason"]
    assert "overnight: off" in decision["reason"]
    assert reads == [BACKLOG, SCRATCH / "overnight.count"]
    assert files[SCRATCH / "overnight.ended"] == NOW.isoformat()
    assert overnight.decide(payload, NOW) is None


@pytest.mark.parametrize("session", [None, "", "..", "../escape", "/absolute", "other"])
def test_session_scoping_has_no_writes(
    overnight: ModuleType, files: dict[Path, str], session: str | None
) -> None:
    """Invalid and unrelated sessions cannot continue or mutate the active night."""
    before = files.copy()
    assert overnight.decide({"cwd": str(ROOT), "session_id": session}, NOW) is None
    assert files == before


@pytest.mark.parametrize("header", ["overnight: off\nsession: session\n", "overnight: on\n"])
def test_off_or_unscoped_header_does_nothing(
    overnight: ModuleType, files: dict[Path, str], header: str
) -> None:
    """Opt-in requires both on and this session's id."""
    files[BACKLOG] = header + "- [ ] item\n"
    before = files.copy()
    assert overnight.decide({"cwd": str(ROOT), "session_id": "session"}, NOW) is None
    assert files == before


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize(
    ("backlog", "counter", "reason"),
    [
        (HEADER, "0", "backlog empty"),
        (HEADER + "until: 2026-10-22T12:00:00\n- [ ] item", "0", "until passed"),
        (HEADER + "until: unreadable\n- [ ] item", "0", "until passed"),
        (HEADER + "- [ ] item", "broken", "cap reached"),
        (HEADER + "- [ ] item", "60", "cap reached"),
        (HEADER + "- [ ] item", "61", "cap reached"),
    ],
)
def test_every_ending_requests_append_and_marks_once(
    overnight: ModuleType,
    files: dict[Path, str],
    backlog: str,
    counter: str,
    reason: str,
    active: bool,
) -> None:
    """End paths request append, never overwrite, and allow only one ending block."""
    files[BACKLOG] = backlog
    files[SCRATCH / "overnight.count"] = counter
    payload = {"cwd": str(ROOT), "session_id": "session", "stop_hook_active": active}
    decision = json.loads(overnight.decide(payload, NOW))
    assert decision["decision"] == "block"
    assert reason in decision["reason"]
    assert decision["reason"].startswith("[overnight-ending]")
    assert "append tasks/morning-2026-10-22.md" in decision["reason"]
    assert "overnight: off" in decision["reason"]
    assert files[SCRATCH / "overnight.ended"] == NOW.isoformat()
    assert overnight.decide(payload, NOW) is None


def test_ending_marker_precedes_all_header_and_counter_reads(
    overnight: ModuleType, files: dict[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed session remains terminal even with a corrupt or inaccessible backlog."""
    files[SCRATCH / "overnight.ended"] = "ended"
    read = Mock(side_effect=AssertionError("terminal session must not read files"))
    monkeypatch.setattr(Path, "read_text", read)
    assert overnight.decide({"cwd": str(ROOT), "session_id": "session"}, NOW) is None
    read.assert_not_called()


@pytest.mark.parametrize("until", [None, "2026-10-22T12:00:01"])
def test_missing_or_future_deadline_continues(overnight: ModuleType, until: str | None) -> None:
    """No deadline and a future deadline must not prematurely end the night."""
    assert overnight.until_passed(until, NOW) is False


def test_unreadable_counter_still_requests_morning_report(
    overnight: ModuleType, files: dict[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An inaccessible counter must end unattended work with the report instruction."""
    files[SCRATCH / "overnight.count"] = "0"
    original = Path.read_text

    def read(path: Path, encoding: str = "utf-8") -> str:
        if path == SCRATCH / "overnight.count":
            raise PermissionError("counter unreadable")
        return original(path, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", read)
    decision = json.loads(overnight.decide({"cwd": str(ROOT), "session_id": "session"}, NOW))
    assert decision["decision"] == "block"
    assert decision["reason"].startswith("[overnight-ending]")
    assert "append tasks/morning-2026-10-22.md" in decision["reason"]
    assert "overnight: off" in decision["reason"]
    assert SCRATCH / "overnight.ended" in files


@pytest.mark.parametrize("target", [BACKLOG, SCRATCH / "overnight.count"])
@pytest.mark.parametrize(
    "error",
    [OSError("unreadable"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")],
)
def test_unreadable_state_ends_once_with_append_instruction(
    overnight: ModuleType,
    files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    error: Exception,
) -> None:
    """Unreadable ownership passes silently; unreadable owned state requests a report."""
    files[SCRATCH / "overnight.count"] = "0"
    original = Path.read_text

    def read(path: Path, encoding: str = "utf-8") -> str:
        if path == target:
            raise error
        return original(path, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", read)
    payload = {"cwd": str(ROOT), "session_id": "session"}
    before = files.copy()
    result = overnight.decide(payload, NOW)
    if target == BACKLOG:
        assert result is None
        assert files == before
        return
    decision = json.loads(result)
    assert decision["decision"] == "block"
    assert "append tasks/morning-2026-10-22.md" in decision["reason"]
    assert "overnight: off" in decision["reason"]
    assert files[SCRATCH / "overnight.ended"] == NOW.isoformat()
    assert overnight.decide(payload, NOW) is None


@pytest.mark.parametrize("header", [None, "other", "off", "os-error", "decode-error"])
def test_unowned_night_never_reads_unreadable_counter(
    overnight: ModuleType,
    files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    header: str | None,
) -> None:
    """Absent, foreign, off, or unreadable ownership cannot block or mutate state."""
    if header is None:
        del files[BACKLOG]
    elif header == "other":
        files[BACKLOG] = "overnight: on\nsession: other\n- [ ] item"
    elif header == "off":
        files[BACKLOG] = "overnight: off\nsession: session\n- [ ] item"
    files[SCRATCH / "overnight.count"] = "0"
    before = files.copy()
    original = Path.read_text
    counter = Mock(side_effect=PermissionError("counter unreadable"))

    def read(path: Path, encoding: str = "utf-8") -> str:
        if path == SCRATCH / "overnight.count":
            return str(counter())
        if path == BACKLOG and header == "os-error":
            raise PermissionError("header unreadable")
        if path == BACKLOG and header == "decode-error":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
        return original(path, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", read)
    assert overnight.decide({"cwd": str(ROOT), "session_id": "session"}, NOW) is None
    counter.assert_not_called()
    assert files == before


@pytest.mark.parametrize("failure", ["write", "mkdir"])
@pytest.mark.parametrize("active", [False, True])
def test_unwritable_ending_marker_keeps_requesting_report(
    overnight: ModuleType,
    files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    active: bool,
) -> None:
    """A writable backlog can end the night despite marker failure or hook recursion."""
    files[BACKLOG] = HEADER
    access = Mock(side_effect=lambda path, mode: path in (BACKLOG, BACKLOG.parent))
    monkeypatch.setattr(overnight.os, "access", access)
    original = Path.write_text

    def write(path: Path, text: str, encoding: str = "utf-8") -> int:
        if path == SCRATCH / "overnight.ended":
            raise PermissionError("marker unwritable")
        return original(path, text, encoding=encoding)

    if failure == "write":
        monkeypatch.setattr(Path, "write_text", write)
    else:
        monkeypatch.setattr(Path, "mkdir", Mock(side_effect=PermissionError("read only")))
    payload = {"cwd": str(ROOT), "session_id": "session", "stop_hook_active": active}
    result = overnight.decide(payload, NOW)
    decision = json.loads(result)
    assert decision["decision"] == "block"
    assert "append tasks/morning-2026-10-22.md" in decision["reason"]
    assert "overnight: off" in decision["reason"]
    assert SCRATCH / "overnight.ended" not in files
    access.assert_any_call(BACKLOG, overnight.os.W_OK)
    access.assert_any_call(BACKLOG.parent, overnight.os.W_OK)
    payload["stop_hook_active"] = True
    assert overnight.decide(payload, NOW) == result
    files[BACKLOG] = "overnight: off\nsession: session\n"
    assert overnight.decide(payload, NOW) is None


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("writable", [(), (BACKLOG,), (BACKLOG.parent,)])
def test_marker_failure_requires_writable_backlog_and_directory(
    overnight: ModuleType,
    files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    active: bool,
    writable: tuple[Path, ...],
) -> None:
    """Without both header and report write permissions, an ending must pass silently."""
    files[BACKLOG] = HEADER
    original = Path.write_text

    def write(path: Path, text: str, encoding: str = "utf-8") -> int:
        if path == SCRATCH / "overnight.ended":
            raise PermissionError("marker unwritable")
        return original(path, text, encoding=encoding)

    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(overnight.os, "access", lambda path, mode: path in writable)
    payload = {"cwd": str(ROOT), "session_id": "session", "stop_hook_active": active}
    assert overnight.decide(payload, NOW) is None
    assert SCRATCH / "overnight.ended" not in files
    assert files[BACKLOG] == HEADER
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "backlog not writable either; passing silently" in captured.err


@pytest.mark.parametrize("active", [False, True])
def test_repeated_stops_in_unwritable_project_never_block(
    overnight: ModuleType,
    files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    active: bool,
) -> None:
    """An unwritable project cannot trap the owner in a loop of report requests."""
    before = files.copy()
    monkeypatch.setattr(Path, "mkdir", Mock(side_effect=PermissionError("read only")))
    monkeypatch.setattr(Path, "write_text", Mock(side_effect=PermissionError("read only")))
    payload = {"cwd": str(ROOT), "session_id": "session", "stop_hook_active": active}
    for _ in range(61):
        assert overnight.decide(payload, NOW) is None
    assert files == before


@pytest.mark.parametrize("prior", [0, 1, 2, 3])
@pytest.mark.parametrize("active", [False, True])
def test_transcript_bounds_endings_when_disk_is_full(
    overnight: ModuleType,
    files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    prior: int,
    active: bool,
) -> None:
    """Permission bits cannot cause unlimited ending blocks when writes fail."""
    transcript = Path("/session.jsonl")
    opener = Mock(side_effect=lambda mode: io.BytesIO(b"[overnight-ending]\n" * prior))
    monkeypatch.setattr(Path, "open", opener)
    monkeypatch.setattr(overnight.os, "access", Mock(return_value=True))
    write = Mock(side_effect=OSError("disk full"))
    mkdir = Mock(side_effect=OSError("disk full"))
    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    before = files.copy()
    result = overnight.decide(
        {"session_id": "session", "transcript_path": str(transcript), "stop_hook_active": active},
        NOW,
    )
    opener.assert_called_once_with("rb")
    assert files == before
    if prior < 2:
        decision = json.loads(result)
        assert decision["decision"] == "block"
        assert decision["reason"].startswith("[overnight-ending]")
        assert "append tasks/morning-2026-10-22.md" in decision["reason"]
        assert "overnight: off" in decision["reason"]
        assert mkdir.call_count == 2
    else:
        assert result is None
        assert mkdir.call_count == 1  # Counter attempt only; no ending-marker attempt.
        write.assert_not_called()
        assert "ending blocks already sent; passing silently" in capsys.readouterr().err


@pytest.mark.parametrize("transcript", [None, "", "/missing.jsonl", "/unreadable.jsonl"])
@pytest.mark.parametrize("writable", [False, True])
def test_unavailable_transcript_falls_back_to_backlog_writability(
    overnight: ModuleType,
    files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    transcript: str | None,
    writable: bool,
) -> None:
    """Missing payloads and read failures retain the marker/backlog fallback."""
    files[BACKLOG] = HEADER
    error = (
        PermissionError("unreadable") if transcript == "/unreadable.jsonl" else FileNotFoundError()
    )
    opener = Mock(side_effect=error)
    monkeypatch.setattr(Path, "open", opener)
    monkeypatch.setattr(Path, "write_text", Mock(side_effect=OSError("disk full")))
    monkeypatch.setattr(overnight.os, "access", Mock(return_value=writable))
    result = overnight.decide({"session_id": "session", "transcript_path": transcript}, NOW)
    if writable:
        decision = json.loads(result)
        assert decision["decision"] == "block"
        assert decision["reason"].startswith("[overnight-ending]")
        assert "append tasks/morning-2026-10-22.md" in decision["reason"]
    else:
        assert result is None
    assert opener.call_count == int(bool(transcript))


def test_transcript_reads_only_last_eight_mib(
    overnight: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old sentinels outside the bounded tail neither count nor get read."""
    tail_size = 8 * 1024 * 1024
    sentinel = b"[overnight-ending]"
    tail = b"\xff" + b"x" * (tail_size - len(sentinel) - 1) + sentinel
    with io.BytesIO(sentinel * 2 + tail) as stream:
        handle = Mock(wraps=stream)
        context = Mock()
        context.__enter__ = Mock(return_value=handle)
        context.__exit__ = Mock(return_value=False)
        opener = Mock(return_value=context)
        monkeypatch.setattr(Path, "open", opener)
        assert overnight._ending_blocks_sent(Path("/large.jsonl")) == 1
        opener.assert_called_once_with("rb")
        assert handle.seek.call_args_list[0].args == (0, 2)
        assert handle.seek.call_args_list[1].args == (len(sentinel) * 2,)
        handle.read.assert_called_once_with()
        assert stream.tell() == len(sentinel) * 2 + tail_size
