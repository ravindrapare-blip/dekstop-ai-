"""Finding files and folders by name, only in the folders people actually use.

Nothing here ever scans the whole disk:
  * only a few configured folders under the home folder are searched, to a limited depth
  * hidden items, symlinks, app/library packages and noisy folders (Library, node_modules, ...) are skipped
  * a cap on the number of items looked at and a time budget stop a search that gets too large
The first tier (Desktop, Documents, Downloads) is tried first; the second only if nothing was found.
"""
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from automation.fuzzy import Pick, normalize, pick, rank, strip_words, Ranked

PRIMARY_DIRS = ("~/Desktop", "~/Documents", "~/Downloads")
SECONDARY_DIRS = ("~/Pictures", "~/Movies", "~/Music", "~")

SKIP_DIR_NAMES = {
    "library", "node_modules", "__pycache__", "venv", "site-packages", "bower_components", "pods", "deriveddata",
}
# Folders that are really single files to the user. They are listed but never entered.
PACKAGE_SUFFIXES = (
    ".app", ".photoslibrary", ".musiclibrary", ".tvlibrary", ".fcpbundle", ".framework", ".bundle",
    ".kext", ".xcodeproj", ".xcworkspace", ".playground", ".imovielibrary", ".dsym",
)

KNOWN_FOLDERS = {
    "desktop": "Desktop", "documents": "Documents", "downloads": "Downloads",
    "pictures": "Pictures", "movies": "Movies", "music": "Music",
}
FOLDER_FILLER = {"the", "my", "folder", "directory", "please"}
FILE_FILLER = {
    "the", "a", "an", "my", "please", "file", "files", "called", "named", "name", "with", "in", "of", "document",
    "folder",
}
TYPE_WORDS = {
    "pdf": {".pdf"}, "pdfs": {".pdf"},
    "spreadsheet": {".xls", ".xlsx", ".csv", ".numbers"}, "excel": {".xls", ".xlsx"},
    "presentation": {".ppt", ".pptx", ".key"}, "powerpoint": {".ppt", ".pptx"}, "slides": {".ppt", ".pptx", ".key"},
    "image": {".png", ".jpg", ".jpeg", ".heic", ".gif"}, "photo": {".png", ".jpg", ".jpeg", ".heic"},
    "picture": {".png", ".jpg", ".jpeg", ".heic"},
    "video": {".mp4", ".mov", ".m4v"}, "movie": {".mp4", ".mov", ".m4v"},
    "audio": {".mp3", ".m4a", ".wav", ".aac"}, "song": {".mp3", ".m4a"},
}
EXTENSION_WORDS = {"png", "jpg", "jpeg", "gif", "docx", "doc", "xlsx", "xls", "csv", "pptx", "ppt", "txt", "md",
                   "zip", "mp3", "mp4", "mov", "pages", "numbers", "key", "rtf", "heic"}


@dataclass(frozen=True)
class FileMatch:
    path: str
    name: str
    is_dir: bool
    mtime: float = 0.0


@dataclass
class Lookup:
    kind: str                                   # "exact", "one", "many" or "none"
    matches: list = field(default_factory=list)
    searched: list = field(default_factory=list)    # folder names that were searched
    truncated: bool = False                     # a search stopped early to stay fast
    reason: str = ""                            # for "none": "missing_path", "not_folder" or "no_match"


@dataclass(frozen=True)
class Query:
    terms: str
    extensions: frozenset


def parse_file_query(text: str) -> Query:
    """"the PDF called budget" -> terms "budget", extensions {.pdf}.  "budget.pdf" works too."""
    extensions: set[str] = set()
    words = []
    for token in text.split():
        m = re.fullmatch(r"(.+)\.([A-Za-z0-9]{1,5})", token)
        if m and re.search(r"[A-Za-z]", m.group(2)):
            extensions.add("." + m.group(2).lower())
            token = m.group(1)
        words.append(token)
    terms = []
    for word in strip_words(" ".join(words), FILE_FILLER).split():
        if word in TYPE_WORDS:
            extensions |= TYPE_WORDS[word]
        elif word in EXTENSION_WORDS:
            extensions.add("." + word)
        else:
            terms.append(word)
    return Query(" ".join(terms), frozenset(extensions))


