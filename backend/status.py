"""One honest picture of what works right now: every part of the assistant as a traffic light.

    ok    works
    warn  works through a fallback, or an optional part is missing (typing instead of talking, AppleScript instead of Hammerspoon)
    off   turned off in config.yaml
    fail  broken; the detail says why and `fix` says what to do

Only the safety rules and the language model are *core*: without them nothing can run, so the assistant is "down".
Anything else that fails leaves it "degraded": the rest keeps working. This module only looks; it never starts or changes anything.
"""
import logging
import os
import shutil
from dataclasses import dataclass

from backend.startup import CheckResult

log = logging.getLogger("assistant.status")

OK, WARN, OFF, FAIL = "ok", "warn", "off", "fail"
OSASCRIPT = "/usr/bin/osascript"


@dataclass
class Component:
    name: str
    state: str
    detail: str
    fix: str = ""
    core: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "state": self.state, "detail": self.detail, "fix": self.fix, "core": self.core}


def overall(components: list[Component]) -> str:
    """"down" if a core part is broken, "degraded" if anything else needs attention, otherwise "ready"."""
    if any(c.core and c.state == FAIL for c in components):
        return "down"
    return "degraded" if any(c.state in (WARN, FAIL) for c in components) else "ready"


def _safe(name: str, make) -> list[Component]:
    """A check that itself breaks is reported as a failed component; it never takes the status page down."""
    try:
        return make()
    except Exception as e:
        log.warning("status check %r failed: %s", name, type(e).__name__)
        return [Component(name, FAIL, f"the check itself failed ({type(e).__name__})")]


def from_model_checks(results: list[CheckResult], core: bool = True) -> list[Component]:
    """The Ollama checks of startup.py, as components. Only the server and the model are core (unless Gemini is the main brain)."""
    out = []
    for r in results:
        if r.name == "ollama installed":
            continue                                 # if the server answers, where the binary lives doesn't matter
        label = {"ollama server": "language model server", "model": "language model"}.get(r.name, r.name)
        out.append(Component(label, OK if r.ok else FAIL, r.detail, r.fix, core=core))
    return out


def _safety(guard) -> list[Component]:
    rules = len(guard.policy.rules)
    if not rules:
        return [Component("safety rules", FAIL, "no rules are loaded, so nothing can run", "Check safety/policy.yaml.", core=True)]
    return [Component("safety rules", OK, f"{rules} tools have rules; everything else is refused", core=True)]


def _memory(memory) -> list[Component]:
    s = memory.status()
    if not s.get("enabled"):
        return [Component("memory", OFF, "turned off in config.yaml (memory.enabled)")]
    if not s.get("ok"):
        return [Component("memory", FAIL, s.get("problem", "the memory file can't be used"), "Run scripts/memory_check.py.")]
    c = s.get("counts", {})
    out = [Component("memory", OK, f"{c.get('reminders', 0)} reminders, {c.get('tasks', 0)} tasks, {c.get('notes', 0)} notes")]
    running = getattr(memory.scheduler, "running", None) if getattr(memory, "scheduler", None) is not None else None
    if running is False:
        out.append(Component("reminders", WARN, "not watching the clock yet", "They start with the backend; restart it if this stays."))
    elif running:
        notifier_on = s.get("notifications", True)
        have_osascript = os.path.exists(OSASCRIPT) or bool(shutil.which("osascript"))
        if notifier_on and not have_osascript:
            out.append(Component("reminders", WARN, "watching the clock, but this system can't show notifications",
                                 "Notifications need macOS (osascript)."))
        else:
            out.append(Component("reminders", OK, "watching the clock" + ("" if notifier_on else " (notifications are off)")))
    return out


def _desktop(desktop) -> list[Component]:
    s = desktop.status()
    out = []
    apps = s.get("apps_found", 0)
    out.append(Component("apps and files", OK if apps else FAIL,
                         f"{apps} apps found" if apps else "no apps were found", "" if apps else "Check /Applications."))
    if s.get("hammerspoon"):
        out.append(Component("Hammerspoon", OK, "running; apps are controlled through it"))
    else:
        out.append(Component("Hammerspoon", WARN, "not answering; apps are opened with AppleScript and `open` instead",
                            "Optional: scripts/setup_hammerspoon.sh, then reload Hammerspoon."))
    return out


