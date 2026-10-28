"""Finding installed apps by name. Only the standard application folders are read."""
import os
import plistlib
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from automation.fuzzy import APP_ALIASES, APP_FILLER, Pick, pick, rank, strip_leading_verbs, strip_words

APP_DIRS = (
    "/Applications",
    "/Applications/Utilities",
    "/System/Applications",
    "/System/Applications/Utilities",
    "~/Applications",
)
# Finder lives with the system's helper apps, most of which should not be offered, so it is listed by name.
EXTRA_APPS = {"/System/Library/CoreServices": ("Finder.app",)}

_BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


@dataclass(frozen=True)
class AppInfo:
    name: str
    path: str


def bundle_id(app_path: str) -> str | None:
    """The app's bundle identifier from its Info.plist, or None if it cannot be read."""
    try:
        with open(Path(app_path) / "Contents" / "Info.plist", "rb") as f:
            value = plistlib.load(f).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    return value if isinstance(value, str) and _BUNDLE_ID.match(value) else None


class AppCatalog:
    def __init__(self, dirs=APP_DIRS, extra=EXTRA_APPS, *, ttl: float = 60.0, clock=time.monotonic):
        self.dirs = [Path(d).expanduser() for d in dirs]
        self.extra = {Path(d): names for d, names in extra.items()}
        self.ttl = ttl
        self._clock = clock
        self._cache: list[AppInfo] = []
        self._loaded_at = -1e9
        self._lock = threading.Lock()

    def apps(self) -> list[AppInfo]:
        with self._lock:
            if self._clock() - self._loaded_at > self.ttl:
                self._cache = self._scan()
                self._loaded_at = self._clock()
            return list(self._cache)

    def _scan(self) -> list[AppInfo]:
        found: dict[str, AppInfo] = {}
        for folder in self.dirs:
            for entry in self._entries(folder):
                found.setdefault(entry.name.lower(), entry)    # earlier folders win
        for folder, names in self.extra.items():
            for entry in self._entries(folder, only=names):
                found.setdefault(entry.name.lower(), entry)
        return sorted(found.values(), key=lambda a: a.name.lower())

    @staticmethod
    def _entries(folder: Path, only: tuple = ()) -> list[AppInfo]:
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError:
            return []
        apps = []
        for e in entries:
            if not e.name.endswith(".app") or e.name.startswith("."):
                continue
            if only and e.name not in only:
                continue
            if e.is_dir():
                apps.append(AppInfo(e.name[: -len(".app")], str(Path(folder) / e.name)))
        return apps

    def exact(self, name: str) -> AppInfo | None:
        """The app with exactly this name (ignoring case), or None."""
        wanted = name.strip().lower()
        return next((a for a in self.apps() if a.name.lower() == wanted), None)

    def find(self, query: str) -> Pick:
        cleaned = strip_words(strip_leading_verbs(query), APP_FILLER)
        return pick(rank(cleaned, self.apps(), lambda a: a.name, aliases=APP_ALIASES))
