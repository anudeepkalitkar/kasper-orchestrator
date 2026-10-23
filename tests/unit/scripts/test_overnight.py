"""Overnight state transitions with an in-memory filesystem and explicit clock."""

import datetime
import errno
import io
import json
import os
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from tests.helpers.hooks import load_script

NOW = datetime.datetime(2026, 10, 22, 12)
ROOT = Path("/project")
BACKLOG = ROOT / "tasks/BACKLOG.md"
SCRATCH = ROOT / "claude-temp/sessions/session"
FALLBACK = ROOT / "claude-temp/overnight-session.ended"
HEADER = "overnight: on\nsession: session\n"


@pytest.fixture
def overnight(repo_root: Path) -> ModuleType:
    """Load the hook without running its entry point."""
    return load_script(repo_root, "overnight")


@pytest.fixture
def lookup_errors() -> dict[tuple[str, Path], OSError]:
    """Inject read and stat failures independently for individual in-memory paths."""
    return {}


@pytest.fixture
def files(
    monkeypatch: pytest.MonkeyPatch,
    overnight: ModuleType,
    lookup_errors: dict[tuple[str, Path], OSError],
) -> dict[Path, str]:
    """Keep all hook reads, writes, and directory creation in memory."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(overnight, "project_root", lambda cwd: ROOT)
    monkeypatch.setattr(overnight.os, "access", Mock(return_value=False))
    contents = {BACKLOG: HEADER + "- [ ] first\n"}

    def read(path: Path, encoding: str = "utf-8") -> str:
        if ("read", path) in lookup_errors:
            raise lookup_errors["read", path]
        if path not in contents:
            raise FileNotFoundError(str(path))
        return contents[path]

    def stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if ("stat", path) in lookup_errors:
            raise lookup_errors["stat", path]
        if path not in contents:
            raise FileNotFoundError(str(path))
        return os.stat_result((0o100644, 0, 0, 1, 0, 0, len(contents[path]), 0, 0, 0))

    def write(path: Path, text: str, encoding: str = "utf-8") -> int:
        contents[path] = text
        return len(text)

    monkeypatch.setattr(Path, "exists", lambda path: path in contents)
    monkeypatch.setattr(Path, "is_file", lambda path: path in contents)
    monkeypatch.setattr(Path, "stat", stat)
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


@pytest.mark.parametrize("marker", [SCRATCH / "overnight.ended", FALLBACK])
def test_ending_marker_precedes_all_header_and_counter_reads(
    overnight: ModuleType, files: dict[Path, str], monkeypatch: pytest.MonkeyPatch, marker: Path
) -> None:
    """Either marker alone silences a session before ownership or counter reads."""
    files[marker] = "ended"
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
def test_unwritable_ending_marker_persists_fallback_and_requests_report_once(
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
    assert files[FALLBACK] == NOW.isoformat()
    assert overnight.decide(payload, NOW) is None
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
    assert result is None
    captured = capsys.readouterr()
    assert captured.out == ""
    if prior < 2:
        assert mkdir.call_count == 2
        assert "overnight: cannot write marker (disk full); passing silently.\n" in captured.err
    else:
        assert mkdir.call_count == 1  # Counter attempt only; no ending-marker attempt.
        write.assert_not_called()
        assert "ending blocks already sent; passing silently" in captured.err


@pytest.mark.parametrize("transcript", [None, "", "/missing.jsonl", "/unreadable.jsonl"])
@pytest.mark.parametrize("writable", [False, True])
@pytest.mark.parametrize("marker_error", [OSError("disk full"), PermissionError("denied")])
def test_unavailable_transcript_falls_back_to_backlog_writability(
    overnight: ModuleType,
    files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    transcript: str | None,
    writable: bool,
    marker_error: OSError,
) -> None:
    """Unavailable transcripts cannot authorize blocks when both marker writes fail."""
    files[BACKLOG] = HEADER
    error = (
        PermissionError("unreadable") if transcript == "/unreadable.jsonl" else FileNotFoundError()
    )
    opener = Mock(side_effect=error)
    monkeypatch.setattr(Path, "open", opener)
    monkeypatch.setattr(Path, "write_text", Mock(side_effect=marker_error))
    monkeypatch.setattr(overnight.os, "access", Mock(return_value=writable))
    result = overnight.decide({"session_id": "session", "transcript_path": transcript}, NOW)
    assert result is None
    assert SCRATCH / "overnight.ended" not in files
    assert FALLBACK not in files
    assert opener.call_count == int(bool(transcript))


def test_transcript_counts_sentinel_before_more_than_eight_mib(
    overnight: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old sentinels and undecodable filler cannot hide an earlier ending."""
    sentinel = b"[overnight-ending]"
    data = sentinel + b"\xff" * (9 * 1024 * 1024)
    monkeypatch.setattr(Path, "open", Mock(side_effect=lambda mode: io.BytesIO(data)))
    assert overnight._ending_blocks_sent(Path("/large.jsonl")) == 1


