#!/usr/bin/env python3
"""Attention hook for Claude Code: play the host OS's own sound when Claude wants the user.

Reads a Claude Code hook event as JSON on stdin and plays a sound. Wired to two hook
events in ``.claude/settings.json``, and each one gets its *own* native sound so the two
are told apart by ear:

- ``Notification`` — Claude is waiting on the user (a permission prompt, or the prompt
  has gone idle). This is the "user action needed" signal: macOS *Glass*, Windows
  *Windows Notify System Generic*, Linux freedesktop *message*.
- ``Stop`` — Claude finished responding; it's the user's turn again: macOS *Funk*,
  Windows *chord*, Linux freedesktop *complete*.

The sounds are played from the paths the OS itself ships them at — nothing from Apple or
Microsoft is bundled here, because their sound files are not ours to redistribute. Where
the host has no such file (a stripped Linux image, a future macOS that drops a name), or
its player cannot decode it, the bundled ``sounds/notify.wav`` plays instead, so every
platform still makes a noise.

A sound needs no notification-centre permission, no registered application id, and no
per-OS banner machinery — which is what this hook used to be, and what made it the most
likely thing to fail silently on a fresh machine.

Playback is stdlib-first per platform: ``winsound`` on Windows (WAV only — both Windows
picks and the bundled fallback are WAV), ``afplay`` on macOS (part of the OS, and happy
with ``.aiff``), PulseAudio's ``paplay`` or ALSA's ``aplay`` on Linux.

The hook is best-effort: a garbled payload, a missing sound file, a host with no player,
or any failure at all exits 0, so it never blocks or breaks the session.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

if sys.platform == "win32":  # stdlib, but Windows-only — no import to make elsewhere
    import winsound

#: The two hook events this script is wired to. Anything else — including a payload with
#: no event at all — is treated as ``Notification``, the "you are needed" signal.
NOTIFICATION_EVENT: Final[str] = "Notification"
STOP_EVENT: Final[str] = "Stop"

#: The bundled chime, resolved from *this script's* location so the repo's ``.claude/``
#: and an installed ``~/.claude/`` each play their own copy — the same
#: project-first-then-home resolution ``run_hook.py`` applies to the scripts.
SOUND_FILE: Final[Path] = Path(__file__).resolve().parent.parent / "sounds" / "notify.wav"

#: Where each platform keeps the sound chosen for each event. Keyed by ``sys.platform``;
#: a platform absent here (a BSD, anything exotic) has no native pick and plays the
#: bundled file.
NATIVE_SOUNDS: Final[dict[str, dict[str, Path]]] = {
    "darwin": {
        NOTIFICATION_EVENT: Path("/System/Library/Sounds/Glass.aiff"),
        STOP_EVENT: Path("/System/Library/Sounds/Funk.aiff"),
    },
    "win32": {
        NOTIFICATION_EVENT: Path(r"C:\Windows\Media\Windows Notify System Generic.wav"),
        STOP_EVENT: Path(r"C:\Windows\Media\chord.wav"),
    },
    "linux": {
        NOTIFICATION_EVENT: Path("/usr/share/sounds/freedesktop/stereo/message.oga"),
        STOP_EVENT: Path("/usr/share/sounds/freedesktop/stereo/complete.oga"),
    },
}

#: The POSIX players tried in order, per platform. Anything else (a BSD, a headless
#: box with neither player) falls through to the documented silent no-op.
_PLAYERS: Final[dict[str, tuple[str, ...]]] = {
    "darwin": ("afplay",),
    "linux": ("paplay", "aplay"),
}


def select_sound(platform: str, event: str, exists: Callable[[Path], bool]) -> Path:
    """Pick the file to play for one platform/event pair.

    Pure by construction: the host enters only through *platform* and *exists*, so the
    whole mapping — every platform, both events, both fallbacks — can be pinned by a unit
    test without touching the filesystem.

    Args:
        platform: The host's ``sys.platform`` value. One without a native mapping gets
            the bundled file.
        event: The payload's ``hook_event_name``. Anything other than ``Stop`` — an
            unknown name, or none at all — is treated as ``Notification``.
        exists: Predicate deciding whether a candidate file is present on this host,
            normally ``Path.is_file``.

    Returns:
        The platform's native sound for this event when the host has it, else the
        bundled ``SOUND_FILE``.
    """
    natives = NATIVE_SOUNDS.get(platform)
    if natives is None:
        return SOUND_FILE
    native = natives[STOP_EVENT if event == STOP_EVENT else NOTIFICATION_EVENT]
    return native if exists(native) else SOUND_FILE


def _play_with(player: str, path: Path) -> int:
    """Run one POSIX player on one file and report how it went.

    Args:
        player: Absolute path to the player executable.
        path: The sound file to play.

    Returns:
        The player's exit status; non-zero means nothing was heard.
    """
    return subprocess.run(
        [player, str(path)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode


def play(path: Path) -> None:
    """Play one sound file through this host's player, best-effort.

    A player that cannot play the file gets one — and only one — retry on the bundled
    ``SOUND_FILE``; the bundled file itself is never retried, so a dead audio stack ends
    in silence rather than a loop.

    Args:
        path: The file to play. A missing file is a silent no-op, never an error.

    Returns:
        None. A host with no usable player is also a silent no-op — a hook must not
        break a session over a sound.
    """
    if not path.is_file():
        return
    if sys.platform == "win32":
        # Async so the hook does not sit through the sound; SND_FILENAME says the
        # argument is a path rather than a system alias. winsound plays WAV only, which
        # is why both Windows picks and the fallback are WAV.
        flags = winsound.SND_FILENAME | winsound.SND_ASYNC
        try:
            winsound.PlaySound(str(path), flags)
        except RuntimeError:
            # PlaySound reports "could not play that" by raising, async included — the
            # Windows equivalent of a non-zero player exit, so the same single retry.
            # The fallback has no fallback: a second failure is silence, not an error.
            if path != SOUND_FILE and SOUND_FILE.is_file():
                with contextlib.suppress(RuntimeError):
                    winsound.PlaySound(str(SOUND_FILE), flags)
        return
    player = next(
        (found for name in _PLAYERS.get(sys.platform, ()) if (found := shutil.which(name))), None
    )
    if player is None:
        return
    if _play_with(player, path) != 0 and path != SOUND_FILE and SOUND_FILE.is_file():
        # A freedesktop ``.oga`` needs a Vorbis-capable paplay (and aplay never decodes
        # one): the player exits non-zero having played nothing. One retry on the bundled
        # WAV — if that fails too the host simply stays silent.
        _play_with(player, SOUND_FILE)


def main() -> int:
    """Entry point: read the hook payload and play the sound its event calls for.

    A payload that is not a hook invocation at all — garbled, empty, or a stdin that
    cannot be read — stays silent; one without a usable ``hook_event_name`` falls back to
    the ``Notification`` sound rather than guessing.

    Returns:
        Always 0: the hook is best-effort and must never block or break a session.
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, OSError):
        # No/garbled payload, or a broken stdin — stay silent rather than break the hook.
        return 0

    name = data.get("hook_event_name") if isinstance(data, dict) else None
    event = name if isinstance(name, str) else NOTIFICATION_EVENT
    try:
        play(select_sound(sys.platform, event, Path.is_file))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
