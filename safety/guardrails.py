"""GuardrailManager: decides whether a tool call may run. It never runs anything itself.

    evaluate(call)  -> ALLOW | CONFIRM | BLOCK | DRY_RUN
    confirm(id, ok) -> ALLOW (exactly the call that was asked about) | BLOCK
"""
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from safety.audit import AuditLog, summarize_args
from safety.checks import check_args
from safety.policy import Policy
from safety.risk import Risk

log = logging.getLogger("assistant.safety")


class Call(Protocol):
    tool: str
    args: dict
    requires_confirmation: bool


class Outcome(str, Enum):
    ALLOW = "allow"        # the executor may run this call now
    CONFIRM = "confirm"    # ask the user; nothing runs until confirm() approves it
    BLOCK = "block"        # refused
    DRY_RUN = "dry_run"    # describe only; nothing runs


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    risk: Risk
    reason: str
    message: str
    tool: str
    args: dict
    confirmation_id: str | None = None
    requires_acknowledgment: bool = False  # high-risk: the user must explicitly accept the risk
    approved: bool = False                 # ALLOW that came from a user's confirmation

    @property
    def authorized(self) -> bool:
        return self.outcome is Outcome.ALLOW

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "authorized": self.authorized,
            "risk": self.risk.label,
            "reason": self.reason,
            "message": self.message,
            "tool": self.tool,
            "args": self.args,
            "confirmation_id": self.confirmation_id,
            "requires_acknowledgment": self.requires_acknowledgment,
        }


@dataclass
class _Pending:
    call: Call
    expires_at: float


@dataclass(frozen=True)
class _Assessment:
    risk: Risk
    reason: str


def describe(call: Call) -> str:
    words = call.tool.replace("_", " ")
    detail = ", ".join(f"{k}: {v}" for k, v in summarize_args(call.args).items())
    return f"{words} ({detail})" if detail else words


