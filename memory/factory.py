"""Builds the memory service from config.yaml."""
from pathlib import Path

from automation.choices import ChoiceStore
from memory.notify import Notifier
from memory.scheduler import ReminderScheduler
from memory.service import MemoryService
from memory.store import MemoryStore


def build_memory(cfg: dict, root: Path, choices: ChoiceStore | None = None) -> MemoryService:
    m = cfg.get("memory", {})
    store = MemoryStore(root / cfg.get("data", {}).get("database", "data/assistant.db"))
    notifier = Notifier(enabled=m.get("notifications", True))
    history_days = m.get("history_days", 30)
    scheduler = ReminderScheduler(store, notifier, history_days=history_days)
    return MemoryService(
        store, notifier, scheduler, choices=choices,
        enabled=m.get("enabled", True),
        notifications=m.get("notifications", True),
        history=m.get("history", True),
        history_limit=m.get("history_limit", 300),
        history_days=history_days,
        prompt_preferences=m.get("prompt_preferences", 8),
    )
