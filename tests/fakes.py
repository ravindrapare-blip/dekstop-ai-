"""A pretend Mac for the action tests: a fake Applications folder, a fake home folder, and a fake `run`
that records the commands the assistant would have run instead of running them."""
import plistlib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from automation import ActionService
from automation.applescript import AppleScript
from automation.apps import AppCatalog
from automation.controller import AppController
from automation.files import PRIMARY_DIRS, SECONDARY_DIRS, FileFinder

APPS = {
    "Safari": "com.apple.Safari",
    "Google Chrome": "com.google.Chrome",
    "Visual Studio Code": "com.microsoft.VSCode",
    "Finder": "com.apple.finder",
    "Notes": "com.apple.Notes",
    "System Preferences": "com.apple.systempreferences",
}


class FakeRun:
    """Stands in for subprocess.run. Records every command; `running` says what osascript reports."""

    def __init__(self, returncode: int = 0, running: bool = True):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.running = running

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        stdout = ""
        if "osascript" in command[0] and any("is running" in part for part in command):
            stdout = "true\n" if self.running else "false\n"
        return subprocess.CompletedProcess(command, self.returncode, stdout=stdout, stderr="")

    def commands(self, program: str) -> list[list[str]]:
        return [c for c in self.calls if c[0].endswith(program)]


@dataclass
class World:
    service: ActionService
    run: FakeRun
    home: Path
    apps_dir: Path


def make_app(folder: Path, name: str, bundle_id: str) -> Path:
    contents = folder / f"{name}.app" / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle_id}))
    return folder / f"{name}.app"


def make_world(tmp_path: Path, apps=APPS, hammerspoon=None, run: FakeRun | None = None, **finder_options) -> World:
    apps_dir = tmp_path / "Applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    for name, bundle_id in apps.items():
        make_app(apps_dir, name, bundle_id)
    home = tmp_path / "home"
    for folder in ("Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music"):
        (home / folder).mkdir(parents=True, exist_ok=True)
    run = run or FakeRun()
    finder = FileFinder(PRIMARY_DIRS, SECONDARY_DIRS, home=home, **finder_options)
    controller = AppController(hammerspoon, AppleScript(run=run), run=run)
    service = ActionService(AppCatalog(dirs=[str(apps_dir)], extra={}), finder, controller)
    return World(service, run, home.resolve(), apps_dir)


def quick_actions() -> ActionService:
    """For tests that only need *an* action service."""
    return make_world(Path(tempfile.mkdtemp(prefix="assistant-test-"))).service
