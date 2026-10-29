"""Chooses the brain for each request: Gemini (cloud) first, the local Ollama model when Gemini can't help.

It looks like the local client to the rest of the code (`chat(messages)`), so the tool router, the validation and the
safety layer work exactly as before whichever brain answered. Gemini only ever proposes a tool call; it can't run anything.

What it does:
  * Gemini off (the default), or no key: every request goes to the local model.
  * Gemini on: try Gemini. If it fails because of quota / rate limit / timeout / network / server trouble / a rejected key or
    model, answer from the local model instead (if USE_LOCAL_FALLBACK allows), and tell the user once.
  * After a cooldown the next request tries Gemini again. If it works, Gemini is used again and the user is told once.
    No background polling: an idle assistant makes no network calls.
  * A problem with one request only (Gemini declined it, or its answer was unreadable) uses the local model for that
    request and starts no cooldown.
Nothing in a log or message contains your key or your words.
"""
import logging
import threading
import time
from datetime import datetime

from backend.gemini import GeminiClient, GeminiError
from backend.llm import ChatResult, OllamaClient, OllamaError, OllamaUnavailable
from backend.settings import Settings

log = logging.getLogger("assistant.brain")

MIN_COOLDOWN = 5.0
MAX_COOLDOWN = 3600.0
BACKOFF_STEPS = 4                       # repeated trouble waits up to this many times longer (5 min, 10, 20, 20, ...)


class BrainUnavailable(OllamaUnavailable):
    """No brain can answer right now. The message says why and what to do."""


def human_duration(seconds: float) -> str:
    seconds = max(1, round(seconds))
    if seconds < 90:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(seconds / 3600)
    return f"{hours} hour{'s' if hours != 1 else ''}"