@pytest.mark.parametrize("prior", [0, 1])
@pytest.mark.parametrize("error_number", [errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.EIO, None])
@pytest.mark.parametrize("writable", [False, True])
@pytest.mark.parametrize("failure", ["write_text", "mkdir"])
def test_non_permission_marker_failure_is_silent(
    overnight: ModuleType,
    files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    prior: int,
    error_number: int | None,
    writable: bool,
    failure: str,
) -> None:
    """Storage errors must silence endings regardless of permission bits or prior count."""
    error = OSError(error_number, "storage failed") if error_number else OSError("storage failed")
    failing = Mock(side_effect=error)
    writes = Mock(side_effect=AssertionError("non-permission failure must not try fallback"))
    monkeypatch.setattr(Path, "write_text", writes)
    monkeypatch.setattr(Path, failure, failing)
    monkeypatch.setattr(overnight.os, "access", Mock(return_value=writable))
    monkeypatch.setattr(
        Path, "open", Mock(side_effect=lambda mode: io.BytesIO(b"[overnight-ending]" * prior))
    )
    before = files.copy()
    assert overnight._end_night(
        SCRATCH / "overnight.ended", BACKLOG, "backlog empty", NOW, Path("/session.jsonl")
    ) is None
    failing.assert_called_once()
    if failure == "mkdir":
        writes.assert_not_called()
    assert files == before
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"overnight: cannot write marker ({error}); passing silently.\n"


@pytest.mark.parametrize("offset", range(-17, 2))
def test_transcript_counts_boundary_sentinel_exactly_once(
    overnight: ModuleType, monkeypatch: pytest.MonkeyPatch, offset: int
) -> None:
    """Every split position and adjacent whole sentinel must count once, including at EOF."""
    data = b"x" * (1024 * 1024 + offset) + b"[overnight-ending]"
    monkeypatch.setattr(Path, "open", Mock(side_effect=lambda mode: io.BytesIO(data)))
    assert overnight._ending_blocks_sent(Path("/boundary.jsonl")) == 1


@pytest.mark.parametrize("prefix_chunks", [0, 2])
def test_transcript_stops_reading_at_ending_ceiling(
    overnight: ModuleType, monkeypatch: pytest.MonkeyPatch, prefix_chunks: int
) -> None:
    """After the ceiling, even an unreadable remainder must not be touched."""
    chunk_size = 1024 * 1024
    handle = Mock()
    handle.read.side_effect = [b"x" * chunk_size] * prefix_chunks + [
        b"[overnight-ending]" * overnight.MAX_ENDING_BLOCKS,
        AssertionError("read beyond ending ceiling"),
    ]
    context = Mock()
    context.__enter__ = Mock(return_value=handle)
    context.__exit__ = Mock(return_value=False)
    opener = Mock(return_value=context)
    monkeypatch.setattr(Path, "open", opener)
    assert overnight._ending_blocks_sent(Path("/session.jsonl")) == overnight.MAX_ENDING_BLOCKS
    opener.assert_called_once_with("rb")
    assert handle.read.call_count == prefix_chunks + 1
    assert all(call.args == (chunk_size,) for call in handle.read.call_args_list)
    handle.seek.assert_not_called()


