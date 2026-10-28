"""Fires reminders when they are due. One small thread that sleeps until the next reminder (never polling faster
than it needs to), so it costs next to nothing while idle."""
import logging
import threading
from datetime import datetime, timedelta

from memory.notify import Notifier
from memory.store import MemoryStore

log = logging.getLogger("assistant.reminders")

MISSED_AFTER = timedelta(minutes=2)       # a reminder this late is announced as missed (the Mac was asleep or off)
BATCH_OVER = 3                            # more than this many at once become one notification
MAX_ATTEMPTS = 3                          # tries per reminder if macOS refuses the notification
MAINTENANCE_EVERY = timedelta(hours=6)


class ReminderScheduler:
    def __init__(self, store: MemoryStore, notifier: Notifier, *, clock=datetime.now, max_wait: float = 30.0,
                 min_wait: float = 5.0, history_days: int = 30) -> None:
        self.store = store
        self.notifier = notifier
        self.clock = clock
        self.max_wait = max_wait
        self.min_wait = min_wait
        self.history_days = history_days
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._attempts: dict[int, int] = {}
        self._last_maintenance: datetime | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="reminders", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def wake(self) -> None:
        """A reminder was added or removed: look at the schedule again."""
        self._wake.set()

    def check(self, now: datetime | None = None) -> int:
        """Announces every reminder that is due. Returns how many were handled."""
        now = now or self.clock()
        due = self.store.due_reminders(now)
        if not due:
            return 0
        handled = []
        if len(due) > BATCH_OVER:
            ok = self.notifier.send(f"{len(due)} reminders", "; ".join(r.text for r in due[:BATCH_OVER]) + "; and more")
            handled = list(due) if self._settled(due, ok) else []
        else:
            for r in due:
                late = now - r.due_at > MISSED_AFTER
                ok = self.notifier.send("Missed reminder" if late else "Reminder", r.text)
                if self._settled([r], ok):
                    handled.append(r)
        self.store.mark_fired([r.id for r in handled], now)
        for r in handled:
            self._attempts.pop(r.id, None)
        return len(handled)

    def _settled(self, reminders: list, ok: bool) -> bool:
        """Done with these if the notification went out, or if it has failed too many times to keep trying."""
        if ok:
            return True
        for r in reminders:
            self._attempts[r.id] = self._attempts.get(r.id, 0) + 1
        return all(self._attempts[r.id] >= MAX_ATTEMPTS for r in reminders)

    def maintain(self, now: datetime | None = None) -> None:
        now = now or self.clock()
        removed = self.store.prune(now, history_days=self.history_days)
        if any(removed.values()):
            log.info("pruned old memory: %s", removed)
        self._last_maintenance = now

    def _seconds_until_next(self, now: datetime) -> float:
        nxt = self.store.next_due()
        if nxt is None:
            return self.max_wait
        return min(self.max_wait, max(self.min_wait, (nxt - now).total_seconds()))   # not faster than min_wait, even if a notification keeps failing

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                now = self.clock()
                if self._last_maintenance is None or now - self._last_maintenance > MAINTENANCE_EVERY:
                    self.maintain(now)
                self.check(now)
                wait = self._seconds_until_next(self.clock())
            except Exception:                              # a problem must never stop reminders for good
                log.exception("reminder check failed")
                wait = self.max_wait
            self._wake.wait(wait)
            self._wake.clear()
