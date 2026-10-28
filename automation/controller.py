"""Opening, focusing and quitting apps. Hammerspoon first, then AppleScript or `open` as fallbacks."""
import subprocess
from dataclasses import dataclass

from automation.applescript import AppleScript, AppleScriptError
from automation.apps import AppInfo, bundle_id
from automation.hammerspoon import HammerspoonClient, HammerspoonUnavailable


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str
    via: str = ""


class AppController:
    def __init__(
        self,
        hammerspoon: HammerspoonClient | None,
        applescript: AppleScript | None = None,
        *,
        run=subprocess.run,
        open_binary: str = "/usr/bin/open",
    ) -> None:
        self.hammerspoon = hammerspoon
        self.applescript = applescript or AppleScript()
        self._run = run
        self.open_binary = open_binary

    # -- apps ------------------------------------------------------------------------------------------------

    def open_app(self, app: AppInfo) -> Outcome:
        """Starts the app, or brings it to the front if it is already running."""
        bid = bundle_id(app.path)
        if self.hammerspoon is not None:
            try:
                reply = self.hammerspoon.act("open_app", bundle_id=bid, path=app.path)
                if reply.get("launched"):
                    return Outcome(True, f"Opened {app.name}.", "Hammerspoon")
            except HammerspoonUnavailable:
                pass
        if self._open(["-a", app.path]):
            return Outcome(True, f"Opened {app.name}.", "open")
        return Outcome(False, f"I couldn't open {app.name}.", "open")

    def focus_app(self, app: AppInfo) -> Outcome:
        """Brings a running app to the front. It does not start apps that are not running."""
        bid = bundle_id(app.path)
        if self.hammerspoon is not None and bid:
            try:
                reply = self.hammerspoon.act("focus_app", bundle_id=bid)
                if reply.get("running"):
                    return Outcome(True, f"Switched to {app.name}.", "Hammerspoon")
                return Outcome(False, f"{app.name} isn't running.", "Hammerspoon")
            except HammerspoonUnavailable:
                pass
        try:
            if not self.applescript.is_running(bid or ""):
                return Outcome(False, f"{app.name} isn't running.", "AppleScript")
            self.applescript.call("activate", bid)
        except AppleScriptError:
            return Outcome(False, f"I couldn't switch to {app.name}.", "AppleScript")
        return Outcome(True, f"Switched to {app.name}.", "AppleScript")

    def close_app(self, app: AppInfo) -> Outcome:
        """Asks the app to quit, the same as pressing Cmd+Q. It may still ask about unsaved work."""
        bid = bundle_id(app.path)
        if self.hammerspoon is not None and bid:
            try:
                reply = self.hammerspoon.act("quit_app", bundle_id=bid)
                if reply.get("running"):
                    return Outcome(True, f"Asked {app.name} to quit.", "Hammerspoon")
                return Outcome(False, f"{app.name} isn't running.", "Hammerspoon")
            except HammerspoonUnavailable:
                pass
        try:
            if not self.applescript.is_running(bid or ""):
                return Outcome(False, f"{app.name} isn't running.", "AppleScript")
            self.applescript.call("quit", bid)
        except AppleScriptError:
            return Outcome(False, f"I couldn't quit {app.name}.", "AppleScript")
        return Outcome(True, f"Asked {app.name} to quit.", "AppleScript")

    # -- files and folders -------------------------------------------------------------------------------------

    def open_path(self, path: str) -> bool:
        """Opens a file in its default app, or a folder in Finder. `path` must be absolute."""
        return path.startswith("/") and self._open([path])

    def _open(self, args: list[str]) -> bool:
        try:
            proc = self._run([self.open_binary, *args], capture_output=True, text=True, timeout=15,
                             stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0
