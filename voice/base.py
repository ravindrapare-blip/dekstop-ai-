"""Shared pieces for the speech engines: errors, availability and path lookup."""
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


class VoiceError(Exception):
    """A speech problem with a code the HTTP layer and the app can act on.

    codes: disabled, engine_missing, model_missing, failed, timeout, bad_audio, too_large, bad_text
    """

    def __init__(self, code: str, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix

    @property
    def detail(self) -> str:
        return f"{self.message} {self.fix}".strip()


@dataclass(frozen=True)
class Availability:
    ok: bool
    reason: str = ""
    code: str = ""
    fix: str = ""

    def to_dict(self) -> dict:
        return {"available": self.ok, "reason": self.reason, "fix": self.fix}


def sentence(text: str) -> str:
    """Capitalise the first letter only (str.capitalize would lowercase file names) and end with a full stop."""
    text = text[:1].upper() + text[1:]
    return text if text.endswith(".") else text + "."


def resolve_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def find_executable(value: str, root: Path) -> str | None:
    """A command name is searched on PATH; anything with a slash is a path relative to the project."""
    if os.sep in value or value.startswith("."):
        path = resolve_path(value, root)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(value)
