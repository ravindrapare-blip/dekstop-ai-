"""Sends each action to the part that can do it: Chrome actions to the browser service, everything else to the
desktop service. The pipeline talks to this one object, so the safety flow is the same for both."""
from pathlib import Path

from automation import ActionService, build_actions
from automation.choices import ChoiceStore, Chosen
from automation.results import ActionResult, Prepared
from browserctl import BROWSER_TOOLS, BrowserService
from browserctl.factory import build_browser


class ActionRouter:
    def __init__(self, desktop: ActionService, browser: BrowserService, choices: ChoiceStore) -> None:
        self.desktop = desktop
        self.browser = browser
        self.choices = choices

    def _owner(self, tool: str):
        return self.browser if tool in BROWSER_TOOLS else self.desktop

    def prepare(self, tool: str, args: dict, requires_confirmation: bool = False,
                dry_run: bool | None = None) -> Prepared:
        return self._owner(tool).prepare(tool, args, requires_confirmation, dry_run)

    def execute(self, tool: str, args: dict) -> ActionResult | None:
        return self._owner(tool).execute(tool, args)

    def choose(self, choice_id: str, option_id: str | None) -> Chosen | ActionResult:
        return self.choices.resolve(choice_id, option_id)

    def status(self) -> dict:
        return self.desktop.status() | {"browser": self.browser.status()}

    def close(self) -> None:
        self.browser.close()


def build_router(cfg: dict, root: Path) -> ActionRouter:
    choices = ChoiceStore()
    desktop = build_actions(cfg, root, choices)
    return ActionRouter(desktop, build_browser(cfg, root, choices), choices)
