"""The final validation: does each part of the assistant really work on this computer?

Every check returns a Result: ok, FAIL (with what to do), or skip (could not be tried here, and why).
Checks take what they need as arguments so they can be tested with stand-ins; `scripts/validate_all.py` wires the real ones.
Nothing here changes your data: reminders and files are tried in a temporary folder.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend.router import Router
from backend.startup import run_checks
from backend.tool_defs import build_registry
from backend.toolcall import ToolCall
from safety import AuditLog, GuardrailManager, Outcome

OK, FAIL, SKIP = "ok", "FAIL", "skip"


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""
    fix: str = ""
    seconds: float = 0.0


def run_one(name: str, check, *args, **kwargs) -> Result:
    """Runs a check, timing it. A check that crashes is a failed check, never a crashed validation."""
    started = time.monotonic()
    try:
        result = check(*args, **kwargs)
    except Exception as e:
        result = Result(name, FAIL, f"the check itself crashed ({type(e).__name__}: {str(e)[:100]})")
    result.name = name
    result.seconds = time.monotonic() - started
    return result


# ---- 1. the language model ------------------------------------------------------------------------------------

def check_model(llm, *, deep: bool = False) -> Result:
    results = {r.name: r for r in run_checks(llm)}
    for name in ("ollama server", "model"):
        r = results.get(name)
        if r is None or not r.ok:
            return Result("model", FAIL, r.detail if r else f"{name} was not checked", r.fix if r else "")
    detail = results["model"].detail
    if deep:
        routed = Router(llm, build_registry(), max_retries=1).route("open chrome")
        if routed.fallback or routed.call.tool != "open_app":
            return Result("model", FAIL, f"it answered, but chose {routed.call.tool!r} for “open chrome”",
                          "Try a larger model or run scripts/validate_model.py to compare models.")
        speed = f", {routed.stats.tokens_per_second:.1f} tokens/s" if routed.stats else ""
        detail += f"; chose open_app for “open chrome”{speed}"
    return Result("model", OK, detail)


# ---- 2. browser automation ------------------------------------------------------------------------------------

class _Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (b"<!doctype html><title>Assistant check</title><a href='/two'>Next page</a>" if self.path == "/"
                else b"<!doctype html><title>Second page</title><p>ok</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def check_browser(service) -> Result:
    """Opens Chrome, loads a page from this computer, reads it, follows a link and opens a second tab."""
    if not service.enabled:
        return Result("browser", SKIP, "browser control is turned off (browser.enabled in config.yaml)")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        steps = [("open_chrome", {}), ("open_url", {"url": base + "/"})]
        for tool, args in steps:
            r = service.execute(tool, args)
            if r.status != "done":
                return Result("browser", FAIL, f"{tool}: {r.message}", "See docs/browser.md; scripts/browser_check.py explains what is missing.")
        title = service.execute("get_page_title", {})
        if "Assistant check" not in title.message:
            return Result("browser", FAIL, f"opened the page but read the wrong title: {title.message}")
        click = service.prepare("click_text", {"text": "Next page"})
        if click.kind != "ready":
            return Result("browser", FAIL, f"could not find the link on the test page: {click.result.message if click.result else click.kind}")
        followed = service.execute("click_text", click.args)
        if followed.status != "done" or "Second page" not in service.execute("get_page_title", {}).message:
            return Result("browser", FAIL, f"clicking the link did not open the second page ({followed.message})")
        tab = service.execute("new_tab", {})
        if tab.status != "done":
            return Result("browser", FAIL, f"new_tab: {tab.message}")
        return Result("browser", OK, "Chrome started, a page loaded, a link was clicked, a new tab opened")
    finally:
        server.shutdown()


# ---- 3. the floating UI shows and hides -----------------------------------------------------------------------

def _osascript(run, *lines: str) -> subprocess.CompletedProcess:
    command = ["/usr/bin/osascript"]
    for line in lines:
        command += ["-e", line]
    return run(command, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)


def check_ui(root: Path, *, run=subprocess.run, platform: str = sys.platform, sleep=time.sleep, hotkey_script: tuple | None = None) -> Result:
    """Starts the app, presses its shortcut and watches the capsule's window disappear and come back.
    Needs macOS, a built app, and Accessibility permission for the terminal. Without those it says so instead of guessing."""
    if platform != "darwin":
        return Result("ui", SKIP, "the capsule is a macOS app; run this check on the Mac")
    app = root / "app" / "build" / "Assistant.app"
    if not app.exists():
        return Result("ui", FAIL, "the app has not been built", "Run: cd app && ./build.sh   (and ./build.sh test for its unit tests)")
    launched = run(["/usr/bin/open", str(app)], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
    if launched.returncode != 0:
        return Result("ui", FAIL, "macOS would not open the app: " + (launched.stderr or "").strip()[:150])
    sleep(2.0)

    def windows() -> int | None:
        r = _osascript(run, 'tell application "System Events" to count windows of (first process whose name is "Assistant")')
        return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None

    try:
        alive = run(["/usr/bin/pgrep", "-x", "Assistant"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
        if alive.returncode != 0:
            return Result("ui", FAIL, "the app started but is not running", "Open it by hand to see its error: open app/build/Assistant.app")
        before = windows()
        if before is None:
            return Result("ui", SKIP, "the app runs, but this terminal may not drive it",
                          "System Settings > Privacy & Security > Accessibility: allow your terminal, then run again. "
                          "Or check by hand: press Cmd+Shift+Space twice; the capsule should hide and show.")
        key = hotkey_script or ('tell application "System Events" to key code 49 using {command down, shift down}',)
        seen = [before]
        for _ in range(2):
            _osascript(run, *key)
            sleep(0.8)
            seen.append(windows())
        if seen[1] is not None and seen[1] != seen[0] and seen[2] == seen[0]:
            return Result("ui", OK, f"the capsule hid and showed again with the shortcut (windows {seen[0]} -> {seen[1]} -> {seen[2]})")
        return Result("ui", SKIP, f"the app runs, but its window count did not change with the shortcut ({seen}); "
                                  "that may just be how macOS reports it", "Check by hand: press Cmd+Shift+Space; the capsule should hide and show.")
    finally:
        _osascript(run, 'tell application "Assistant" to quit')


# ---- 4. microphone and speech ---------------------------------------------------------------------------------

def check_voice(voice, *, run=subprocess.run, platform: str = sys.platform) -> Result:
    status = voice.status()
    if not status["enabled"]:
        return Result("voice", SKIP, "voice is turned off (voice.enabled in config.yaml)")
    stt, tts = status["stt"], status["tts"]
    if not stt["available"]:
        return Result("voice", FAIL, f"speech to text: {stt['reason']}", stt["fix"])
    if not tts["available"]:
        reasons = "; ".join(f"{e['engine']}: {e['reason']}" for e in tts["engines"])
        return Result("voice", FAIL, f"no text to speech engine works ({reasons})", "See docs/voice.md.")
    sentence = "Testing one two three."
    try:
        wav = voice.speak(sentence)
        heard = voice.transcribe(wav).lower()
    except Exception as e:
        return Result("voice", FAIL, f"the speak-and-listen round trip failed: {getattr(e, 'detail', None) or e}", getattr(e, "fix", "") or "")
    matched = sum(word in heard for word in ("testing", "one", "two", "three"))
    if matched < 2:
        return Result("voice", FAIL, f"it spoke the sentence but heard “{heard.strip()}”", "Check the whisper model (docs/voice.md).")
    detail = f"spoke a sentence and heard it back ({matched}/4 words)"
    if platform == "darwin":
        r = run(["/usr/sbin/system_profiler", "SPAudioDataType", "-json"], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
        try:
            devices = json.loads(r.stdout)["SPAudioDataType"][0].get("_items", [])
        except (ValueError, KeyError, IndexError):
            devices = None
        if devices is not None and not any("coreaudio_input_source" in d or "coreaudio_device_input" in d for d in devices):
            return Result("voice", FAIL, "speech works, but this Mac reports no microphone", "Connect or enable a microphone.")
        detail += "; a microphone is present (the app asks for permission the first time you use it)"
    else:
        detail += "; the microphone is only checked on a Mac"
    return Result("voice", OK, detail)


# ---- 5. reminders ---------------------------------------------------------------------------------------------

def check_reminders(*, notify: bool = False) -> Result:
    from memory.notify import Notifier
    from memory.scheduler import ReminderScheduler
    from memory.service import MemoryService
    from memory.store import MemoryStore

    class Recorder:
        def __init__(self):
            self.sent = []

        def send(self, title, body):
            self.sent.append((title, body))
            return True

    with tempfile.TemporaryDirectory() as tmp:            # never the real memory
        now = datetime.now().replace(microsecond=0)
        store = MemoryStore(Path(tmp) / "check.db")
        recorder = Recorder()
        scheduler = ReminderScheduler(store, recorder, clock=lambda: now)
        service = MemoryService(store, recorder, scheduler, clock=lambda: now)
        due = (now + timedelta(minutes=5)).isoformat(timespec="seconds")
        prepared = service.prepare("create_reminder", {"text": "validation check", "due_datetime": due})
        made = service.execute("create_reminder", prepared.args) if prepared.kind == "ready" else prepared.result
        if made.status != "done":
            return Result("reminders", FAIL, made.message)
        if "validation check" not in service.execute("list_reminders", {}).message:
            return Result("reminders", FAIL, "the reminder was saved but not listed")
        if scheduler.check(now + timedelta(minutes=1)) != 0:
            return Result("reminders", FAIL, "a reminder went off before it was due")
        if scheduler.check(now + timedelta(minutes=6)) != 1 or recorder.sent != [("Reminder", "validation check")]:
            return Result("reminders", FAIL, "the reminder did not go off when it was due")
        if store.reminders()[0].status != "fired" or scheduler.check(now + timedelta(minutes=7)) != 0:
            return Result("reminders", FAIL, "a reminder went off twice or was not marked as done")
        detail = "saved, listed, went off once at its time (in a temporary database)"
        if notify:
            if not Notifier().send("Assistant check", "If you can see this, reminders can notify you."):
                return Result("reminders", FAIL, "macOS did not accept the notification",
                              "System Settings > Notifications: allow Script Editor, then run scripts/memory_check.py --notify.")
            detail += "; a real notification was sent, look for it on screen"
        else:
            detail += "; add --notify to also send a real macOS notification"
        return Result("reminders", OK, detail)


# ---- 6. the safety layer --------------------------------------------------------------------------------------

SAFETY_CASES = [
    # (tool, args, expected outcome, needs the risk acknowledged)
    ("open_app", {"app_name": "Safari"}, Outcome.ALLOW, False),
    ("get_battery", {}, Outcome.ALLOW, False),
    ("close_app", {"app_name": "Safari"}, Outcome.CONFIRM, False),
    ("close_app", {"app_name": "Finder"}, Outcome.CONFIRM, True),
    ("open_folder", {"path_or_name": "/etc"}, Outcome.CONFIRM, True),
    ("open_file", {"path_or_name": "~/.ssh/id_rsa"}, Outcome.CONFIRM, True),
    ("open_file", {"path_or_name": "/tmp/installer.pkg"}, Outcome.CONFIRM, True),
    ("open_url", {"url": "https://example.com"}, Outcome.ALLOW, False),
    ("open_url", {"url": "https://example.com/login"}, Outcome.CONFIRM, False),
    ("open_url", {"url": "javascript:alert(1)"}, Outcome.BLOCK, False),
    ("open_url", {"url": "file:///etc/passwd"}, Outcome.BLOCK, False),
    ("run_shell", {"command": "ls"}, Outcome.BLOCK, False),
    ("delete_file", {"path": "~/x"}, Outcome.CONFIRM, True),          # high risk by name, but there is no such tool to choose
    ("set_volume", {"level": 150}, Outcome.BLOCK, False),
    ("click_text", {"text": "Delete account", "nth": 0, "target": "Delete account", "page_hint": ""}, Outcome.CONFIRM, True),
    ("fill_field", {"label": "Password", "nth": 0, "target": "Password", "value": "x", "field_kind": "password", "page_hint": ""}, Outcome.BLOCK, False),
    ("set_preference", {"key": "bank pin", "value": "1234"}, Outcome.BLOCK, False),
    ("organize_desktop", {}, Outcome.CONFIRM, False),
    ("organize_desktop_trusted", {"folder": "/Users/me/Desktop"}, Outcome.BLOCK, False),
    ("some_tool_nobody_wrote", {}, Outcome.BLOCK, False),
]


def check_safety(policy) -> Result:
    selectable = {t.name for t in build_registry().all()}
    if selectable & set(policy.blocked) or "organize_desktop_trusted" in selectable:
        return Result("safety", FAIL, "a capability that must stay blocked can be chosen by the model: "
                      + ", ".join(sorted(selectable & (set(policy.blocked) | {"organize_desktop_trusted"}))), "Remove it from backend/tool_defs.py.")
    unruled = sorted(selectable - set(policy.rules))
    if unruled:
        return Result("safety", FAIL, "tools without a safety rule: " + ", ".join(unruled), "Add them to safety/policy.yaml (or remove them).")
    with tempfile.TemporaryDirectory() as tmp:
        guard = GuardrailManager(policy, AuditLog(Path(tmp) / "audit.jsonl"))
        wrong = []
        for tool, args, expected, acknowledge in SAFETY_CASES:
            d = guard.evaluate(ToolCall(tool, dict(args), False))
            if d.outcome is not expected or (expected is Outcome.CONFIRM and d.requires_acknowledgment != acknowledge):
                wrong.append(f"{tool} {args} -> {d.outcome.value}")
        if wrong:
            return Result("safety", FAIL, f"{len(wrong)} of {len(SAFETY_CASES)} cases were judged wrongly: " + "; ".join(wrong[:3]),
                          "Do not use the assistant until this passes: check safety/policy.yaml and safety/checks.py.")
        if guard.evaluate(ToolCall("open_app", {"app_name": "Safari"}), dry_run=True).outcome is not Outcome.DRY_RUN:
            return Result("safety", FAIL, "dry run did not stop an action")
        asked = guard.evaluate(ToolCall("close_app", {"app_name": "Safari"}))
        first = guard.confirm(asked.confirmation_id, True)
        again = guard.confirm(asked.confirmation_id, True)
        if first.outcome is not Outcome.ALLOW or again.outcome is not Outcome.BLOCK:
            return Result("safety", FAIL, "an approval was not single-use")
        asked = guard.evaluate(ToolCall("close_app", {"app_name": "Finder"}))
        if guard.confirm(asked.confirmation_id, True).outcome is not Outcome.BLOCK:
            return Result("safety", FAIL, "a high-risk action was approved without acknowledging the risk")
        blocker = Path(tmp) / "a-file"
        blocker.write_text("x")
        broken = GuardrailManager(policy, AuditLog(blocker / "audit.jsonl"))          # a log that can't be written
        if broken.evaluate(ToolCall("open_app", {"app_name": "Safari"})).authorized:
            return Result("safety", FAIL, "an action was allowed although it could not be logged")
    return Result("safety", OK, f"{len(SAFETY_CASES)} sample actions judged correctly; every tool has a rule; blocked capabilities "
                                "can't be chosen; dry run, single-use approvals, risk acknowledgment and fail-closed logging hold")


# ---- 7. app and file tools ------------------------------------------------------------------------------------

def check_files(actions, *, live: bool = False) -> Result:
    """Finds apps, finds a file and previews a tidy-up in a temporary folder. With live=True it also opens a folder in Finder."""
    from automation.files import FileFinder
    from organizer.movelog import MoveLog
    from organizer.service import OrganizerService

    status = actions.status()
    if not status.get("apps_found"):
        return Result("apps and files", FAIL, "no applications were found", "Check that /Applications exists and can be read.")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp).resolve() / "home"
        desktop = home / "Desktop"
        desktop.mkdir(parents=True)
        for name in ("budget plan.pdf", "holiday photo.jpg", "song.mp3"):
            (desktop / name).write_text(name)
            old = time.time() - 3 * 86400
            os.utime(desktop / name, (old, old))
        finder = FileFinder(["~/Desktop"], [], home=home)
        found = finder.locate("budget plan", "file")
        if found.kind not in ("exact", "one") or not found.matches[0].path.endswith("budget plan.pdf"):
            return Result("apps and files", FAIL, f"could not find a file by name (got {found.kind})")
        organizer = OrganizerService(MoveLog(Path(tmp) / "moves.jsonl"), files=finder, home=home)
        prepared = organizer.prepare("organize_desktop", {})
        if prepared.kind != "ready" or "I would move 3 files" not in prepared.preview:
            return Result("apps and files", FAIL, "the tidy-up preview was not produced")
        if sorted(p.name for p in desktop.iterdir()) != sorted(["budget plan.pdf", "holiday photo.jpg", "song.mp3"]):
            return Result("apps and files", FAIL, "previewing a tidy-up changed files")
        detail = f"{status['apps_found']} apps found; found a file by name; previewed a tidy-up without moving anything"
        detail += "; Hammerspoon is running" if status.get("hammerspoon") else "; Hammerspoon is not running (AppleScript is used instead)"
        if live:
            r = actions.execute("open_folder", {"path_or_name": str(desktop)})
            if r is None or r.status != "done":
                return Result("apps and files", FAIL, f"opening a folder failed: {r.message if r else 'nothing ran'}")
            detail += "; opened a folder in Finder"
        return Result("apps and files", OK, detail)


# ---- the running backend ----------------------------------------------------------------------------------------

def check_backend(base_url: str, *, fetch) -> Result:
    """`fetch(url)` returns parsed JSON or raises OSError. A backend that is not running is a skip, not a failure."""
    try:
        health = fetch(base_url + "/health")
    except (OSError, ValueError):
        return Result("backend", SKIP, "the backend is not running", "Start it with scripts/start.sh to check it too.")
    try:
        status = fetch(base_url + "/status")
    except (OSError, ValueError):
        return Result("backend", FAIL, "it answers /health but not /status")
    bad = [c for c in status["components"] if c["state"] == "fail"]
    if status["state"] == "down":
        return Result("backend", FAIL, "it is running but not ready: " + ", ".join(c["name"] for c in bad), next((c["fix"] for c in bad if c["fix"]), ""))
    note = "" if not bad else f"; {len(bad)} optional part(s) failing: " + ", ".join(c["name"] for c in bad)
    return Result("backend", OK, f"running (phase {health.get('phase')}), state {status['state']}{note}")
