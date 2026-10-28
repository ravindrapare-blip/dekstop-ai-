"""The SQLite file. Created readable by this user only; a new connection per use keeps threads simple."""
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")
VERSION = 1


class MemoryError_(Exception):
    """The memory file can't be used. The message is fit to show the user."""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._prepare_file()
        self._migrate()

    def _prepare_file(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.path.exists():
                os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600))     # private from the first byte
            os.chmod(self.path, 0o600)
        except OSError as e:
            raise MemoryError_(f"I can't create my memory file ({e.strerror or 'error'}).") from None

    @contextmanager
    def connect(self):
        """A connection that commits on success and rolls back on any error."""
        try:
            conn = sqlite3.connect(self.path, timeout=5)
        except sqlite3.Error:
            raise MemoryError_("I can't open my memory file.") from None
        conn.row_factory = sqlite3.Row
        try:
            with closing(conn):
                with conn:
                    yield conn
        except sqlite3.IntegrityError:
            raise                                                   # a bug in the caller, not a problem with the file
        except sqlite3.OperationalError as e:
            raise MemoryError_("I couldn't use my memory file just now (it may be busy, or the disk full). "
                               "Nothing was changed; try again.") from e
        except sqlite3.DatabaseError as e:
            raise MemoryError_("My memory file couldn't be read. It may be damaged; "
                               "`scripts/memory_check.py` can show what's wrong.") from e

    def _migrate(self) -> None:
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > VERSION:
                raise MemoryError_("My memory file was made by a newer version of the assistant.")
            if version < VERSION:
                conn.executescript(SCHEMA.read_text() + f"\nPRAGMA user_version = {VERSION};")
