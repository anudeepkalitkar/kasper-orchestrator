"""Unit tier for ``.claude/scripts/notify.py``: the per-event sound map and how it plays.

Task 005's mapping table is the requirement, so the six platform x event paths are written
out here as literals — copied from the task doc, never read back from ``NATIVE_SOUNDS`` —
because a test that asks the module what it thinks the paths are cannot catch a wrong one.

The tier's no-I/O rule holds throughout: ``select_sound`` takes its existence predicate as
an argument, ``play``'s two ``.is_file()`` calls are answered by a patched
:meth:`pathlib.Path.is_file` over a set of names, the player is a recording stub in place
of ``subprocess``/``shutil``, Windows' ``winsound`` is a stub carrying the real flag values,
and ``main`` reads an in-memory ``StringIO``. Nothing here spawns a process, stats a file,
or makes a sound. The one disk touch is loading the script by path, which is what an
``import`` would be if ``.claude/`` were an importable package.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import NamedTuple

import pytest

from tests.helpers.hooks import load_script

#: The mapping table of task 005, verbatim: platform, ``hook_event_name``, the file played.
MAPPING: list[tuple[str, str, str]] = [
    ("darwin", "Notification", "/System/Library/Sounds/Glass.aiff"),
    ("darwin", "Stop", "/System/Library/Sounds/Funk.aiff"),
    ("win32", "Notification", r"C:\Windows\Media\Windows Notify System Generic.wav"),
    ("win32", "Stop", r"C:\Windows\Media\chord.wav"),
    ("linux", "Notification", "/usr/share/sounds/freedesktop/stereo/message.oga"),
    ("linux", "Stop", "/usr/share/sounds/freedesktop/stereo/complete.oga"),
]

#: The Notification (``waiting on you``) pick per platform — where anything unrecognised goes.
NOTIFICATION_SOUNDS: list[tuple[str, str]] = [
    (platform, path) for platform, event, path in MAPPING if event == "Notification"
]

#: A platform with no native pick at all: the bundled WAV is the whole answer there.
UNMAPPED_PLATFORM = "freebsd14"

#: ``winsound``'s real constants (Windows ``mmsystem.h``): play a *path*, and do not block.
SND_FILENAME = 0x00020000
SND_ASYNC = 0x0001

#: ``subprocess.DEVNULL``'s value, so the stub answers a ``stdout=``/``stderr=`` check.
DEVNULL = -3


class FakeHost(NamedTuple):
    """What a faked POSIX host recorded.

    Attributes:
        asked: Every player name ``shutil.which`` was asked about, in order.
        calls: Every ``subprocess.run`` invocation, as ``(argv, keyword arguments)``.
    """

    asked: list[str]
    calls: list[tuple[list[str], dict[str, object]]]


@pytest.fixture
def notify(repo_root: Path) -> ModuleType:
    """The hook script under test, loaded from its path in this checkout."""
    return load_script(repo_root, "notify")


def present(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> None:
    """Make exactly *paths* look present on this host, without touching the disk.

    Args:
        monkeypatch: The active patcher; the real ``Path.is_file`` returns afterwards.
        paths: The files ``play`` should find. Everything else is missing.
    """
    findable = set(paths)
    monkeypatch.setattr(Path, "is_file", lambda self: self in findable)


def posix_host(
    monkeypatch: pytest.MonkeyPatch,
    notify: ModuleType,
    platform: str,
    players: tuple[str, ...] = ("paplay", "aplay", "afplay"),
    returncode: int = 0,
) -> FakeHost:
    """Give the module a POSIX host whose players are recorders, not real processes.

    Args:
        monkeypatch: The active patcher.
        notify: The loaded script.
        platform: The ``sys.platform`` the module should see.
        players: The player names that count as installed on this host's PATH.
        returncode: The exit status every faked player run reports.

    Returns:
        The recorder: which players were looked up, and which were run with what.
    """
    host = FakeHost(asked=[], calls=[])

    def which(name: str) -> str | None:
        host.asked.append(name)
        return f"/usr/bin/{name}" if name in players else None

    def run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        host.calls.append((argv, kwargs))
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(notify, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(notify, "shutil", SimpleNamespace(which=which))
    monkeypatch.setattr(notify, "subprocess", SimpleNamespace(run=run, DEVNULL=DEVNULL))
    return host


def windows_host(
    monkeypatch: pytest.MonkeyPatch, notify: ModuleType, failing: tuple[Path, ...] = ()
) -> list[tuple[str, int]]:
    """Give the module a Windows host: a stub ``winsound`` and a subprocess that may not run.

    Args:
        monkeypatch: The active patcher.
        notify: The loaded script.
        failing: The files whose ``PlaySound`` raises ``RuntimeError`` — how the real
            ``winsound`` reports "could not play that", async included. The attempt is
            recorded before the raise, so a retry can be counted.

    Returns:
        The list every ``PlaySound`` attempt appends ``(path string, flags)`` to.
    """
    played: list[tuple[str, int]] = []
    broken = {str(path) for path in failing}

    def play_sound(name: str, flags: int) -> None:
        played.append((name, flags))
        if name in broken:
            raise RuntimeError("Failed to play sound")

    def forbidden_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        raise AssertionError(f"Windows must play through winsound, not {argv}")

    monkeypatch.setattr(notify, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        notify,
        "winsound",
        SimpleNamespace(PlaySound=play_sound, SND_FILENAME=SND_FILENAME, SND_ASYNC=SND_ASYNC),
        raising=False,
    )
    monkeypatch.setattr(notify, "subprocess", SimpleNamespace(run=forbidden_run, DEVNULL=DEVNULL))
    return played


def record_play(monkeypatch: pytest.MonkeyPatch, notify: ModuleType) -> list[Path]:
    """Replace ``play`` with a recorder, so ``main`` can be driven without a sound card.

    Args:
        monkeypatch: The active patcher.
        notify: The loaded script.

    Returns:
        The list of paths ``main`` asked to play, in order.
    """
    played: list[Path] = []

    def fake_play(path: Path) -> None:
        played.append(path)

    monkeypatch.setattr(notify, "play", fake_play)
    return played


def stdin_payload(
    monkeypatch: pytest.MonkeyPatch,
    notify: ModuleType,
    text: str,
    platform: str = "darwin",
    files_exist: bool = True,
) -> None:
    """Point the module at an in-memory payload on a host of the given shape.

    Args:
        monkeypatch: The active patcher.
        notify: The loaded script.
        text: The exact bytes ``main`` will read as stdin — malformed JSON included.
        platform: The ``sys.platform`` ``main`` passes to ``select_sound``.
        files_exist: What the existence predicate ``main`` supplies should answer.
    """
    monkeypatch.setattr(notify, "sys", SimpleNamespace(platform=platform, stdin=io.StringIO(text)))
    monkeypatch.setattr(notify, "Path", SimpleNamespace(is_file=lambda path: files_exist))


# --------------------------------------------------------------------------------------
# select_sound — the mapping table
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("platform", "event", "expected"), MAPPING)
def test_each_platform_and_event_plays_its_documented_native_sound(
    notify: ModuleType, platform: str, event: str, expected: str
) -> None:
    """Task 005's table, cell by cell, against the literal path the human chose."""
    assert str(notify.select_sound(platform, event, lambda path: True)) == expected


