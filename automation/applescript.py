"""AppleScript through osascript, used when Hammerspoon is not available.

The app is passed as a script argument, never pasted into the script text, so a name can never be
read as AppleScript code.
"""
import re
import subprocess

_BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")

_SCRIPTS = {
    "is_running": ["on run argv", "return application id (item 1 of argv) is running", "end run"],
    "activate": ["on run argv", "tell application id (item 1 of argv) to activate", "end run"],
    "quit": ["on run argv", "tell application id (item 1 of argv) to quit", "end run"],
}


class AppleScriptError(Exception):
    pass


class AppleScript:
    def __init__(self, *, run=subprocess.run, binary: str = "/usr/bin/osascript", timeout: float = 10.0):
        self._run = run
        self.binary = binary
        self.timeout = timeout

    def call(self, script: str, bundle_id: str) -> str:
        if script not in _SCRIPTS:
            raise AppleScriptError(f"unknown script {script!r}")
        if not _BUNDLE_ID.match(bundle_id or ""):
            raise AppleScriptError("that app has no usable identifier")
        command = [self.binary]
        for line in _SCRIPTS[script]:
            command += ["-e", line]
        command.append(bundle_id)
        try:
            proc = self._run(command, capture_output=True, text=True, timeout=self.timeout, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise AppleScriptError(f"osascript did not finish ({type(e).__name__})") from None
        if proc.returncode != 0:
            raise AppleScriptError((proc.stderr or "osascript failed").strip()[:200])
        return (proc.stdout or "").strip()

    def is_running(self, bundle_id: str) -> bool:
        return self.call("is_running", bundle_id).lower() == "true"
