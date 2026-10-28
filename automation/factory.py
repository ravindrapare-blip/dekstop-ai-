"""Builds the action service from config.yaml."""
from pathlib import Path

from automation.apps import AppCatalog
from automation.choices import ChoiceStore
from automation.controller import AppController
from automation.files import PRIMARY_DIRS, SECONDARY_DIRS, FileFinder
from automation.hammerspoon import HammerspoonClient
from automation.service import ActionService


def build_actions(cfg: dict, root: Path, choices: ChoiceStore | None = None) -> ActionService:
    a = cfg.get("automation", {})
    f = cfg.get("files", {})
    hammerspoon = None
    if a.get("enabled", True) and a.get("hammerspoon", True):
        hammerspoon = HammerspoonClient(
            f"http://{a.get('host', '127.0.0.1')}:{a.get('port', 8767)}",
            root / a.get("token_file", "data/.token"),
        )
    finder = FileFinder(
        f.get("search_dirs", PRIMARY_DIRS),
        f.get("more_dirs", SECONDARY_DIRS),
        max_depth=f.get("max_depth", 3),
        max_entries=f.get("max_entries", 20_000),
        time_budget=f.get("time_budget_seconds", 2.0),
    )
    return ActionService(AppCatalog(), finder, AppController(hammerspoon), choices=choices)
