"""Risk levels and what each one means."""
from enum import IntEnum


class Risk(IntEnum):
    LOW = 1       # runs automatically (and is logged)
    MEDIUM = 2    # asks the user first: "I can do this. Please confirm."
    HIGH = 3      # blocked by default; only runs after an explicit, acknowledged approval
    CRITICAL = 4  # never runs, no approval can unlock it

    @classmethod
    def from_name(cls, name: str) -> "Risk":
        try:
            return cls[str(name).strip().upper()]
        except KeyError:
            raise ValueError(f"unknown risk level {name!r}") from None

    @property
    def label(self) -> str:
        return self.name.lower()
