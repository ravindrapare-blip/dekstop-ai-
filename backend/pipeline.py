"""The one path an action takes:  find the real target  ->  safety layer  ->  run  ->  log.

Nothing is executed unless the safety layer authorized the exact call that is about to run.
"""
from dataclasses import dataclass, field, replace

from automation.service import ActionResult, ActionService
from backend.toolcall import ToolCall
from safety import Decision, GuardrailManager, Outcome as GuardOutcome


@dataclass
class Outcome:
    tool: str
    args: dict
    requires_confirmation: bool = False
    decision: Decision | None = None          # None when nothing was judged yet (a question or a not-found)
    result: ActionResult | None = None


class Pipeline:
    def __init__(self, guard: GuardrailManager, actions: ActionService) -> None:
        self.guard = guard
        self.actions = actions

    def run(self, tool: str, args: dict, requires_confirmation: bool = False, dry_run: bool | None = None) -> Outcome:
        """A call proposed by the model."""
        prepared = self.actions.prepare(tool, args, requires_confirmation, dry_run)
        if prepared.kind != "ready":
            return Outcome(tool, args, requires_confirmation, None, prepared.result)
        confirm = requires_confirmation if prepared.requires_confirmation is None else prepared.requires_confirmation
        outcome = self._judge_and_run(prepared.tool or tool, prepared.args, confirm, dry_run)
        if prepared.preview and outcome.decision is not None:
            outcome.decision = self._with_preview(outcome.decision, prepared.preview)
        return outcome

    @staticmethod
    def _with_preview(decision: Decision, preview: str) -> Decision:
        """When the user is asked (or it is a dry run), the question or description is the preview itself."""
        if decision.outcome is GuardOutcome.CONFIRM:
            return replace(decision, message=f"{preview}\n\nNothing moves until you confirm.")
        if decision.outcome is GuardOutcome.DRY_RUN:
            return replace(decision, message=f"Dry run. {preview}\n\nNothing was moved.")
        return decision

    def choose(self, choice_id: str, option_id: str | None, dry_run: bool | None = None) -> Outcome:
        """The user picked one of several matches (or cancelled)."""
        chosen = self.actions.choose(choice_id, option_id)
        if isinstance(chosen, ActionResult):
            return Outcome("", {}, False, None, chosen)
        dry = dry_run if dry_run is not None else chosen.dry_run     # a dry run stays a dry run
        return self._judge_and_run(chosen.tool, chosen.args, chosen.requires_confirmation, dry)

    def after_confirmation(self, decision: Decision) -> ActionResult | None:
        """The user approved a pending request and the safety layer authorized it."""
        return self._execute(decision)

    def _judge_and_run(self, tool: str, args: dict, requires_confirmation: bool, dry_run: bool | None) -> Outcome:
        decision = self.guard.evaluate(ToolCall(tool, args, requires_confirmation), dry_run=dry_run)
        return Outcome(tool, args, requires_confirmation, decision, self._execute(decision))

    def _execute(self, decision: Decision) -> ActionResult | None:
        if not decision.authorized:
            return None
        result = self.actions.execute(decision.tool, decision.args)
        if result is not None:
            self.guard.record_execution(decision.tool, decision.args, result.status, result.via)
        return result
