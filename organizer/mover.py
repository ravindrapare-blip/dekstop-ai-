"""Does the moving, for a plan the user approved. Every step is re-checked against what is on disk NOW.

Rules that cannot be bent:
  * nothing is ever overwritten (a name clash gets a timestamped name)
  * files are only renamed within this computer's disk: never copied, never deleted
  * a file that changed since the preview is left alone
  * every folder created and every move is written to the move log BEFORE it happens; if that fails, nothing more moves
"""
import errno
import logging
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from organizer.movelog import MoveLog
from organizer.planner import Plan, PlannedMove, fold, stamp_of, unique_name

log = logging.getLogger("assistant.organizer")


@dataclass
class Report:
    moved: list[tuple[PlannedMove, str]] = field(default_factory=list)       # (move, the name it ended up with)
    skipped: list[tuple[str, str]] = field(default_factory=list)             # (file name, why)
    created: list[Path] = field(default_factory=list)


def is_within(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


def safe_move(src: Path, dst: Path) -> None:
    """Moves a file without ever replacing anything. Raises FileExistsError if `dst` exists.

    A hard link fails if the name is taken, so it is used where the disk allows it; otherwise the name is checked
    right before a plain rename."""
    options = {"follow_symlinks": False} if os.link in os.supports_follow_symlinks else {}
    try:
        os.link(src, dst, **options)
    except FileExistsError:
        raise
    except OSError as e:
        if e.errno == errno.EXDEV:
            raise OSError(errno.EXDEV, "that folder is on a different disk") from None
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, "exists", str(dst)) from None
        os.rename(src, dst)                                  # a disk without hard links
        return
    try:
        os.unlink(src)
    except OSError:
        os.unlink(dst)                                       # could not remove the original: leave exactly one file
        raise


LOG_PROBLEM = "I can't write my move log, so I stopped"


def _ensure_dir(path: Path, plan: Plan, home: Path, movelog: MoveLog, report: Report) -> str:
    """"" when the folder is ready, otherwise the reason it isn't."""
    if path.is_symlink():
        return "that folder is a shortcut"
    if path.exists():
        return "" if path.is_dir() and is_within(path.resolve(), home) else "that isn't a usable folder"
    if not path.parent.is_dir() or not is_within(path.parent.resolve(), home):
        return "the folder it belongs in isn't available"
    try:
        movelog.write({"event": "mkdir", "batch": plan.id, "path": str(path)})
    except OSError:
        return LOG_PROBLEM
    try:
        os.mkdir(path)
    except OSError as e:
        return f"couldn't create the folder ({e.strerror or 'error'})"
    report.created.append(path)
    return ""


def execute(plan: Plan, movelog: MoveLog, now: datetime, home: Path) -> Report:
    report = Report()
    folder = plan.folder
    try:
        unchanged = folder.resolve(strict=True) == folder and not folder.is_symlink() and is_within(folder, home)
    except OSError:
        unchanged = False
    if not unchanged:
        report.skipped = [(m.name, "the folder changed") for m in plan.moves]
        return report

    stamp = stamp_of(now)
    ready: dict[Path, str] = {}
    present: dict[Path, set[str]] = {}
    stopped = ""
    for move in plan.moves:
        if stopped:
            report.skipped.append((move.name, stopped))
            continue
        dest_dir = move.dest_dir
        if dest_dir not in ready:
            ready[dest_dir] = _ensure_dir(dest_dir, plan, home, movelog, report)
            present[dest_dir] = {fold(e) for e in os.listdir(dest_dir)} if not ready[dest_dir] else set()
        if ready[dest_dir]:
            report.skipped.append((move.name, ready[dest_dir]))
            if ready[dest_dir] == LOG_PROBLEM:
                stopped = LOG_PROBLEM
            continue

        src = folder / move.name
        try:
            info = os.lstat(src)
        except OSError:
            report.skipped.append((move.name, "it is no longer there"))
            continue
        if (not stat.S_ISREG(info.st_mode) or info.st_size != move.size or info.st_mtime_ns != move.mtime_ns
                or info.st_ino != move.inode):
            report.skipped.append((move.name, "it changed since the preview"))
            continue

        final = move.dest_name if fold(move.dest_name) not in present[dest_dir] else unique_name(move.dest_name, present[dest_dir], stamp)
        dst = dest_dir / final
        entry = {"event": "move", "batch": plan.id, "src": str(src), "dst": str(dst)}
        try:
            movelog.write({**entry, "status": "attempt"})
        except OSError:
            stopped = LOG_PROBLEM
            report.skipped.append((move.name, stopped))
            continue
        try:
            safe_move(src, dst)
        except OSError as e:
            reason = "that name is already taken" if isinstance(e, FileExistsError) else (e.strerror or "it could not be moved")
            report.skipped.append((move.name, reason))
            _try_log(movelog, {**entry, "status": "failed", "reason": reason})
            continue
        present[dest_dir].add(fold(final))
        report.moved.append((move, final))
        if not _try_log(movelog, {**entry, "status": "moved"}):
            stopped = LOG_PROBLEM
    return report


def _try_log(movelog: MoveLog, event: dict) -> bool:
    try:
        movelog.write(event)
        return True
    except OSError:
        log.error("could not write to the move log")
        return False