def test_two_distant_endings_silence_without_marker_attempt(
    overnight: ModuleType, files: dict[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large transcript cannot age out the first ending and allow a third."""
    files[BACKLOG] = HEADER
    data = b"[overnight-ending]" + b"x" * (9 * 1024 * 1024) + b"[overnight-ending]"
    monkeypatch.setattr(Path, "open", Mock(side_effect=lambda mode: io.BytesIO(data)))
    write = Mock(side_effect=AssertionError("ceiling must precede marker write"))
    monkeypatch.setattr(Path, "write_text", write)
    assert overnight._end_night(
        SCRATCH / "overnight.ended", BACKLOG, "backlog empty", NOW, Path("/large.jsonl")
    ) is None
    write.assert_not_called()


@pytest.mark.parametrize("permission_denied", [False, True])
@pytest.mark.parametrize("writable", [False, True])
@pytest.mark.parametrize("fallback_errno", [None, errno.ENOSPC, errno.EACCES])
@pytest.mark.parametrize("persist_transcript", [False, True])
@pytest.mark.parametrize("marker_stat_errno", [None, errno.EIO, errno.EACCES])
@pytest.mark.parametrize("counter_read_errno", [None, errno.EIO])
def test_failed_writes_bound_repeated_stops(
    overnight: ModuleType,
    files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    permission_denied: bool,
    writable: bool,
    fallback_errno: int | None,
    persist_transcript: bool,
    lookup_errors: dict[tuple[str, Path], OSError],
    marker_stat_errno: int | None,
    counter_read_errno: int | None,
) -> None:
    """Lookup, write, permission, and transcript failures independently bound all Stops."""
    if marker_stat_errno is not None:
        lookup_errors["stat", SCRATCH / "overnight.ended"] = OSError(marker_stat_errno, "stat")
    if counter_read_errno is not None:
        lookup_errors["read", SCRATCH / "overnight.count"] = OSError(counter_read_errno, "read")
    error = PermissionError("denied") if permission_denied else OSError(errno.ENOSPC, "full")
    primary_write = Mock(side_effect=error)
    fallback_error = OSError(fallback_errno, "fallback failed") if fallback_errno else None
    fallback_write = Mock(side_effect=fallback_error)
    original = Path.write_text

    def write(path: Path, text: str, encoding: str = "utf-8") -> int:
        if path == FALLBACK:
            fallback_write()
            return original(path, text, encoding=encoding)
        primary_write()
        raise AssertionError("primary write must fail")

    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(overnight.os, "access", Mock(return_value=writable))
    transcript = bytearray()
    monkeypatch.setattr(Path, "open", Mock(side_effect=lambda mode: io.BytesIO(transcript)))
    before = files.copy()
    blocks = 0
    fallback_allowed = permission_denied and writable and marker_stat_errno is None
    fallback_persists = fallback_allowed and fallback_errno is None
    stops = overnight.MAX_CONTINUATIONS + 1
    for stop in range(stops):
        result = overnight.decide(
            {"session_id": "session", "transcript_path": "/session.jsonl"}, NOW
        )
        if fallback_persists and stop == 0:
            assert result is not None
        else:
            assert result is None
        if result is not None:
            decision = json.loads(result)
            assert decision["decision"] == "block"
            assert decision["reason"].startswith("[overnight-ending]")
            assert "append tasks/morning-2026-10-22.md" in decision["reason"]
            assert "overnight: off" in decision["reason"]
            blocks += 1
            if persist_transcript:
                transcript.extend(result.encode() + b"\n")
        assert blocks <= overnight.MAX_ENDING_BLOCKS
    assert blocks == int(fallback_persists)
    attempts_per_stop = 2 if counter_read_errno is None else 1
    attempted_stops = 0 if marker_stat_errno is not None else 1 if fallback_persists else stops
    assert primary_write.call_count == attempts_per_stop * attempted_stops
    expected_attempts = 1 if fallback_persists else stops if fallback_allowed else 0
    assert fallback_write.call_count == expected_attempts
    if fallback_persists:
        before[FALLBACK] = NOW.isoformat()
    assert files == before
    captured = capsys.readouterr()
    assert captured.out == ""
    if fallback_allowed and fallback_error is not None:
        diagnostic = (
            f"overnight: cannot write fallback marker ({fallback_error}); passing silently."
        )
        assert captured.err.count(diagnostic) == stops


@pytest.mark.parametrize("data", [b"", b"ordinary transcript", b"[overnight-endin"])
def test_transcript_without_complete_sentinel_counts_zero(
    overnight: ModuleType, monkeypatch: pytest.MonkeyPatch, data: bytes
) -> None:
    """Empty transcripts and partial sentinel prefixes are not ending blocks."""
    monkeypatch.setattr(Path, "open", Mock(side_effect=lambda mode: io.BytesIO(data)))
    assert overnight._ending_blocks_sent(Path("/session.jsonl")) == 0


def test_transcript_read_failure_after_one_ending_returns_unknown(
    overnight: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An interrupted scan cannot claim its partial count as the complete transcript count."""
    handle = Mock()
    handle.read.side_effect = [b"[overnight-ending]", OSError(errno.EIO, "read failed")]
    context = Mock()
    context.__enter__ = Mock(return_value=handle)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(Path, "open", Mock(return_value=context))
    assert overnight._ending_blocks_sent(Path("/session.jsonl")) is None
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "overnight: cannot read transcript" in captured.err
    assert "read failed" in captured.err


@pytest.mark.parametrize("marker", [SCRATCH / "overnight.ended", FALLBACK])
@pytest.mark.parametrize("error_number", [errno.EIO, errno.EACCES])
@pytest.mark.parametrize("persisted", [False, True])
def test_failed_marker_stat_never_rearms_session(
    overnight: ModuleType,
    files: dict[Path, str],
    lookup_errors: dict[tuple[str, Path], OSError],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    marker: Path,
    error_number: int,
    persisted: bool,
) -> None:
    """Unknown marker presence must silence every Stop before ownership or any write."""
    if persisted:
        files[marker] = "already ended"
    error = OSError(error_number, "lookup failed")
    lookup_errors["stat", marker] = error
    read = Mock(side_effect=AssertionError("failed lookup must precede ownership"))
    write = Mock(side_effect=AssertionError("failed lookup must never rearm a block"))
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "write_text", write)
    before = files.copy()
    for _ in range(100):
        assert overnight.decide({"session_id": "session"}, NOW) is None
    read.assert_not_called()
    write.assert_not_called()
    assert files == before
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"overnight: cannot check {marker} ({error}); passing silently.\n" * 100
    )


