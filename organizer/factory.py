"""Builds the organizer from config.yaml."""
from pathlib import Path

from organizer.movelog import MoveLog
from organizer.service import OrganizerService


def build_organizer(cfg: dict, root: Path, files=None) -> OrganizerService:
    o = cfg.get("organizer", {})
    return OrganizerService(
        MoveLog(root / "data" / "logs" / "moves.jsonl"),
        files=files,
        folders=o.get("folders", ["~/Desktop", "~/Downloads"]),
        enabled=o.get("enabled", True),
        settle_seconds=o.get("settle_seconds", 60),
        max_files=o.get("max_files", 200),
        trusted_rules=o.get("trusted_rules") or [],
    )
