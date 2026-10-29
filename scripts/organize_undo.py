"""Put back the files from the most recent "organize" (or a chosen one), using the move log.

Usage: .venv/bin/python scripts/organize_undo.py              # shows what would be put back and asks first
       .venv/bin/python scripts/organize_undo.py --yes        # no question
       .venv/bin/python scripts/organize_undo.py --list       # the batches that can be undone
       .venv/bin/python scripts/organize_undo.py --batch ID   # a specific one (the plan id in data/logs/moves.jsonl)
Files are only moved back to where they came from, nothing is overwritten, and folders the organizer created are
removed only if they are empty. Exit code 0 on success.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from organizer.movelog import MoveLog  # noqa: E402
from organizer.undo import batches, undo  # noqa: E402


def main() -> int:
    movelog = MoveLog(ROOT / "data" / "logs" / "moves.jsonl")
    events = movelog.read()
    available = batches(events)
    if "--list" in sys.argv:
        for batch in available:
            moved = [e for e in events if e.get("batch") == batch and e.get("event") == "move" and e.get("status") == "moved"]
            print(f"{batch}  {moved[0]['at']}  {len(moved)} files")
        if not available:
            print("Nothing to undo.")
        return 0
    batch = sys.argv[sys.argv.index("--batch") + 1] if "--batch" in sys.argv and sys.argv.index("--batch") + 1 < len(sys.argv) else None
    batch = batch or (available[-1] if available else None)
    if batch is None or batch not in available:
        print("Nothing to undo." if batch is None else f"No undoable batch called {batch}. Use --list.")
        return 0 if batch is None else 1
    moved = [e for e in events if e.get("batch") == batch and e.get("event") == "move" and e.get("status") == "moved"]
    print(f"Batch {batch} moved {len(moved)} files:")
    for e in moved[:15]:
        print(f"  {e['dst']}  ->  back to  {e['src']}")
    if len(moved) > 15:
        print(f"  ... and {len(moved) - 15} more")
    if "--yes" not in sys.argv and input("Put them back? Type 'yes': ").strip().lower() != "yes":
        print("Nothing was changed.")
        return 0
    report = undo(movelog, batch)
    print(f"Put back {len(report.restored)} file{'s' if len(report.restored) != 1 else ''}."
          + (f" Removed {len(report.removed_dirs)} empty folder(s) it had made." if report.removed_dirs else ""))
    for name, why in report.skipped:
        print(f"  left alone: {name} ({why})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
