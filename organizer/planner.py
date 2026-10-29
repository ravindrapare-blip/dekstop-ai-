"""Builds the plan (the preview). Reads folders; never changes anything.

A plan lists, for the top level of one folder only: which files go where, which names clash (and what they are
renamed to), and what is left alone and why. Folders are never entered and never moved.
"""
import os
import secrets
import stat
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from organizer.categories import CATEGORIES, Filter, categorize, is_hidden_or_system

MAX_NAME_BYTES = 255
HIDDEN_FLAG = getattr(stat, "UF_HIDDEN", 0)
LOCKED_FLAG = getattr(stat, "UF_IMMUTABLE", 0)


def fold(name: str) -> str:
    """How the Mac compares names: ignoring case and Unicode form (é can be one character or two)."""
    return unicodedata.normalize("NFC", name).casefold()


@dataclass(frozen=True)
class PlannedMove:
    name: str
    group: str                    # the category, or the destination folder's name
    dest_dir: Path
    dest_name: str
    size: int
    mtime_ns: int
    inode: int

    @property
    def renamed(self) -> bool:
        return self.dest_name != self.name


@dataclass
class Plan:
    id: str
    folder: Path
    moves: list[PlannedMove] = field(default_factory=list)
    skipped: dict[str, list[str]] = field(default_factory=dict)         # reason -> names
    new_dirs: list[Path] = field(default_factory=list)
    only: str = ""                # what the user limited it to ("pdfs"), for the messages
    into: str = ""                # the destination folder the user asked for, if any
    into_path: Path | None = None
    truncated: bool = False
    created_at: datetime | None = None

    def groups(self) -> dict[tuple[str, Path], list[PlannedMove]]:
        out: dict[tuple[str, Path], list[PlannedMove]] = defaultdict(list)
        for m in self.moves:
            out[(m.group, m.dest_dir)].append(m)
        return out

    def categories(self) -> set[str]:
        return {m.group for m in self.moves}


@dataclass(frozen=True)
class Destination:
    path: Path
    exists: bool
    label: str


def unique_name(name: str, taken: set[str], stamp: str) -> str:
    """`name` if free, else "name 2026-10-29 14-05-33.ext", else with " 2", " 3" ... Nothing is ever overwritten."""
    if fold(name) not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    else:
        ext = "." + ext
    counter = 1
    while True:
        suffix = f" {stamp}" + (f" {counter}" if counter > 1 else "")
        room = MAX_NAME_BYTES - len((suffix + ext).encode())
        candidate = stem.encode()[:max(1, room)].decode(errors="ignore") + suffix + ext
        if fold(candidate) not in taken:
            return candidate
        counter += 1


def stamp_of(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H-%M-%S")           # no colons: Finder doesn't allow them in names


def _flags(info: os.stat_result) -> int:
    """macOS file flags (hidden, locked); 0 on systems without them."""
    return getattr(info, "st_flags", 0)


def _listing(path: Path) -> list[os.DirEntry]:
    with os.scandir(path) as it:
        return sorted(it, key=lambda e: (fold(e.name), e.name))      # a steady order, even for names that differ only by case


def build_plan(folder: Path, now: datetime, *, only: Filter | None = None, into: Destination | None = None,
               settle_seconds: float = 60.0, max_files: int = 200) -> Plan:
    plan = Plan(secrets.token_hex(6), folder, only=only.label if only else "", into=into.label if into else "",
                into_path=into.path if into else None, created_at=now)
    skipped: dict[str, list[str]] = defaultdict(list)
    entries = _listing(folder)
    siblings = {fold(e.name): e for e in entries}
    stamp = stamp_of(now)

    candidates = []
    for entry in entries:
        name = entry.name
        try:
            info = entry.stat(follow_symlinks=False)
            if entry.is_symlink():
                skipped["shortcuts and links"].append(name)
            elif entry.is_dir(follow_symlinks=False):
                skipped["folders"].append(name)
            elif is_hidden_or_system(name) or _flags(info) & HIDDEN_FLAG:
                skipped["hidden or system files"].append(name)
            elif not stat.S_ISREG(info.st_mode):
                skipped["special files"].append(name)
            elif _flags(info) & LOCKED_FLAG:
                skipped["locked files"].append(name)
            elif now.timestamp() - info.st_mtime < settle_seconds:
                skipped["files changed in the last minute (maybe still downloading)"].append(name)
            else:
                category = categorize(name)
                if only is not None and not only.matches(name, category):
                    skipped["other kinds of files"].append(name)
                else:
                    candidates.append((name, category, info))
        except OSError:
            skipped["files I couldn't read"].append(name)

    if len(candidates) > max_files:
        plan.truncated = True
        candidates = candidates[:max_files]

    dirs: dict[Path, set[str]] = {}                        # destination dir -> names already in it (folded)

    def destination(group: str) -> tuple[Path | None, str]:
        if into is not None:
            path = into.path
        else:
            existing = siblings.get(fold(group))
            path = folder / (existing.name if existing else group)
            if existing is not None and not (existing.is_dir(follow_symlinks=False)):
                return None, f"something that isn't a folder is already named “{existing.name}”"
        if path not in dirs:
            names: set[str] = set()
            if path.is_dir():
                names = {fold(e.name) for e in _listing(path)}
            elif path not in plan.new_dirs and not path.exists():
                plan.new_dirs.append(path)
            dirs[path] = names
        return path, ""

    for name, category, info in candidates:
        group = into.label if into is not None else category
        dest_dir, problem = destination(group)
        if dest_dir is None:
            skipped[problem].append(name)
            continue
        names = dirs[dest_dir]
        final = unique_name(name, names, stamp)
        names.add(fold(final))
        plan.moves.append(PlannedMove(name, group, dest_dir, final, info.st_size, info.st_mtime_ns, info.st_ino))

    plan.skipped = dict(skipped)
    return plan


def group_rank(group: str) -> tuple[int, str]:
    """The standard categories in their usual order, then any destination folder by name."""
    return (CATEGORIES.index(group) if group in CATEGORIES else len(CATEGORIES), group.lower())


def order_groups(plan: Plan):
    return sorted(plan.groups().items(), key=lambda kv: group_rank(kv[0][0]))
