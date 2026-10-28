""""Which one did you mean?" questions that are waiting for an answer.

One store is shared by every kind of action, so the app answers all of them through the same /choose.
An answer is only a pick among the options that were offered; the picked call is still judged by the safety layer.
"""
import secrets
import threading
import time
from dataclasses import dataclass

from automation.results import ActionResult, Prepared

CHOICE_TTL_SECONDS = 120
MAX_PENDING_CHOICES = 5


@dataclass
class Chosen:
    tool: str
    args: dict
    requires_confirmation: bool
    dry_run: bool | None = None      # a dry run stays a dry run after the user picks


@dataclass
class _Choice:
    tool: str
    targets: dict                    # option id -> the complete arguments of the call to make
    requires_confirmation: bool
    expires_at: float
    dry_run: bool | None = None


class ChoiceStore:
    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._choices: dict[str, _Choice] = {}
        self._lock = threading.Lock()

    def ask(self, tool: str, targets: dict[str, dict], shown: list[dict], confirm: bool,
            dry_run: bool | None, prompt: str) -> Prepared:
        choice_id = secrets.token_hex(6)
        with self._lock:
            self._purge()
            while len(self._choices) >= MAX_PENDING_CHOICES:
                self._choices.pop(next(iter(self._choices)))
            self._choices[choice_id] = _Choice(tool, targets, confirm, self._clock() + CHOICE_TTL_SECONDS, dry_run)
        choice = {"choice_id": choice_id, "prompt": prompt, "options": shown}
        return Prepared("choose", result=ActionResult("choose", prompt, choice=choice))

    def resolve(self, choice_id: str, option_id: str | None) -> Chosen | ActionResult:
        """Passing no option cancels. A choice can be used once, even if the answer was invalid."""
        with self._lock:
            self._purge()
            pending = self._choices.pop(choice_id, None)
        if pending is None:
            return ActionResult("failed", "That choice has expired. Please ask again.")
        if option_id is None:
            return ActionResult("failed", "Cancelled.")
        args = pending.targets.get(option_id)
        if args is None:
            return ActionResult("failed", "That wasn't one of the options.")
        return Chosen(pending.tool, dict(args), pending.requires_confirmation, pending.dry_run)

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._choices)

    def _purge(self) -> None:    # caller holds the lock
        now = self._clock()
        for cid in [c for c, p in self._choices.items() if p.expires_at <= now]:
            del self._choices[cid]
