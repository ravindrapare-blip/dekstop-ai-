"""The shared secret between the backend and Hammerspoon. Only this user can read it."""
import os
import secrets
from pathlib import Path

MIN_LENGTH = 16


def read_token(path: Path) -> str | None:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return text if len(text) >= MIN_LENGTH else None


def ensure_token(path: Path) -> str:
    """Return the token, creating it (readable by this user only) if it does not exist yet."""
    existing = read_token(path)
    if existing:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    os.chmod(path, 0o600)
    return token
