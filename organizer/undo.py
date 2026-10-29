"""Puts files back, using the move log. Used by `scripts/organize_undo.py`."""
from dataclasses import dataclass, field
from pathlib import Path

from organizer.mover import safe_move
from organizer.movelog import MoveLog


@dataclass
class UndoReport:
    batch: str = ""
    restored: list[tuple[str, str]] = field(default_factory=list)      # (from, back to)
    skipped: list[tuple[str, str]] = field(default_factory=list)       # (file, why)
    removed_dirs: list[str] = field(default_factory=list)


def batches(events: list[dict]) -> list[str]:
    """Batches that moved something and haven't been undone, oldest first."""
    done = {e["batch"] for e in events if e.get("event") == "undo_done"}
    order: list[str] = []
    for e in events:
        if e.get("event") == "move" and e.get("status") == "moved" and e["batch"] not in done and e["batch"] not in order:
            order.append(e["batch"])
    return order


def undo(movelog: MoveLog, batch: str | None = None) -> UndoReport:
    events = movelog.read()
    available = batches(events)
    report = UndoReport(batch or (available[-1] if available else ""))
    if not report.batch:
        return report
    moves = [e for e in events if e.get("batch") == report.batch and e.get("event") == "move" and e.get("status") == "moved"]
    for e in reversed(moves):
        src, dst = Path(e["src"]), Path(e["dst"])                       # it went from src to dst; now dst goes back to src
        if not dst.exists() or dst.is_symlink():
            report.skipped.append((dst.name, "it is no longer there"))
        elif src.exists() or src.is_symlink():
            report.skipped.append((dst.name, "something with its old name is already there"))
        else:
            try:
                safe_move(dst, src)
            except OSError as ex:
                report.skipped.append((dst.name, ex.strerror or "it could not be moved"))
                continue
            report.restored.append((str(dst), str(src)))
            movelog.write({"event": "undo", "batch": report.batch, "src": str(dst), "dst": str(src), "status": "restored"})
    for e in reversed([e for e in events if e.get("batch") == report.batch and e.get("event") == "mkdir"]):
        folder = Path(e["path"])
        try:
            if folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
                folder.rmdir()                                          # only folders this batch made, and only if empty
                report.removed_dirs.append(str(folder))
        except OSError:
            continue
    movelog.write({"event": "undo_done", "batch": report.batch})
    return report
