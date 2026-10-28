"""All reads and writes of the assistant's memory. Plain SQL, no models, no embeddings: it stays small and fast.

Everything has a size limit, and old things are pruned, so the file cannot grow without bound.
Callers pass the current time, which keeps this deterministic to test.
"""
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from memory.db import Database

LIMITS = {
    "reminders": 200,        # waiting reminders
    "tasks": 500,            # open tasks
    "notes": 500,
    "preferences": 100,
}
MAX_NOTE_CHARS = 5000
MAX_DETAILS_CHARS = 1000
MAX_TEXT_CHARS = 200
KEEP_FIRED_DAYS = 7          # a reminder that already went off is kept this long, then dropped
KEEP_DONE_DAYS = 90          # finished tasks too
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")


class LimitReached(Exception):
    """A size limit was hit. The message says what to do."""


def _stamp(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat(timespec="seconds")


def normalize_key(text: str) -> str | None:
    """"Preferred Browser" -> "preferred_browser"; None if it can't be a key."""
    key = re.sub(r"[\s]+", "_", str(text).strip().lower())
    return key if KEY_PATTERN.match(key) else None


@dataclass(frozen=True)
class Reminder:
    id: int
    text: str
    due_at: datetime
    status: str


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    details: str
    due_date: str | None
    status: str


@dataclass(frozen=True)
class Note:
    id: int
    title: str
    content: str
    updated_at: str


def _reminder(row) -> Reminder:
    return Reminder(row["id"], row["text"], datetime.fromisoformat(row["due_at"]), row["status"])


def _task(row) -> Task:
    return Task(row["id"], row["title"], row["details"], row["due_date"], row["status"])


def _note(row) -> Note:
    return Note(row["id"], row["title"], row["content"], row["updated_at"])


class MemoryStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._db: Database | None = None
        self._lock = threading.Lock()

    @property
    def db(self) -> Database:
        """Opened on first use, so building the assistant creates no file and a damaged file can't stop it starting."""
        with self._lock:
            if self._db is None:
                self._db = Database(self.path)
            return self._db

    def _count(self, conn, sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    # ---- reminders -----------------------------------------------------------------------------------------

    def add_reminder(self, text: str, due_at: datetime, now: datetime) -> int:
        with self.db.connect() as conn:
            if self._count(conn, "SELECT COUNT(*) FROM reminders WHERE status = 'pending'") >= LIMITS["reminders"]:
                raise LimitReached(f"You already have {LIMITS['reminders']} reminders waiting. Delete some first.")
            cur = conn.execute("INSERT INTO reminders (text, due_at, created_at) VALUES (?, ?, ?)",
                               (text, _stamp(due_at), _stamp(now)))
            return cur.lastrowid

    def reminders(self) -> list[Reminder]:
        """Waiting reminders by time, then the ones that already went off (newest first)."""
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM reminders ORDER BY status = 'fired', "
                                "CASE status WHEN 'pending' THEN due_at END ASC, due_at DESC, id").fetchall()
        return [_reminder(r) for r in rows]

    def get_reminder(self, reminder_id: int) -> Reminder | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return _reminder(row) if row else None

    def delete_reminder(self, reminder_id: int) -> bool:
        with self.db.connect() as conn:
            return conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,)).rowcount > 0

    def due_reminders(self, now: datetime) -> list[Reminder]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM reminders WHERE status = 'pending' AND due_at <= ? ORDER BY due_at, id",
                                (_stamp(now),)).fetchall()
        return [_reminder(r) for r in rows]

    def next_due(self) -> datetime | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT MIN(due_at) FROM reminders WHERE status = 'pending'").fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def mark_fired(self, ids: list[int], now: datetime) -> None:
        with self.db.connect() as conn:
            conn.executemany("UPDATE reminders SET status = 'fired', fired_at = ? WHERE id = ? AND status = 'pending'",
                             [(_stamp(now), i) for i in ids])

    # ---- tasks ---------------------------------------------------------------------------------------------

    def add_task(self, title: str, details: str, due_date: str | None, now: datetime) -> int:
        with self.db.connect() as conn:
            if self._count(conn, "SELECT COUNT(*) FROM tasks WHERE status = 'open'") >= LIMITS["tasks"]:
                raise LimitReached(f"You already have {LIMITS['tasks']} open tasks. Finish some first.")
            cur = conn.execute("INSERT INTO tasks (title, details, due_date, created_at) VALUES (?, ?, ?, ?)",
                               (title, details, due_date, _stamp(now)))
            return cur.lastrowid

    def tasks(self, *, include_done: bool = False) -> list[Task]:
        """Open tasks, soonest due first (no date last); finished ones after them if asked for."""
        where = "" if include_done else "WHERE status = 'open'"
        with self.db.connect() as conn:
            rows = conn.execute(f"SELECT * FROM tasks {where} ORDER BY status = 'done', due_date IS NULL, due_date, id").fetchall()
        return [_task(r) for r in rows]

    def get_task(self, task_id: int) -> Task | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(row) if row else None

    def complete_task(self, task_id: int, now: datetime) -> bool:
        """True if it was open and is now done; False if it doesn't exist or was already done."""
        with self.db.connect() as conn:
            return conn.execute("UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ? AND status = 'open'",
                                (_stamp(now), task_id)).rowcount > 0

    # ---- notes ---------------------------------------------------------------------------------------------

    def save_note(self, title: str, content: str, now: datetime) -> tuple[int, bool]:
        """Saves a note. A note with the same title gets the new text added after the old (nothing is overwritten).
        Returns (id, created)."""
        with self.db.connect() as conn:
            row = conn.execute("SELECT id, content FROM notes WHERE title = ? COLLATE NOCASE ORDER BY id LIMIT 1", (title,)).fetchone()
            if row:
                combined = f"{row['content']}\n\n{content}"
                if len(combined) > MAX_NOTE_CHARS:
                    raise LimitReached(f"That note would be longer than {MAX_NOTE_CHARS} characters. Start a new note instead.")
                conn.execute("UPDATE notes SET content = ?, updated_at = ? WHERE id = ?", (combined, _stamp(now), row["id"]))
                return row["id"], False
            if self._count(conn, "SELECT COUNT(*) FROM notes") >= LIMITS["notes"]:
                raise LimitReached(f"You already have {LIMITS['notes']} notes. Delete some first.")
            cur = conn.execute("INSERT INTO notes (title, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
                               (title, content, _stamp(now), _stamp(now)))
            return cur.lastrowid, True

    def notes(self) -> list[Note]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM notes ORDER BY updated_at DESC, id DESC").fetchall()
        return [_note(r) for r in rows]

    def get_note(self, note_id: int) -> Note | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _note(row) if row else None

    # ---- preferences ---------------------------------------------------------------------------------------

    def set_preference(self, key: str, value: str, now: datetime) -> str | None:
        """Remembers key=value. Returns the previous value, if there was one."""
        with self.db.connect() as conn:
            row = conn.execute("SELECT value FROM preferences WHERE key = ?", (key,)).fetchone()
            if row is None and self._count(conn, "SELECT COUNT(*) FROM preferences") >= LIMITS["preferences"]:
                raise LimitReached(f"I already remember {LIMITS['preferences']} preferences. Forget some first.")
            conn.execute("INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                         (key, value, _stamp(now)))
            return row["value"] if row else None

    def get_preference(self, key: str) -> str | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT value FROM preferences WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def preferences(self, limit: int = LIMITS["preferences"]) -> list[tuple[str, str]]:
        """Most recently changed first."""
        with self.db.connect() as conn:
            rows = conn.execute("SELECT key, value FROM preferences ORDER BY updated_at DESC, key LIMIT ?", (limit,)).fetchall()
        return [(r["key"], r["value"]) for r in rows]

    def forget_preference(self, key: str) -> bool:
        with self.db.connect() as conn:
            return conn.execute("DELETE FROM preferences WHERE key = ?", (key,)).rowcount > 0

    # ---- command history -----------------------------------------------------------------------------------

    def record_command(self, text: str, tool: str, status: str, now: datetime, *, keep: int = 300) -> None:
        with self.db.connect() as conn:
            conn.execute("INSERT INTO command_history (at, text, tool, status) VALUES (?, ?, ?, ?)",
                         (_stamp(now), text[:MAX_TEXT_CHARS], tool, status))
            conn.execute("DELETE FROM command_history WHERE id <= (SELECT MAX(id) FROM command_history) - ?", (max(1, keep),))

    def history(self, limit: int = 20) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT at, text, tool, status FROM command_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---- housekeeping --------------------------------------------------------------------------------------

    def prune(self, now: datetime, *, history_days: int = 30) -> dict[str, int]:
        """Drops what is no longer useful. Returns how many of each were removed."""
        with self.db.connect() as conn:
            gone = {
                "reminders": conn.execute("DELETE FROM reminders WHERE status = 'fired' AND fired_at < ?",
                                          (_stamp(now - timedelta(days=KEEP_FIRED_DAYS)),)).rowcount,
                "tasks": conn.execute("DELETE FROM tasks WHERE status = 'done' AND completed_at < ?",
                                      (_stamp(now - timedelta(days=KEEP_DONE_DAYS)),)).rowcount,
                "history": conn.execute("DELETE FROM command_history WHERE at < ?",
                                        (_stamp(now - timedelta(days=history_days)),)).rowcount,
            }
        return gone

    def counts(self) -> dict[str, int]:
        tables = ("reminders", "tasks", "notes", "preferences", "command_history")
        with self.db.connect() as conn:
            return {t: self._count(conn, f"SELECT COUNT(*) FROM {t}") for t in tables}

    def clear(self, table: str) -> int:
        """Deletes everything in one table. Used by `scripts/memory_check.py --clear`."""
        if table not in ("reminders", "tasks", "notes", "preferences", "command_history"):
            raise ValueError(f"unknown table {table!r}")
        with self.db.connect() as conn:
            return conn.execute(f"DELETE FROM {table}").rowcount
