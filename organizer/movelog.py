"""The move log: one line for every folder created and every file moved (or not), written BEFORE each move.

If a line can't be written, the move doesn't happen. `scripts/organize_undo.py` reads this file to put files back.
"""
import json
import threading
from datetime import datetime
from pathlib import Path

MAX_BYTES = 2_000_000       # one backup is kept, so the log never grows without bound


class MoveLog:
    def __init__(self, path: Path | str, max_bytes: int = MAX_BYTES, clock=datetime.now) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.clock = clock
        self._lock = threading.Lock()

    def write(self, event: dict) -> None:
        """Raises OSError if the line can't be saved; the caller must then stop."""
        line = json.dumps({"at": self.clock().isoformat(timespec="seconds"), **event}, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def read(self) -> list[dict]:
        """Every readable line, oldest first (the backup file first)."""
        events = []
        for path in (self.path.with_suffix(self.path.suffix + ".1"), self.path):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        return events
