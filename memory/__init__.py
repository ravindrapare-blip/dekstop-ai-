"""What the assistant remembers: reminders, tasks, notes, preferences and a short command history.

One SQLite file on this Mac (data/assistant.db), readable by this user only. Nothing is sent anywhere.
This package never decides what is allowed: `backend.pipeline` asks the safety layer first.
"""
from memory.service import MEMORY_TOOLS, MemoryService

__all__ = ["MEMORY_TOOLS", "MemoryService"]