@pytest.mark.parametrize("marker_writable", [False, True])
@pytest.mark.parametrize("error_number", [errno.EIO, errno.EACCES])
def test_counter_read_failure_ends_once_only_after_marker_persists(
    overnight: ModuleType,
    files: dict[Path, str],
    lookup_errors: dict[tuple[str, Path], OSError],
    monkeypatch: pytest.MonkeyPatch,
    marker_writable: bool,
    error_number: int,
) -> None:
    """A failed counter read must never reset progress or emit a continue block."""
    counter = SCRATCH / "overnight.count"
    files[counter] = "17"
    lookup_errors["read", counter] = OSError(error_number, "counter unreadable")
    monkeypatch.setattr(overnight.os, "access", Mock(return_value=True))
    original = Path.write_text
    writes: list[Path] = []

    def write(path: Path, text: str, encoding: str = "utf-8") -> int:
        writes.append(path)
        if not marker_writable:
            raise PermissionError(errno.EACCES, "marker denied")
        return original(path, text, encoding=encoding)

    monkeypatch.setattr(Path, "write_text", write)
    for stop in range(100):
        result = overnight.decide({"session_id": "session"}, NOW)
        if marker_writable and stop == 0:
            decision = json.loads(result)
            assert decision["decision"] == "block"
            assert decision["reason"].startswith("[overnight-ending]")
            assert "state unreadable" in decision["reason"]
            assert "append tasks/morning-2026-10-22.md" in decision["reason"]
            assert "overnight: off" in decision["reason"]
            assert files[SCRATCH / "overnight.ended"] == NOW.isoformat()
        else:
            assert result is None
    assert files[counter] == "17"
    expected = [SCRATCH / "overnight.ended"]
    assert writes == (expected if marker_writable else (expected + [FALLBACK]) * 100)


def test_missing_counter_starts_at_zero_and_then_increments(
    overnight: ModuleType, files: dict[Path, str]
) -> None:
    """Only an absent counter starts a new continuation sequence at one."""
    counter = SCRATCH / "overnight.count"
    assert counter not in files
    for count in (1, 2):
        decision = json.loads(overnight.decide({"session_id": "session"}, NOW))
        assert decision["decision"] == "block"
        assert "first open" in decision["reason"]
        assert "[overnight-ending]" not in decision["reason"]
        assert files[counter] == str(count)
    assert SCRATCH / "overnight.ended" not in files
    assert FALLBACK not in files


@pytest.mark.parametrize("persisted_marker", [SCRATCH / "overnight.ended", FALLBACK, None])
def test_transcript_permission_denied_defers_to_marker_persistence(
    overnight: ModuleType,
    files: dict[Path, str],
    monkeypatch: pytest.MonkeyPatch,
    persisted_marker: Path | None,
) -> None:
    """An unreadable transcript permits exactly one ending if either marker can land."""
    files[BACKLOG] = HEADER
    opener = Mock(side_effect=PermissionError(errno.EACCES, "transcript denied"))
    monkeypatch.setattr(Path, "open", opener)
    monkeypatch.setattr(overnight.os, "access", Mock(return_value=True))
    original = Path.write_text

    def write(path: Path, text: str, encoding: str = "utf-8") -> int:
        if path in (SCRATCH / "overnight.ended", FALLBACK) and path != persisted_marker:
            raise PermissionError(errno.EACCES, "marker denied")
        return original(path, text, encoding=encoding)

    monkeypatch.setattr(Path, "write_text", write)
    for stop in range(100):
        result = overnight.decide(
            {"session_id": "session", "transcript_path": "/denied.jsonl"}, NOW
        )
        if persisted_marker is not None and stop == 0:
            decision = json.loads(result)
            assert decision["decision"] == "block"
            assert decision["reason"].startswith("[overnight-ending]")
            assert "backlog empty" in decision["reason"]
            assert files[persisted_marker] == NOW.isoformat()
        else:
            assert result is None
    assert opener.call_count == (1 if persisted_marker else 100)