@pytest.mark.parametrize(("platform", "expected"), NOTIFICATION_SOUNDS)
@pytest.mark.parametrize("event", ["PreToolUse", "stop", "STOP", "", "Notification "])
def test_an_unrecognised_event_plays_the_notification_sound(
    notify: ModuleType, platform: str, event: str, expected: str
) -> None:
    """Anything that is not exactly ``Stop`` means "you are needed" — including near-misses."""
    assert str(notify.select_sound(platform, event, lambda path: True)) == expected


@pytest.mark.parametrize(("platform", "event", "native"), MAPPING)
def test_a_missing_system_file_falls_back_to_the_bundled_wav(
    notify: ModuleType, platform: str, event: str, native: str
) -> None:
    """A host that never shipped the file (or dropped it) still makes a noise."""
    assert notify.select_sound(platform, event, lambda path: False) == notify.SOUND_FILE


@pytest.mark.parametrize("event", ["Notification", "Stop", "PreToolUse"])
def test_an_unmapped_platform_plays_the_bundled_wav(notify: ModuleType, event: str) -> None:
    """No native pick exists for a BSD, so the bundled WAV answers every event there."""
    assert notify.select_sound(UNMAPPED_PLATFORM, event, lambda path: True) == notify.SOUND_FILE


def test_the_bundled_fallback_is_the_wav_shipped_beside_the_scripts(
    notify: ModuleType, repo_root: Path
) -> None:
    """``SOUND_FILE`` resolves from the script's own location: this checkout's copy."""
    assert notify.SOUND_FILE == repo_root / ".claude" / "sounds" / "notify.wav"


