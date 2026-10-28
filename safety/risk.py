"""Risk tiers. The classifier and approval flow arrive in phase 2."""
from enum import IntEnum


class Risk(IntEnum):
    READ_ONLY = 0   # runs automatically
    REVERSIBLE = 1  # runs automatically, always logged
    SENSITIVE = 2   # needs user approval every time
    FORBIDDEN = 3   # never offered, always refused