class GuardrailManager:
    def __init__(
        self,
        policy: Policy,
        audit: AuditLog | None = None,
        *,
        timeout_seconds: float = 60,
        max_pending: int = 5,
        dry_run: bool = False,
        clock=time.monotonic,
    ) -> None:
        self.policy = policy
        self.audit = audit
        self.timeout = timeout_seconds
        self.max_pending = max(1, max_pending)
        self.dry_run = dry_run
        self._clock = clock
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    # -- assessment: pure, no side effects -----------------------------------

    def assess(self, call: Call) -> _Assessment:
        if call.tool in self.policy.blocked:
            b = self.policy.blocked[call.tool]
            return _Assessment(b.risk, b.reason)
        base = self.policy.rules.get(call.tool)
        if base is None:
            return _Assessment(Risk.CRITICAL, f"{call.tool!r} is not an allowed action")
        finding = check_args(call.tool, call.args, self.policy)
        if finding and finding.risk > base:
            return _Assessment(finding.risk, finding.reason)
        if base is Risk.LOW and call.requires_confirmation:
            return _Assessment(Risk.MEDIUM, "this action asks for confirmation")
        return _Assessment(base, f"{call.tool} is a {base.label}-risk action")

    # -- public API -----------------------------------------------------------

    def evaluate(self, call: Call, *, dry_run: bool | None = None) -> Decision:
        a = self.assess(call)
        dry = self.dry_run if dry_run is None else dry_run

        if a.risk is Risk.CRITICAL:
            return self._finish(self._decision(Outcome.BLOCK, a, call, f"I can't do that: {a.reason}."), "evaluate")
        if dry:
            return self._finish(self._decision(Outcome.DRY_RUN, a, call, self._dry_run_message(a, call)), "evaluate")
        if a.risk is Risk.LOW:
            return self._finish(self._decision(Outcome.ALLOW, a, call, ""), "evaluate")

        high = a.risk is Risk.HIGH
        if high:
            message = (f"This is a high-risk action ({a.reason}) and is blocked by default: "
                       f"{describe(call)}. Please confirm explicitly to proceed.")
        else:
            message = f"I can do this: {describe(call)}. Please confirm."
        confirmation_id = self._add_pending(call)
        decision = self._decision(Outcome.CONFIRM, a, call, message,
                                  confirmation_id=confirmation_id, requires_acknowledgment=high)
        return self._finish(decision, "evaluate", on_audit_failure=lambda: self._drop(confirmation_id))

    def confirm(self, confirmation_id: str, approved: bool, *, acknowledge_risk: bool = False) -> Decision:
        with self._lock:
            self._purge()
            pending = self._pending.get(confirmation_id)
        if pending is None:
            return self._finish(self._unknown_confirmation(confirmation_id), "confirm")

        call = pending.call
        a = self.assess(call)  # re-check: the world may have changed since we asked
        if not approved:
            self._drop(confirmation_id)
            return self._finish(self._decision(Outcome.BLOCK, a, call, "Cancelled. I did not do it."), "confirm")
        if a.risk is Risk.CRITICAL:
            self._drop(confirmation_id)
            return self._finish(self._decision(Outcome.BLOCK, a, call, f"I can't do that: {a.reason}."), "confirm")
        if a.risk is Risk.HIGH and not acknowledge_risk:
            return self._finish(self._decision(
                Outcome.BLOCK, a, call,
                "This is a high-risk action. It stays blocked unless you explicitly acknowledge the risk.",
                confirmation_id=confirmation_id, requires_acknowledgment=True), "confirm")

        self._drop(confirmation_id)
        decision = self._decision(Outcome.ALLOW, a, call, "", approved=True)
        return self._finish(decision, "confirm")

    def record_execution(self, tool: str, args: dict, status: str, via: str = "") -> None:
        """Log what happened after an authorized action ran. A logging problem never undoes the action."""
        if self.audit is None:
            return
        try:
            self.audit.write({"stage": "execute", "tool": tool, "args": summarize_args(args),
                              "status": status, "via": via})
        except OSError as e:
            log.error("could not log the result of %s: %s", tool, e)

    def pending_count(self) -> int:
        with self._lock:
            self._purge()
            return len(self._pending)

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _decision(outcome: Outcome, a: _Assessment, call: Call, message: str, **extra) -> Decision:
        return Decision(outcome, a.risk, a.reason, message, call.tool, dict(call.args), **extra)

    @staticmethod
    def _dry_run_message(a: _Assessment, call: Call) -> str:
        note = {
            Risk.MEDIUM: " It would ask for your confirmation first.",
            Risk.HIGH: " It would be blocked unless you explicitly confirmed it.",
        }.get(a.risk, "")
        return f"Dry run: I would {describe(call)}.{note} Nothing was done."

    def _unknown_confirmation(self, confirmation_id: str) -> Decision:
        a = _Assessment(Risk.CRITICAL, "no pending action with that id (it may have expired)")
        return Decision(Outcome.BLOCK, a.risk, a.reason, "I have nothing waiting for that confirmation.",
                        "", {}, confirmation_id=confirmation_id)

    def _add_pending(self, call: Call) -> str:
        with self._lock:
            self._purge()
            while len(self._pending) >= self.max_pending:
                self._pending.pop(next(iter(self._pending)))  # drop the oldest
            cid = uuid.uuid4().hex[:12]
            self._pending[cid] = _Pending(call, self._clock() + self.timeout)
            return cid

    def _drop(self, confirmation_id: str | None) -> None:
        with self._lock:
            self._pending.pop(confirmation_id, None)

    def _purge(self) -> None:  # caller holds the lock
        now = self._clock()
        for cid in [c for c, p in self._pending.items() if p.expires_at <= now]:
            del self._pending[cid]

    def _finish(self, d: Decision, stage: str, on_audit_failure=None) -> Decision:
        """Log the decision BEFORE it is returned. If it cannot be logged, nothing is authorized."""
        event = {
            "stage": stage, "outcome": d.outcome.value, "risk": d.risk.label, "tool": d.tool,
            "args": summarize_args(d.args), "reason": d.reason, "approved": d.approved,
            "confirmation_id": d.confirmation_id,
        }
        log.info("%s %s tool=%s risk=%s reason=%s", stage, d.outcome.value, d.tool, d.risk.label, d.reason)
        if self.audit is None:
            return d
        try:
            self.audit.write(event)
        except OSError as e:
            log.error("audit log unavailable, refusing action: %s", e)
            if on_audit_failure:
                on_audit_failure()
            return Decision(Outcome.BLOCK, d.risk, "the audit log is unavailable",
                            "I can't do that safely right now because I can't write my activity log.",
                            d.tool, d.args)
        return d
