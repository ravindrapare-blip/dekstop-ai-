"""Append-only audit log (JSON lines). Written BEFORE an action is authorized."""
import json
import threading
from datetime import datetime
from pathlib import Path

MAX_BYTES = 2_000_000   # one backup is kept, so the log never grows without bound
MAX_VALUE_CHARS = 80    # long values (note text, etc.) are cut so the log stays small and private


def summarize_args(args: dict) -> dict:
    out = {}
    for key, value in args.items():
        text = value if isinstance(value, str) else repr(value)
        out[key] = text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "..."
    return out


class AuditLog:
    def __init__(self, path: Path | str, max_bytes: int = MAX_BYTES) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def write(self, event: dict) -> None:
        """Raises OSError if the entry cannot be saved; callers must then refuse the action."""
        line = json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **event}, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