def _browser(browser) -> list[Component]:
    s = browser.status()
    if not s.get("enabled"):
        return [Component("browser", OFF, "turned off in config.yaml (browser.enabled)")]
    if s.get("problem"):
        return [Component("browser", WARN, s["problem"], "See docs/browser.md.")]
    return [Component("browser", OK, "ready" + ("; the helper is running" if s.get("helper_running") else "; starts when first needed"))]


def _organizer(organizer) -> list[Component]:
    s = organizer.status()
    if not s.get("enabled"):
        return [Component("organizer", OFF, "turned off in config.yaml (organizer.enabled)")]
    if not s.get("folders"):
        return [Component("organizer", WARN, "none of the folders it may organize exist", "Check organizer.folders in config.yaml.")]
    rules = len(s.get("trusted_rules", []))
    return [Component("organizer", OK, "folders: " + ", ".join(s["folders"]) + (f"; {rules} trusted rule(s)" if rules else ""))]


def _voice(voice) -> list[Component]:
    s = voice.status()
    if not s.get("enabled"):
        return [Component("voice", OFF, "turned off in config.yaml (voice.enabled)")]
    stt, tts = s["stt"], s["tts"]
    if stt.get("external"):
        out = [Component("speech to text", OK, "macOS Dictation (STT_ENGINE=macos): click in the text box and press the Dictation key")]
    else:
        out = [Component("speech to text", OK if stt["available"] else WARN, "ready" if stt["available"] else stt["reason"],
                         "" if stt["available"] else (stt["fix"] + " Typing still works."))]
    if tts["available"]:
        names = [e["engine"] for e in tts["engines"] if e["available"]]
        out.append(Component("text to speech", OK, "ready (" + ", ".join(names) + ")"))
    else:
        out.append(Component("text to speech", WARN, "no voice engine is available", "See docs/voice.md. Replies are shown but not spoken."))
    return out


def _gemini(brain) -> Component:
    s = brain.status()
    g = s["gemini"]
    only_brain = not s["fallback_enabled"]               # with no local fallback, Gemini failing means the assistant can't think
    bad = FAIL if only_brain else WARN
    if g["state"] == "off":
        return Component("Gemini", OFF, "turned off (USE_GEMINI=false); the local model does everything on this Mac")
    if g["state"] == "no_key":
        return Component("Gemini", bad, "turned on, but there is no GEMINI_API_KEY",
                         "Put your key in .env (see README.md, Gemini setup)." + ("" if only_brain else " Until then the local model is used."), core=only_brain)
    if g["state"] == "cooling_down":
        wait = f"retrying in {_human(g['retry_in_seconds'])}"
        return Component("Gemini", bad, f"{g['reason']}; {wait}" + ("" if only_brain else "; the local model is answering"),
                         "Check GEMINI_API_KEY / GEMINI_MODEL, your quota and your internet." if g["kind"] in ("auth", "model", "quota") else "",
                         core=only_brain)
    return Component("Gemini", OK, f"ready ({g['model']}); the local model takes over if it fails" if s["fallback_enabled"]
                     else f"ready ({g['model']}); no local fallback", core=only_brain)


def _human(seconds: int) -> str:
    return f"{seconds} s" if seconds < 90 else f"{round(seconds / 60)} min"


def collect(*, model_checks: list[CheckResult], guard, actions=None, voice=None, brain=None) -> list[Component]:
    """Everything, core parts first. Parts that aren't present (a test setup with only some services) are left out."""
    if brain is not None and brain.gemini_wanted:
        s = brain.status()
        if not s["fallback_enabled"]:
            local = [Component(c.name, OFF, "not used (USE_LOCAL_FALLBACK=false)") for c in from_model_checks(model_checks, core=False)]
        else:
            local = from_model_checks(model_checks, core=not brain.gemini_usable)    # a usable Gemini means the local model isn't essential
        parts = local + _safe("Gemini", lambda: [_gemini(brain)])
    else:
        parts = from_model_checks(model_checks) + (_safe("Gemini", lambda: [_gemini(brain)]) if brain is not None else [])
    parts += _safe("safety rules", lambda: _safety(guard))
    if actions is not None:
        desktop = getattr(actions, "desktop", actions if hasattr(actions, "apps") else None)
        for name, part, make in (("apps and files", desktop, _desktop), ("memory", getattr(actions, "memory", None), _memory),
                                 ("browser", getattr(actions, "browser", None), _browser),
                                 ("organizer", getattr(actions, "organizer", None), _organizer)):
            if part is not None:
                parts += _safe(name, lambda make=make, part=part: make(part))
    if voice is not None:
        parts += _safe("voice", lambda: _voice(voice))
    return parts
