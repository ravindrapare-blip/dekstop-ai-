"""Look at, test and clear what the assistant remembers. Everything is in one file on this Mac.

Usage: .venv/bin/python scripts/memory_check.py                      # where it is, who can read it, how much is stored
       .venv/bin/python scripts/memory_check.py --show history        # or: preferences, reminders, tasks, notes
       .venv/bin/python scripts/memory_check.py --notify              # send a test notification
       .venv/bin/python scripts/memory_check.py --clear history       # or: preferences, reminders, tasks, notes, all (asks first; --yes skips the question)
Exit code 0 if the memory file can be used.
"""
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import load_config  # noqa: E402
from memory.db import MemoryError_  # noqa: E402
from memory.notify import Notifier  # noqa: E402
from memory.store import MemoryStore  # noqa: E402

TABLES = {"history": "command_history", "preferences": "preferences", "reminders": "reminders", "tasks": "tasks", "notes": "notes"}


def arg_after(flag: str) -> str | None:
    args = sys.argv
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


def show(store: MemoryStore, what: str) -> None:
    if what == "history":
        for h in reversed(store.history(50)):
            print(f"{h['at']}  {h['tool']:<18} {h['status']:<10} {h['text']}")
    elif what == "preferences":
        for key, value in store.preferences():
            print(f"{key}: {value}")
    elif what == "reminders":
        for r in store.reminders():
            print(f"#{r.id}  {r.due_at:%Y-%m-%d %H:%M}  {r.status:<7} {r.text}")
    elif what == "tasks":
        for t in store.tasks(include_done=True):
            print(f"#{t.id}  {t.status:<5} due {t.due_date or '-':<10} {t.title}")
    elif what == "notes":
        for n in store.notes():
            print(f"#{n.id}  {n.title}  (updated {n.updated_at[:16]})")
    else:
        print(f"Unknown thing to show: {what}. Choose from: {', '.join(TABLES)}.")
        sys.exit(2)


def main() -> int:
    cfg = load_config()
    m = cfg.get("memory", {})
    path = ROOT / cfg.get("data", {}).get("database", "data/assistant.db")
    store = MemoryStore(path)
    try:
        counts = store.counts()
    except MemoryError_ as e:
        print(f"[FAIL] {e}")
        return 1

    mode = stat.S_IMODE(path.stat().st_mode)
    print(f"[ok]   memory file: {path}")
    print(f"[{'ok' if mode & 0o077 == 0 else 'WARN'}]   permissions {oct(mode)}" + ("" if mode & 0o077 == 0 else " (others can read it; run: chmod 600 on the file)"))
    print("[ok]   stored: " + ", ".join(f"{n} {t.replace('command_history', 'history entries')}" for t, n in counts.items()))
    print(f"[info] reminder notifications: {'on' if m.get('notifications', True) else 'off'};"
          f" command history: {'on' if m.get('history', True) else 'off'} (keeps {m.get('history_limit', 300)}, {m.get('history_days', 30)} days)")

    what = arg_after("--show")
    if what:
        print()
        show(store, what)
    if "--notify" in sys.argv:
        ok = Notifier().send("Assistant", "This is a test notification.")
        print("[ok]   notification sent" if ok else
              "[FAIL] macOS did not accept the notification. Allow notifications for Script Editor / osascript in System Settings > Notifications.")
        return 0 if ok else 1
    target = arg_after("--clear")
    if target:
        tables = list(TABLES.values()) if target == "all" else [TABLES.get(target)]
        if None in tables:
            print(f"Unknown thing to clear: {target}. Choose from: {', '.join(TABLES)}, all.")
            return 2
        if "--yes" not in sys.argv and input(f"Delete {target} permanently? Type 'yes': ").strip().lower() != "yes":
            print("Nothing was deleted.")
            return 0
        print("Deleted " + ", ".join(f"{store.clear(t)} from {t}" for t in tables) + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