class Brain:
    def __init__(
        self,
        local: OllamaClient | None,
        gemini: GeminiClient | None = None,
        *,
        gemini_wanted: bool = False,
        use_local_fallback: bool = True,
        cooldown_seconds: float = 300.0,
        auth_cooldown_seconds: float = 1800.0,
        speak_notice: bool = False,
        clock=time.monotonic,
        wall_clock=datetime.now,
    ) -> None:
        self.local = local
        self.gemini = gemini
        self.gemini_wanted = gemini_wanted                    # USE_GEMINI, even if there is no key
        self.use_local_fallback = use_local_fallback
        self.cooldown_seconds = cooldown_seconds
        self.auth_cooldown_seconds = auth_cooldown_seconds
        self.speak_notice = speak_notice
        self._clock = clock
        self._wall = wall_clock
        self._lock = threading.Lock()
        self._cooldown_until = 0.0
        self._failures = 0                                    # trouble in a row, for the growing wait
        self._reason = ""                                     # why Gemini is being skipped: the human sentence
        self._kind = ""
        self._probing = False
        self._told_about_missing_key = False
        self.using = ""                                       # the engine that answered last: "gemini", "ollama" or ""
        self.switches = 0
        self.last_switch = ""                                 # when, as text, for the status page

    # -- the same shape as the local client ----------------------------------------------------------------------

    @property
    def model(self) -> str:
        return self.local.model if self.local is not None else (self.gemini.model if self.gemini is not None else "")

    def close(self) -> None:
        if self.gemini is not None:
            self.gemini.close()

    def chat(self, messages: list[dict], *, json_mode: bool = True) -> ChatResult:
        notice = ""
        if self.gemini is not None:
            if self._begin_attempt():
                try:
                    result = self.gemini.chat(messages, json_mode=json_mode)
                except GeminiError as error:
                    notice = self._gemini_failed(error)
                else:
                    notice = self._gemini_worked()
                    result.notice = notice
                    result.speak_notice = bool(notice) and self.speak_notice
                    return result
                finally:
                    self._end_attempt()
        elif self.gemini_wanted:
            notice = self._missing_key_notice()
        return self._answer_locally(messages, json_mode, notice)

    # -- Gemini's side of the state --------------------------------------------------------------------------------

    def _begin_attempt(self) -> bool:
        """May this request try Gemini? Not while cooling down, and only one request at a time probes a recovering Gemini."""
        with self._lock:
            if self._clock() < self._cooldown_until or self._probing:
                return False
            self._probing = True
            return True

    def _end_attempt(self) -> None:
        with self._lock:
            self._probing = False

    def _cooldown_for(self, error: GeminiError, failures: int) -> float:
        if error.retry_after is not None:
            base = error.retry_after
        elif error.kind in ("auth", "model"):
            base = self.auth_cooldown_seconds                  # waiting a minute won't fix a wrong key
        else:
            base = self.cooldown_seconds * min(2 ** max(0, failures - 1), BACKOFF_STEPS)
        return min(max(base, MIN_COOLDOWN), MAX_COOLDOWN)

    def _gemini_failed(self, error: GeminiError) -> str:
        if not error.is_outage:
            log.warning("gemini: this request used the local model (%s%s)", error.kind, f", {error.status}" if error.status else "")
            return ""
        with self._lock:
            was_available = self._failures == 0                 # false when this is trouble in a row: the user was already told
            self._failures += 1
            wait = self._cooldown_for(error, self._failures)
            self._cooldown_until = self._clock() + wait
            self._reason, self._kind = error.why, error.kind
            if was_available:
                self.switches += 1
                self.last_switch = self._wall().isoformat(timespec="seconds")
        log.warning("gemini: unavailable (%s%s); %s; trying again in %s", error.kind, f", {error.status}" if error.status else "",
                    "using the local model" if self.use_local_fallback else "local fallback is off", human_duration(wait))
        if not was_available or not self.use_local_fallback:
            return ""                                           # already told, or an error is about to be shown instead
        return f"{error.why}, so I switched to the local model. I'll try Gemini again in {human_duration(wait)}."

    def _gemini_worked(self) -> str:
        with self._lock:
            recovered = self._failures > 0
            self._failures = 0
            self._cooldown_until = 0.0
            self._reason = self._kind = ""
            if recovered:
                self.switches += 1
                self.last_switch = self._wall().isoformat(timespec="seconds")
            self.using = "gemini"
        if recovered:
            log.info("gemini: available again; switched back")
        return "Gemini is available again, so I'm using it." if recovered else ""

    def _missing_key_notice(self) -> str:
        with self._lock:
            first = not self._told_about_missing_key
            self._told_about_missing_key = True
        return "Gemini is turned on but there is no API key, so I'm using the local model." if first else ""

    # -- the local side ---------------------------------------------------------------------------------------------

    def _answer_locally(self, messages: list[dict], json_mode: bool, notice: str) -> ChatResult:
        trouble = self._trouble_text()
        if self.local is None:
            raise BrainUnavailable("No language model is set up.")
        if self.gemini_wanted and not self.use_local_fallback:
            reason = trouble or ("there is no Gemini API key" if self.gemini is None else "Gemini is not available")
            raise BrainUnavailable(f"Gemini is unavailable ({reason}) and the local fallback is turned off (USE_LOCAL_FALLBACK=false).")
        try:
            result = self.local.chat(messages, json_mode=json_mode)
        except OllamaError as e:
            if trouble:
                raise type(e)(f"{e} (Gemini is also unavailable: {trouble}.)") from e
            raise
        result.notice = notice
        result.speak_notice = bool(notice) and self.speak_notice
        with self._lock:
            self.using = "ollama"
        return result

    def _trouble_text(self) -> str:
        with self._lock:
            if self._clock() < self._cooldown_until:
                return f"{self._reason} (retrying in {human_duration(self._cooldown_until - self._clock())})"
            return self._reason

    # -- reporting ---------------------------------------------------------------------------------------------------

    @property
    def gemini_usable(self) -> bool:
        """Gemini is on, has a key, and isn't in a cooldown right now."""
        with self._lock:
            return self.gemini is not None and self._clock() >= self._cooldown_until

    def status(self) -> dict:
        """Safe to show anywhere: no key, no prompts."""
        with self._lock:
            now = self._clock()
            cooling = self.gemini is not None and now < self._cooldown_until
            if self.gemini is not None:
                state = "cooling_down" if cooling else "ready"
            else:
                state = "no_key" if self.gemini_wanted else "off"
            return {
                "using": self.using or ("gemini" if self.gemini is not None else "ollama"),
                "gemini": {"state": state, "model": self.gemini.model if self.gemini else "", "reason": self._reason if cooling else "",
                           "kind": self._kind if cooling else "", "retry_in_seconds": round(self._cooldown_until - now) if cooling else 0},
                "local": {"available": self.local is not None, "model": self.local.model if self.local else ""},
                "fallback_enabled": self.use_local_fallback,
                "switches": self.switches,
                "last_switch": self.last_switch,
            }


def build_brain(cfg: dict, settings: Settings, local: OllamaClient | None) -> Brain:
    b = cfg.get("brain", {})
    gemini = None
    if settings.gemini_configured:
        gemini = GeminiClient(
            settings.gemini_api_key, settings.gemini_model,
            timeout=float(b.get("timeout_seconds", 10)),
            send_private_context=bool(b.get("send_memory_to_gemini", False)),
        )
    return Brain(
        local, gemini,
        gemini_wanted=settings.use_gemini,
        use_local_fallback=settings.use_local_fallback,
        cooldown_seconds=float(b.get("cooldown_seconds", 300)),
        auth_cooldown_seconds=float(b.get("auth_cooldown_seconds", 1800)),
        speak_notice=bool(b.get("speak_switch_notice", False)),
    )
