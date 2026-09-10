#!/usr/bin/env python3
"""Attention hook for Claude Code: play one bundled chime when Claude wants the user.

Reads a Claude Code hook event as JSON on stdin and plays a sound. Wired to two hook
events in ``.claude/settings.json``:

- ``Notification`` — Claude is waiting on the user (a permission prompt, or the prompt
  has gone idle). This is the "user action needed" signal.
- ``Stop`` — Claude finished responding; it's the user's turn again.

Both events play the *same* file on purpose: the signal is "look at me", and a sound
needs no notification-centre permission, no registered application id, and no per-OS
banner machinery — which is what this hook used to be, and what made it the most likely
thing to fail silently on a fresh machine.

Playback is stdlib-first per platform: ``winsound`` on Windows, ``afplay`` on macOS
(part of the OS), PulseAudio's ``paplay`` or ALSA's ``aplay`` on Linux.

The hook is best-effort: a garbled payload, a missing sound file, a host with no player,
or any failure at all exits 0, so it never blocks or breaks the session.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

if sys.platform == "win32":  # stdlib, but Windows-only — no import to make elsewhere
    import winsound

#: The bundled chime, resolved from *this script's* location so the repo's ``.claude/``
#: and an installed ``~/.claude/`` each play their own copy — the same
#: project-first-then-home resolution ``run_hook.py`` applies to the scripts.
SOUND_FILE: Final[Path] = Path(__file__).resolve().parent.parent / "sounds" / "notify.wav"

#: The POSIX players tried in order, per platform. Anything else (a BSD, a headless
#: box with neither player) falls through to the documented silent no-op.
_PLAYERS: Final[dict[str, tuple[str, ...]]] = {
    "darwin": ("afplay",),
    "linux": ("paplay", "aplay"),
}


def play(path: Path) -> None:
    """Play one WAV file through this host's player, best-effort.

    Args:
        path: The WAV to play. A missing file is a silent no-op, never an error.

    Returns:
        None. A host with no usable player is also a silent no-op — a hook must not
        break a session over a sound.
    """
    if not path.is_file():
        return
    if sys.platform == "win32":
        # Async so the hook does not sit through the sound; SND_FILENAME says the
        # argument is a path rather than a system alias.
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return
    player = next(
        (found for name in _PLAYERS.get(sys.platform, ()) if (found := shutil.which(name))), None
    )
    if player is not None:
        subprocess.run(
            [player, str(path)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


def main() -> int:
    """Entry point: consume the hook payload and play the chime.

    The payload's content is irrelevant now that both events share one sound; it is read
    only so that a garbled one — i.e. not a hook invocation at all — stays silent.

    Returns:
        Always 0: the hook is best-effort and must never block or break a session.
    """
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # No/garbled payload — stay silent rather than break the hook.

    try:
        play(SOUND_FILE)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
