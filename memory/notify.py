"""macOS notifications through osascript. The text is passed as script arguments, never pasted into the
script, so a reminder can never be read as AppleScript code. Nothing leaves the Mac."""
import logging
import re
import subprocess

log = logging.getLogger("assistant.notify")

_SCRIPT = ["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run"]
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _line(text: str, limit: int) -> str:
    out = " ".join(_CONTROL.sub(" ", str(text)).split())
    return out if len(out) <= limit else out[: limit - 1] + "…"


class Notifier:
    def __init__(self, *, enabled: bool = True, run=subprocess.run, binary: str = "/usr/bin/osascript", timeout: float = 10.0):
        self.enabled = enabled
        self._run = run
        self.binary = binary
        self.timeout = timeout

    def send(self, title: str, body: str) -> bool:
        """True if macOS accepted the notification. A turned-off notifier counts as success: there is nothing to retry."""
        if not self.enabled:
            return True
        command = [self.binary]
        for line in _SCRIPT:
            command += ["-e", line]
        command += [_line(title, 60) or "Assistant", _line(body, 250)]
        try:
            proc = self._run(command, capture_output=True, text=True, timeout=self.timeout, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("notification not sent (%s)", type(e).__name__)
            return False
        if proc.returncode != 0:
            log.warning("notification not sent: %s", (proc.stderr or "osascript failed").strip()[:200])
            return False
        return True