def test_only_the_native_candidate_is_checked_for_existence(notify: ModuleType) -> None:
    """The predicate is asked about the platform's own pick — not about the fallback."""
    asked: list[Path] = []

    def exists(path: Path) -> bool:
        asked.append(path)
        return True

    notify.select_sound("darwin", "Stop", exists)
    assert asked == [Path("/System/Library/Sounds/Funk.aiff")]


# --------------------------------------------------------------------------------------
# play — players, the single retry, and the Windows branch
# --------------------------------------------------------------------------------------


def test_a_native_sound_plays_through_the_first_player_found(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS has one player; the file is handed to it as a positional argument."""
    glass = Path("/System/Library/Sounds/Glass.aiff")
    present(monkeypatch, glass, notify.SOUND_FILE)
    host = posix_host(monkeypatch, notify, "darwin", players=("afplay",))

    notify.play(glass)

    assert [argv for argv, _ in host.calls] == [["/usr/bin/afplay", str(glass)]]


def test_linux_prefers_paplay_and_falls_back_to_aplay(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``paplay`` is asked for first; a host without it still plays through ``aplay``."""
    sound = Path("/usr/share/sounds/freedesktop/stereo/message.oga")
    present(monkeypatch, sound, notify.SOUND_FILE)
    host = posix_host(monkeypatch, notify, "linux", players=("aplay",))

    notify.play(sound)

    assert host.asked == ["paplay", "aplay"]
    assert [argv for argv, _ in host.calls] == [["/usr/bin/aplay", str(sound)]]


def test_the_players_output_is_discarded_and_its_exit_status_not_raised(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook must stay quiet on stdout/stderr and must not raise on a player's failure."""
    sound = Path("/System/Library/Sounds/Glass.aiff")
    present(monkeypatch, sound, notify.SOUND_FILE)
    host = posix_host(monkeypatch, notify, "darwin", players=("afplay",))

    notify.play(sound)

    _, kwargs = host.calls[0]
    assert kwargs == {"check": False, "stdout": DEVNULL, "stderr": DEVNULL}


def test_a_failed_native_player_retries_exactly_once_on_the_bundled_wav(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Vorbis-less ``paplay`` exits non-zero having played nothing: one retry, no more."""
    sound = Path("/usr/share/sounds/freedesktop/stereo/complete.oga")
    present(monkeypatch, sound, notify.SOUND_FILE)
    host = posix_host(monkeypatch, notify, "linux", players=("paplay",), returncode=1)

    notify.play(sound)

    assert [argv for argv, _ in host.calls] == [
        ["/usr/bin/paplay", str(sound)],
        ["/usr/bin/paplay", str(notify.SOUND_FILE)],
    ]


def test_a_successful_player_is_never_retried(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit status 0 means it was heard; a second sound would be a double chime."""
    sound = Path("/usr/share/sounds/freedesktop/stereo/message.oga")
    present(monkeypatch, sound, notify.SOUND_FILE)
    host = posix_host(monkeypatch, notify, "linux", players=("paplay",), returncode=0)

    notify.play(sound)

    assert len(host.calls) == 1


def test_a_failed_bundled_wav_is_not_retried_on_itself(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback has no fallback: playing it and failing ends in silence, not a loop."""
    present(monkeypatch, notify.SOUND_FILE)
    host = posix_host(monkeypatch, notify, "linux", players=("paplay",), returncode=1)

    notify.play(notify.SOUND_FILE)

    assert [argv for argv, _ in host.calls] == [["/usr/bin/paplay", str(notify.SOUND_FILE)]]


def test_a_failed_player_is_not_retried_when_the_bundled_wav_is_missing(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No bundled copy beside the script (a partial install) means no retry at all."""
    sound = Path("/usr/share/sounds/freedesktop/stereo/complete.oga")
    present(monkeypatch, sound)
    host = posix_host(monkeypatch, notify, "linux", players=("paplay",), returncode=1)

    notify.play(sound)

    assert [argv for argv, _ in host.calls] == [["/usr/bin/paplay", str(sound)]]


def test_a_host_with_no_player_stays_silent(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A headless box has neither player: nothing runs, and nothing raises."""
    sound = Path("/usr/share/sounds/freedesktop/stereo/message.oga")
    present(monkeypatch, sound, notify.SOUND_FILE)
    host = posix_host(monkeypatch, notify, "linux", players=())

    notify.play(sound)

    assert host.asked == ["paplay", "aplay"]
    assert host.calls == []


def test_an_unmapped_posix_platform_looks_for_no_player_at_all(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A BSD has no player list, so ``play`` is a documented silent no-op there."""
    present(monkeypatch, notify.SOUND_FILE)
    host = posix_host(monkeypatch, notify, UNMAPPED_PLATFORM)

    notify.play(notify.SOUND_FILE)

    assert host.asked == []
    assert host.calls == []


def test_a_missing_sound_file_is_a_silent_no_op(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``play`` checks the file before hunting a player: no file, no lookup, no run."""
    present(monkeypatch)
    host = posix_host(monkeypatch, notify, "darwin", players=("afplay",))

    notify.play(Path("/System/Library/Sounds/Glass.aiff"))

    assert host.asked == []
    assert host.calls == []


def test_windows_plays_the_file_by_path_and_does_not_block(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SND_FILENAME | SND_ASYNC``: the argument is a path, and the hook does not wait."""
    chord = Path(r"C:\Windows\Media\chord.wav")
    present(monkeypatch, chord)
    played = windows_host(monkeypatch, notify)

    notify.play(chord)

    assert played == [(str(chord), SND_FILENAME | SND_ASYNC)]


def test_windows_stays_silent_when_the_file_is_missing(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existence check guards the Windows branch too — ``PlaySound`` is never reached."""
    present(monkeypatch)
    played = windows_host(monkeypatch, notify)

    notify.play(Path(r"C:\Windows\Media\chord.wav"))

    assert played == []


def test_a_windows_file_that_will_not_play_retries_exactly_once_on_the_bundled_wav(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``PlaySound`` raising is Windows' non-zero exit: one retry, same flags, no more."""
    chord = Path(r"C:\Windows\Media\chord.wav")
    present(monkeypatch, chord, notify.SOUND_FILE)
    played = windows_host(monkeypatch, notify, failing=(chord,))

    notify.play(chord)

    assert played == [
        (str(chord), SND_FILENAME | SND_ASYNC),
        (str(notify.SOUND_FILE), SND_FILENAME | SND_ASYNC),
    ]


def test_a_windows_retry_that_also_fails_raises_nothing(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead audio stack ends in silence: ``play`` returns, and nothing escapes it."""
    chord = Path(r"C:\Windows\Media\chord.wav")
    present(monkeypatch, chord, notify.SOUND_FILE)
    played = windows_host(monkeypatch, notify, failing=(chord, notify.SOUND_FILE))

    notify.play(chord)  # must not raise

    assert len(played) == 2


def test_a_failed_bundled_wav_is_not_retried_on_itself_on_windows(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback has no fallback on Windows either — one attempt, then silence."""
    present(monkeypatch, notify.SOUND_FILE)
    played = windows_host(monkeypatch, notify, failing=(notify.SOUND_FILE,))

    notify.play(notify.SOUND_FILE)

    assert played == [(str(notify.SOUND_FILE), SND_FILENAME | SND_ASYNC)]


def test_windows_does_not_retry_when_the_bundled_wav_is_missing(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No bundled copy beside the script (a partial install) means no retry at all."""
    generic = Path(r"C:\Windows\Media\Windows Notify System Generic.wav")
    present(monkeypatch, generic)
    played = windows_host(monkeypatch, notify, failing=(generic,))

    notify.play(generic)

    assert played == [(str(generic), SND_FILENAME | SND_ASYNC)]


# --------------------------------------------------------------------------------------
# main — the payload, the routing, and the never-fail contract
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload", ["", "   ", "not json at all", "{", '{"hook_event_name": ', "\x00\x01"]
)
def test_a_payload_that_is_not_json_exits_zero_and_plays_nothing(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    """Anything that is not a hook invocation stays silent instead of breaking the hook."""
    stdin_payload(monkeypatch, notify, payload)
    played = record_play(monkeypatch, notify)

    assert notify.main() == 0
    assert played == []


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ("Notification", "/System/Library/Sounds/Glass.aiff"),
        ("Stop", "/System/Library/Sounds/Funk.aiff"),
    ],
)
def test_a_valid_payload_is_routed_by_its_hook_event_name(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch, event: str, expected: str
) -> None:
    """The two wired events are told apart by ear, so they must not resolve alike."""
    stdin_payload(monkeypatch, notify, f'{{"hook_event_name": "{event}", "message": "hi"}}')
    played = record_play(monkeypatch, notify)

    assert notify.main() == 0
    assert [str(path) for path in played] == [expected]


@pytest.mark.parametrize(
    ("platform", "event", "expected"),
    [
        ("linux", "Stop", "/usr/share/sounds/freedesktop/stereo/complete.oga"),
        ("win32", "Notification", r"C:\Windows\Media\Windows Notify System Generic.wav"),
    ],
)
def test_main_asks_for_the_host_platforms_sound_not_this_developers(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch, platform: str, event: str, expected: str
) -> None:
    """``main`` passes the *running* ``sys.platform`` through — a hardcoded one would pass
    every macOS test and still play the wrong file on Linux and Windows."""
    stdin_payload(monkeypatch, notify, f'{{"hook_event_name": "{event}"}}', platform=platform)
    played = record_play(monkeypatch, notify)

    assert notify.main() == 0
    assert [str(path) for path in played] == [expected]


@pytest.mark.parametrize(
    "payload",
    ['{"hook_event_name": "PreToolUse"}', "{}", '{"hook_event_name": 42}', "[1, 2]", "null"],
)
def test_a_payload_without_a_usable_event_plays_the_notification_sound(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    """Well-formed JSON that names no event we know still signals "you are needed"."""
    stdin_payload(monkeypatch, notify, payload)
    played = record_play(monkeypatch, notify)

    assert notify.main() == 0
    assert [str(path) for path in played] == ["/System/Library/Sounds/Glass.aiff"]


def test_main_falls_back_to_the_bundled_wav_when_the_native_file_is_absent(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``main`` supplies a real existence check: a stripped host gets the bundled WAV."""
    stdin_payload(monkeypatch, notify, '{"hook_event_name": "Stop"}', files_exist=False)
    played = record_play(monkeypatch, notify)

    assert notify.main() == 0
    assert played == [notify.SOUND_FILE]


def test_a_stdin_that_cannot_be_read_exits_zero_and_plays_nothing(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken pipe is not a payload: ``OSError`` off ``read`` stays silent, still 0."""

    def unreadable(size: int = -1) -> str:
        raise OSError("stdin is closed")

    monkeypatch.setattr(
        notify, "sys", SimpleNamespace(platform="darwin", stdin=SimpleNamespace(read=unreadable))
    )
    played = record_play(monkeypatch, notify)

    assert notify.main() == 0
    assert played == []


def test_main_returns_zero_even_when_playing_raises(
    notify: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook must never break a session — an exploding audio stack still exits 0."""

    def exploding_play(path: Path) -> None:
        raise OSError("no audio device")

    stdin_payload(monkeypatch, notify, '{"hook_event_name": "Notification"}')
    monkeypatch.setattr(notify, "play", exploding_play)

    assert notify.main() == 0
