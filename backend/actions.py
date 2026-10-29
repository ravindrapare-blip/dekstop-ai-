"""Sends each action to the part that can do it: Chrome actions to the browser service, everything else to the
desktop service. The pipeline talks to this one object, so the safety flow is the same for both."""
import logging
from pathlib import Path

from automation import ActionService, build_actions
from automation.choices import ChoiceStore, Chosen
from automation.results import ActionResult, Prepared
from browserctl import BROWSER_TOOLS, BrowserService
from browserctl.factory import build_browser
from memory import MEMORY_TOOLS, MemoryService
from memory.factory import build_memory
from organizer import ORGANIZER_TOOLS, OrganizerService
from organizer.factory import build_organizer

log = logging.getLogger("assistant.backend")


class ActionRouter:
    def __init__(self, desktop: ActionService, browser: BrowserService, choices: ChoiceStore,
                 memory: MemoryService | None = None, organizer: OrganizerService | None = None) -> None:
        self.desktop = desktop
        self.browser = browser
        self.choices = choices
        self.memory = memory
        self.organizer = organizer

    def _owner(self, tool: str):
        if tool in BROWSER_TOOLS:
            return self.browser
        if tool in MEMORY_TOOLS and self.memory is not None:
            return self.memory
        if tool in ORGANIZER_TOOLS and self.organizer is not None:
            return self.organizer
        return self.desktop

    def prepare(self, tool: str, args: dict, requires_confirmation: bool = False,
                dry_run: bool | None = None) -> Prepared:
        return self._owner(tool).prepare(tool, args, requires_confirmation, dry_run)

    def execute(self, tool: str, args: dict) -> ActionResult | None:
        return self._owner(tool).execute(tool, args)

    def choose(self, choice_id: str, option_id: str | None) -> Chosen | ActionResult:
        return self.choices.resolve(choice_id, option_id)

    def status(self) -> dict:
        out = self.desktop.status() | {"browser": self.browser.status()}
        if self.memory is not None:
            out["memory"] = self.memory.status()
        if self.organizer is not None:
            out["organizer"] = self.organizer.status()
        return out

    # memory features the backend uses around each request
    def memory_context(self) -> str:
        return self.memory.context_line() if self.memory is not None else ""

    def record_command(self, text: str, tool: str, status: str) -> None:
        if self.memory is not None:
            self.memory.record_command(text, tool, status)

    def start(self) -> None:
        if self.memory is not None:
            self.memory.start()

    def close(self) -> None:
        """Stops the browser helper and the reminder watcher. One failing never stops the other."""
        for part in (self.browser, self.memory):
            if part is None:
                continue
            try:
                part.close()
            except Exception:
                log.exception("could not stop %s cleanly", type(part).__name__)


def build_router(cfg: dict, root: Path) -> ActionRouter:
    choices = ChoiceStore()
    desktop = build_actions(cfg, root, choices)
    return ActionRouter(desktop, build_browser(cfg, root, choices), choices, build_memory(cfg, root, choices),
                        build_organizer(cfg, root, desktop.files))
