"""Controlled offline CLI stand-in, executed only by integration tests."""

import subprocess
import sys
import time
from pathlib import Path

from tests.helpers.debate import answer


def main() -> int:
    """Emulate readiness and final-output delivery without credentials or networking."""
    vendor, mode, *args = sys.argv[1:]
    with Path("launches.txt").open("a", encoding="utf-8") as log:
        log.write(f"{vendor} {'ready' if 'status' in args else 'seat'}\n")
    if "status" in args:
        if mode == "readiness":
            time.sleep(20)
        return 0
    prompt = sys.stdin.read()
    if mode == "canary":
        Path("claude-temp/sessions/unexpected").mkdir(exist_ok=True)
    if mode == "empty":
        return 0
    if mode == "grandchild":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        Path("grandchild.pid").write_text(str(child.pid), encoding="utf-8")
        time.sleep(60)
    phase = prompt.splitlines()[0].removeprefix("PHASE ")
    text = answer(phase)
    print("controlled transcript", file=sys.stderr, flush=True)
    if vendor == "codex":
        Path(args[args.index("-o") + 1]).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