def is_within(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


class _Budget:
    def __init__(self, max_entries: int, seconds: float, clock):
        self.left = max_entries
        self.deadline = clock() + seconds
        self.clock = clock
        self.truncated = False

    def spend(self) -> bool:
        """True while the search may continue."""
        self.left -= 1
        if self.left < 0 or self.clock() > self.deadline:
            self.truncated = True
            return False
        return True


class FileFinder:
    def __init__(
        self,
        primary=PRIMARY_DIRS,
        secondary=SECONDARY_DIRS,
        *,
        home: Path | str | None = None,
        max_depth: int = 3,
        max_entries: int = 20_000,
        time_budget: float = 2.0,
        clock=time.monotonic,
    ) -> None:
        self.home = Path(home or Path.home()).resolve()
        self.max_depth = max_depth
        self.max_entries = max_entries
        self.time_budget = time_budget
        self.clock = clock
        self.primary = self._safe_roots(primary)
        self.secondary = [r for r in self._safe_roots(secondary) if r not in self.primary]

    # -- roots -----------------------------------------------------------------------------------------------

    def _expand(self, value: str) -> Path:
        if value == "~":
            return self.home
        if value.startswith("~/"):
            return self.home / value[2:]
        return Path(value)

    def _safe_roots(self, values) -> list[Path]:
        """Only existing folders inside the home folder. Anything else (like "/") is dropped."""
        roots = []
        for value in values:
            try:
                path = self._expand(str(value)).resolve()
            except OSError:
                continue
            if path.is_dir() and is_within(str(path), str(self.home)) and str(path) != os.sep:
                roots.append(path)
        return roots

    def _depth(self, root: Path, tier_depth: int) -> int:
        return 0 if root == self.home else tier_depth    # the home folder itself is only listed, never entered

    # -- walking ---------------------------------------------------------------------------------------------

    def _scan(self, root: Path, max_depth: int, budget: _Budget):
        stack = [(root, 0)]
        while stack:
            folder, depth = stack.pop()
            try:
                it = os.scandir(folder)
            except OSError:
                continue
            with it:
                while True:
                    try:
                        entry = next(it)
                    except StopIteration:
                        break
                    except OSError:
                        break
                    if not budget.spend():
                        return
                    name = entry.name
                    if name.startswith("."):
                        continue
                    try:
                        if entry.is_symlink():
                            continue                    # never follow links out of the searched folders
                        is_dir = entry.is_dir(follow_symlinks=False)
                        mtime = entry.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        continue
                    lower = name.lower()
                    package = is_dir and lower.endswith(PACKAGE_SUFFIXES)
                    path = str(Path(folder) / name)
                    yield FileMatch(path, name, is_dir and not package, mtime)
                    if is_dir and not package and lower not in SKIP_DIR_NAMES and depth + 1 <= max_depth:
                        stack.append((Path(path), depth + 1))

    def _collect(self, roots, tier_depth, kind, extensions, budget) -> list[FileMatch]:
        found = []
        for root in roots:
            for m in self._scan(root, self._depth(root, tier_depth), budget):
                if kind == "file" and m.is_dir:
                    continue
                if kind == "folder" and not m.is_dir:
                    continue
                if extensions and (m.is_dir or Path(m.name).suffix.lower() not in extensions):
                    continue
                found.append(m)
            if budget.truncated:
                break
        return found

    @staticmethod
    def _stem(m: FileMatch) -> str:
        return m.name if m.is_dir else Path(m.name).stem

    def _rank(self, query: Query, candidates: list[FileMatch]) -> list[Ranked]:
        if not query.terms:
            return [Ranked(0.7, m) for m in sorted(candidates, key=lambda m: -m.mtime)]
        return rank(normalize(query.terms), candidates, self._stem, tie_break=lambda m: m.mtime)

    def _tiers(self):
        return ((self.primary, self.max_depth), (self.secondary, max(1, self.max_depth - 1)))

    # -- public ------------------------------------------------------------------------------------------------

    def display(self, path: str) -> str:
        """A path with the home folder shortened to ~."""
        return "~" + path[len(str(self.home)):] if is_within(path, str(self.home)) else path

    def locate(self, text: str, kind: str = "any") -> Lookup:
        """Find the one thing the user means. `kind` is "file", "folder" or "any"."""
        raw = text.strip().strip("\"'").strip()
        if not raw:
            return Lookup("none", reason="no_match")

        exact = self._exact(raw, kind)
        if exact is not None:
            return exact

        query = parse_file_query(raw)
        if not query.terms and not query.extensions:
            return Lookup("none", reason="no_match")

        searched: list[str] = []
        truncated = False
        for roots, depth in self._tiers():
            if not roots:
                continue
            budget = _Budget(self.max_entries, self.time_budget, self.clock)
            ranked = self._rank(query, self._collect(roots, depth, kind, query.extensions, budget))
            searched += [r.name for r in roots]
            truncated = truncated or budget.truncated
            chosen = pick(ranked)
            if chosen.kind != "none":
                return Lookup(chosen.kind, list(chosen.items), searched, truncated)
        return Lookup("none", [], searched, truncated, "no_match")

    def search(self, text: str, limit: int = 10) -> Lookup:
        """Everything that matches, best first (for "search for ..."), searched tier by tier."""
        query = parse_file_query(text)
        if not query.terms and not query.extensions:
            return Lookup("none", reason="no_match")
        searched: list[str] = []
        truncated = False
        for roots, depth in self._tiers():
            if not roots:
                continue
            budget = _Budget(self.max_entries, self.time_budget, self.clock)
            ranked = self._rank(query, self._collect(roots, depth, "any", query.extensions, budget))
            searched += [r.name for r in roots]
            truncated = truncated or budget.truncated
            if ranked:
                return Lookup("many", [r.item for r in ranked[:limit]], searched, truncated)
        return Lookup("none", [], searched, truncated, "no_match")

    def recent(self, limit: int = 10) -> Lookup:
        """The most recently changed files in the first-tier folders."""
        budget = _Budget(self.max_entries, self.time_budget, self.clock)
        files = self._collect(self.primary, 2, "file", frozenset(), budget)
        files.sort(key=lambda m: -m.mtime)
        return Lookup("many" if files else "none", files[:limit], [r.name for r in self.primary],
                      budget.truncated, "" if files else "no_match")

    # -- exact paths and well-known folders -----------------------------------------------------------------------

    def _exact(self, raw: str, kind: str) -> Lookup | None:
        if raw.startswith("/") or raw == "~" or raw.startswith("~/"):
            path = os.path.abspath(str(self._expand(raw)) if raw.startswith("~") else raw)
            if not os.path.exists(path):
                return Lookup("none", reason="missing_path")
            return self._exact_result(path, kind)

        words = strip_words(raw, FOLDER_FILLER)
        if kind in ("folder", "any"):
            if words in KNOWN_FOLDERS:
                return self._exact_result(str(self.home / KNOWN_FOLDERS[words]), kind, must_exist=True)
            if words == "home":
                return self._exact_result(str(self.home), kind)
            if words == "applications":
                return self._exact_result("/Applications", kind, must_exist=True)

        relative = Path(raw)
        if ".." not in relative.parts and (self.home / relative).exists() and raw not in (".", ""):
            return self._exact_result(str(self.home / relative), kind)
        return None

    def _exact_result(self, path: str, kind: str, must_exist: bool = False) -> Lookup:
        if must_exist and not os.path.exists(path):
            return Lookup("none", reason="missing_path")
        is_dir = os.path.isdir(path)
        if kind == "folder" and not is_dir:
            return Lookup("none", reason="not_folder")
        return Lookup("exact", [FileMatch(path, os.path.basename(path) or path, is_dir)])
