"""Creating symlinks in a test, on platforms where an account may not be allowed to.

POSIX accounts always may; a Windows account may only with Developer Mode or the
``SeCreateSymbolicLinkPrivilege``, and otherwise gets ``OSError: [WinError 1314]``. That is
a missing *capability*, not a failing behaviour, so the test skips there and still runs
everywhere symlinks work — rather than being switched off for all of Windows.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    """Point ``link`` at ``target``, skipping the test when the account may not.

    Args:
        link: The symlink to create.
        target: What it points at.
        target_is_directory: Windows needs to be told; ignored on POSIX.

    Raises:
        Skipped: When the platform or account forbids creating symlinks.
    """
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        pytest.skip(f"symlinks not permitted for this account ({exc})")
