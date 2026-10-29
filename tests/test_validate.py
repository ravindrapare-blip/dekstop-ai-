import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from automation.choices import ChoiceStore
from backend import validate as v
from backend.config import ROOT
from backend.llm import OllamaClient
from browserctl import BrowserService
from fake_browser import FakeWorker, cand
from fakes import make_world
from safety import Policy
from test_voice import FakeEngine, make_wav
from voice import VoiceService

POLICY = Policy.load(ROOT / "safety" / "policy.yaml")


def llm(*, models=("m",), up=True, chat=None):
    def handler(request):
        if not up:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"role": "assistant", "content": chat or "{}"}, "eval_count": 10, "eval_duration": 10**9})
        return httpx.Response(200, json={"version": "0.1", "models": [{"name": n} for n in models]})
    return OllamaClient("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(handler))


def tool(name, **args):
    return json.dumps({"tool": name, "args": args, "requires_confirmation": False})


# ---- running a check ------------------------------------------------------------------------------------------

def test_a_crashing_check_is_a_failed_check_not_a_crashed_validation():
    def broken():
        raise RuntimeError("boom with /secret/path")
    r = v.run_one("thing", broken)
    assert r.name == "thing" and r.status == v.FAIL and "RuntimeError" in r.detail and r.seconds >= 0


def test_run_one_names_and_times_the_result():
    r = v.run_one("model", lambda: v.Result("whatever", v.OK, "fine"))
    assert (r.name, r.status, r.detail) == ("model", v.OK, "fine")


# ---- 1. model ----------------------------------------------------------------------------------------------------

def test_the_model_check_passes_when_the_model_is_there():
    r = v.check_model(llm())
    assert r.status == v.OK and "m is available" in r.detail


def test_the_model_check_explains_a_missing_model_and_a_missing_server():
    missing = v.check_model(llm(models=("other",)))
    assert missing.status == v.FAIL and "not pulled" in missing.detail and "ollama pull" in missing.fix
    down = v.check_model(llm(up=False))
    assert down.status == v.FAIL and "cannot connect" in down.detail and "ollama serve" in down.fix


def test_the_deep_model_check_makes_it_choose_a_tool():
    good = v.check_model(llm(chat=tool("open_app", app_name="Google Chrome")), deep=True)
    assert good.status == v.OK and "chose open_app" in good.detail and "tokens/s" in good.detail
    wrong = v.check_model(llm(chat=tool("reply", text="hi")), deep=True)
    assert wrong.status == v.FAIL and "'reply'" in wrong.detail and "validate_model" in wrong.fix
    nonsense = v.check_model(llm(chat="not json"), deep=True)
    assert nonsense.status == v.FAIL


# ---- 2. browser ----------------------------------------------------------------------------------------------------

class PageWorker(FakeWorker):
    """A pretend Chrome that knows the validation page: a title, and one link to a second page."""

    def _go(self, tab, url, steps):
        out = super()._go(tab, url, steps)
        tab["title"] = out["title"] = "Second page" if url.endswith("/two") else "Assistant check"
        return out

    def do_find_clickable(self, text, limit=6):
        self.buttons = [cand("Next page", kind="link")] if "Next page" in text else []
        return super().do_find_clickable(text, limit)

    def do_click(self, text, nth=0, expect=None):
        tab = self._active()
        base = tab["url"].rstrip("/")
        tab["url"], tab["title"] = base + "/two", "Second page"
        return {"clicked": expect or text, "navigated": True, "new_tab": False, "url": tab["url"], "title": "Second page", "login": False, "payment": False, "steps": []}


def test_the_browser_check_drives_a_page_a_link_and_a_tab():
    worker = PageWorker()
    r = v.check_browser(BrowserService(worker, choices=ChoiceStore()))
    assert r.status == v.OK and worker.names().count("new_tab") == 1 and "click" in worker.names()


def test_the_browser_check_reports_what_went_wrong():
    from browserctl.client import WorkerUnavailable
    broken = PageWorker()
    broken.errors["open_chrome"] = WorkerUnavailable("The browser helper isn't installed yet. Run this once:  cd browser && npm install")
    r = v.check_browser(BrowserService(broken))
    assert r.status == v.FAIL and "npm install" in r.detail and "docs/browser.md" in r.fix

    class WrongTitle(PageWorker):
        def do_get_title(self): return {"title": "Something else"}
    assert v.check_browser(BrowserService(WrongTitle())).status == v.FAIL

    class NoLink(PageWorker):
        def do_find_clickable(self, text, limit=6): return FakeWorker.do_find_clickable(self, text, limit)
    assert "find the link" in v.check_browser(BrowserService(NoLink())).detail


def test_a_browser_that_is_turned_off_is_skipped_not_failed():
    service = BrowserService(PageWorker(), enabled=False)
    assert v.check_browser(service).status == v.SKIP


# ---- 3. the UI --------------------------------------------------------------------------------------------------

class Mac:
    """A scripted `run` for macOS commands: records them, and answers the window-count question from a list."""

    def __init__(self, counts, *, open_ok=True, running=True, denied=False):
        self.counts, self.open_ok, self.running, self.denied = list(counts), open_ok, running, denied
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        done = lambda code=0, out="", err="": subprocess.CompletedProcess(command, code, out, err)
        if command[0] == "/usr/bin/open":
            return done(0 if self.open_ok else 1, err="" if self.open_ok else "not allowed")
        if command[0] == "/usr/bin/pgrep":
            return done(0 if self.running else 1)
        script = " ".join(command)
        if "count windows" in script:
            if self.denied:
                return done(1, err="not allowed assistive access")
            return done(0, str(self.counts.pop(0)))
        return done()


@pytest.fixture
def built(tmp_path):
    (tmp_path / "app" / "build" / "Assistant.app").mkdir(parents=True)
    return tmp_path


def test_the_ui_check_needs_a_mac(tmp_path):
    assert v.check_ui(tmp_path, platform="linux").status == v.SKIP


def test_the_ui_check_says_when_the_app_is_not_built(tmp_path):
    r = v.check_ui(tmp_path, platform="darwin", run=Mac([]), sleep=lambda s: None)
    assert r.status == v.FAIL and "build.sh" in r.fix


def test_the_ui_check_sees_the_capsule_hide_and_show(built):
    mac = Mac([1, 0, 1])
    r = v.check_ui(built, platform="darwin", run=mac, sleep=lambda s: None)
    assert r.status == v.OK and "1 -> 0 -> 1" in r.detail
    assert any("quit" in " ".join(c) for c in mac.commands[-1:])                 # and the app is closed again afterwards
    assert sum("key code" in " ".join(c) for c in mac.commands) == 2


def test_the_ui_check_does_not_claim_success_when_nothing_changed(built):
    r = v.check_ui(built, platform="darwin", run=Mac([1, 1, 1]), sleep=lambda s: None)
    assert r.status == v.SKIP and "by hand" in r.fix


def test_the_ui_check_asks_for_accessibility_instead_of_failing(built):
    r = v.check_ui(built, platform="darwin", run=Mac([], denied=True), sleep=lambda s: None)
    assert r.status == v.SKIP and "Accessibility" in r.fix


def test_the_ui_check_fails_if_the_app_will_not_run(built):
    assert v.check_ui(built, platform="darwin", run=Mac([], open_ok=False), sleep=lambda s: None).status == v.FAIL
    r = v.check_ui(built, platform="darwin", run=Mac([], running=False), sleep=lambda s: None)
    assert r.status == v.FAIL and "not running" in r.detail


# ---- 4. microphone and speech ---------------------------------------------------------------------------------

def speech(stt_text=b"Testing one two three", stt_ok=True, tts_ok=True, enabled=True):
    return VoiceService(FakeEngine("whisper", wav=stt_text, ok=stt_ok), [FakeEngine("Piper", wav=make_wav(1.0), ok=tts_ok)], enabled=enabled)


def profiler(inputs=True, broken=False):
    def run(command, **kwargs):
        out = "not json" if broken else json.dumps({"SPAudioDataType": [{"_items": [{"_name": "Speakers"}] + ([{"_name": "Mic", "coreaudio_input_source": "x"}] if inputs else [])}]})
        return subprocess.CompletedProcess(command, 0, out, "")
    return run


def test_speech_round_trip_works():
    r = v.check_voice(speech(), platform="linux")
    assert r.status == v.OK and "4/4 words" in r.detail and "only checked on a Mac" in r.detail


def test_the_microphone_is_looked_for_on_a_mac():
    assert "microphone is present" in v.check_voice(speech(), platform="darwin", run=profiler(True)).detail
    no_mic = v.check_voice(speech(), platform="darwin", run=profiler(False))
    assert no_mic.status == v.FAIL and "no microphone" in no_mic.detail
    assert v.check_voice(speech(), platform="darwin", run=profiler(broken=True)).status == v.OK        # can't tell: not a failure


def test_speech_problems_are_named():
    assert v.check_voice(speech(enabled=False)).status == v.SKIP
    stt = v.check_voice(speech(stt_ok=False))
    assert stt.status == v.FAIL and "speech to text" in stt.detail
    tts = v.check_voice(speech(tts_ok=False))
    assert tts.status == v.FAIL and "text to speech" in tts.detail
    deaf = v.check_voice(speech(stt_text=b"completely different words"))
    assert deaf.status == v.FAIL and "heard" in deaf.detail and "whisper" in deaf.fix


def test_a_speech_engine_that_errors_during_the_round_trip_fails_the_check():
    from voice import VoiceError
    engine = FakeEngine("Piper", error=VoiceError("failed", "Piper crashed.", "Reinstall Piper."))
    r = v.check_voice(VoiceService(FakeEngine("whisper", wav=b"x"), [engine]))
    assert r.status == v.FAIL and "Piper crashed" in r.detail


# ---- 5. reminders ------------------------------------------------------------------------------------------------

def test_the_reminder_check_goes_through_the_whole_cycle_without_touching_real_memory():
    r = v.check_reminders()
    assert r.status == v.OK and "went off once" in r.detail
    assert not (ROOT / "data" / "check.db").exists()


def test_the_reminder_check_can_send_a_real_notification(monkeypatch):
    from memory import notify
    monkeypatch.setattr(notify.Notifier, "send", lambda self, title, body: True)
    assert "real notification was sent" in v.check_reminders(notify=True).detail
    monkeypatch.setattr(notify.Notifier, "send", lambda self, title, body: False)
    r = v.check_reminders(notify=True)
    assert r.status == v.FAIL and "Script Editor" in r.fix


def test_the_reminder_check_catches_a_broken_scheduler(monkeypatch):
    from memory import scheduler
    monkeypatch.setattr(scheduler.ReminderScheduler, "check", lambda self, now=None: 0)
    assert v.check_reminders().status == v.FAIL
    monkeypatch.setattr(scheduler.ReminderScheduler, "check", lambda self, now=None: 1)
    assert "before it was due" in v.check_reminders().detail


# ---- 6. safety -------------------------------------------------------------------------------------------------

def test_the_safety_check_passes_with_the_real_policy():
    r = v.check_safety(POLICY)
    assert r.status == v.OK and f"{len(v.SAFETY_CASES)} sample actions" in r.detail


def test_the_safety_check_fails_if_a_rule_is_loosened():
    loose = Policy.from_dict({"rules": {**{k: v_.label for k, v_ in POLICY.rules.items()}, "close_app": "low", "open_app": "low"}, "blocked": {}})
    r = v.check_safety(loose)
    assert r.status == v.FAIL and "judged wrongly" in r.detail


def test_the_safety_check_fails_if_a_tool_has_no_rule():
    rules = {k: v_.label for k, v_ in POLICY.rules.items() if k != "get_time"}
    r = v.check_safety(Policy.from_dict({"rules": rules, "blocked": {}}))
    assert r.status == v.FAIL and "get_time" in r.detail


def test_the_safety_check_fails_if_a_blocked_capability_became_a_tool():
    raw = {"rules": {k: v_.label for k, v_ in POLICY.rules.items()}, "blocked": {"open_app": {"risk": "high", "reason": "x"}}}
    # open_app is both a rule and blocked: the policy itself refuses to load that, which is also the right outcome
    with pytest.raises(Exception):
        Policy.from_dict(raw)
    raw["rules"].pop("open_app")
    r = v.check_safety(Policy.from_dict(raw))
    assert r.status == v.FAIL and "open_app" in r.detail


# ---- 7. apps and files -------------------------------------------------------------------------------------------

def test_the_files_check_previews_a_tidy_up_and_changes_nothing():
    world = make_world(Path(__import__("tempfile").mkdtemp()))
    r = v.check_files(world.service)
    assert r.status == v.OK and "previewed a tidy-up without moving anything" in r.detail and "AppleScript" in r.detail
    assert world.run.calls == []                                           # nothing was opened or run


def test_the_live_files_check_really_opens_a_folder():
    world = make_world(Path(__import__("tempfile").mkdtemp()))
    r = v.check_files(world.service, live=True)
    assert r.status == v.OK and "opened a folder in Finder" in r.detail and world.run.commands("open")


def test_the_files_check_fails_without_apps(tmp_path):
    r = v.check_files(make_world(tmp_path, apps={}).service)
    assert r.status == v.FAIL and "/Applications" in r.fix


# ---- the running backend ----------------------------------------------------------------------------------------

def fetcher(health=None, status=None, down=False, status_down=False):
    def fetch(url):
        if down:
            raise OSError("refused")
        if url.endswith("/health"):
            return health or {"status": "ok", "phase": 9}
        if status_down:
            raise OSError("boom")
        return status
    return fetch


def comp(name, state, fix="", core=False):
    return {"name": name, "state": state, "detail": "", "fix": fix, "core": core}


def test_the_backend_check_reads_the_status():
    assert v.check_backend("http://x", fetch=fetcher(down=True)).status == v.SKIP
    ok = v.check_backend("http://x", fetch=fetcher(status={"state": "ready", "components": [comp("a", "ok")]}))
    assert ok.status == v.OK and "phase 9" in ok.detail
    degraded = v.check_backend("http://x", fetch=fetcher(status={"state": "degraded", "components": [comp("voice", "fail")]}))
    assert degraded.status == v.OK and "voice" in degraded.detail
    down = v.check_backend("http://x", fetch=fetcher(status={"state": "down", "components": [comp("language model", "fail", "pull it", core=True)]}))
    assert down.status == v.FAIL and down.fix == "pull it"
    assert v.check_backend("http://x", fetch=fetcher(status_down=True)).status == v.FAIL


# ---- the scripts -------------------------------------------------------------------------------------------------

def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_script(*args):
    return subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "validate_all.py"), *args], capture_output=True, text=True,
                          cwd=ROOT, timeout=120, env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})


def test_the_script_lists_its_checks_and_rejects_unknown_ones():
    listed = run_script("--list")
    assert listed.returncode == 0 and listed.stdout.split() == ["model", "gemini", "browser", "ui", "voice", "reminders", "safety", "files", "backend"]
    bad = run_script("--only", "nonsense")
    assert bad.returncode == 2 and "Unknown check" in bad.stdout


def test_the_script_runs_the_checks_that_work_anywhere_and_exits_zero():
    out = run_script("--only", "reminders,safety,backend")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "[  ok] reminders" in out.stdout and "[  ok] safety" in out.stdout
    assert "3 checks:" in out.stdout and "0 failed" in out.stdout


def test_the_script_exits_nonzero_and_says_how_to_fix_it_when_a_check_fails():
    out = run_script("--only", "voice")            # no speech engines are built in the test environment
    assert out.returncode == 1 and "[FAIL] voice" in out.stdout and "->" in out.stdout and "1 failed" in out.stdout


def test_skipped_checks_are_listed_as_not_verified():
    out = run_script("--only", "ui")
    assert out.returncode == 0 and "Skipped checks were NOT verified: ui" in out.stdout


def test_the_status_script_prints_the_report_and_its_exit_code(capsys, monkeypatch):
    status = load_script("status")
    report = {"ready": True, "state": "degraded", "model": "m", "components": [comp("memory", "ok"), comp("voice", "warn", "install whisper")]}
    monkeypatch.setattr(status, "from_backend", lambda cfg: report)
    assert status.main() == 0
    text = capsys.readouterr().out
    assert "READY, with parts that need attention" in text and "✓ memory" in text and "! voice" in text and "fix: install whisper" in text
    monkeypatch.setattr(status, "from_backend", lambda cfg: {**report, "ready": False, "state": "down"})
    assert status.main() == 1 and "NOT READY" in capsys.readouterr().out


# ---- the checks must notice a faulty component (they would be worthless if they only ever passed) -------------

def test_the_reminder_check_notices_a_reminder_that_goes_off_twice(monkeypatch):
    from memory import scheduler
    original, calls = scheduler.ReminderScheduler.check, [0]

    def sometimes_twice(self, now=None):
        calls[0] += 1
        result = original(self, now)
        return 1 if calls[0] == 3 else result

    monkeypatch.setattr(scheduler.ReminderScheduler, "check", sometimes_twice)
    r = v.check_reminders()
    assert r.status == v.FAIL and "twice" in r.detail


def test_the_safety_check_notices_approvals_that_can_be_reused(monkeypatch):
    class Reusable(v.GuardrailManager):
        def confirm(self, confirmation_id, approved, **kw):
            saved = self._pending.get(confirmation_id)
            decision = super().confirm(confirmation_id, approved, **kw)
            if saved is not None:
                self._pending[confirmation_id] = saved
            return decision
    monkeypatch.setattr(v, "GuardrailManager", Reusable)
    r = v.check_safety(POLICY)
    assert r.status == v.FAIL and "single-use" in r.detail


def test_the_safety_check_notices_a_missing_risk_acknowledgment_rule(monkeypatch):
    class Lax(v.GuardrailManager):
        def confirm(self, confirmation_id, approved, *, acknowledge_risk=False):
            return super().confirm(confirmation_id, approved, acknowledge_risk=True)
    monkeypatch.setattr(v, "GuardrailManager", Lax)
    r = v.check_safety(POLICY)
    assert r.status == v.FAIL and "acknowledging" in r.detail


def test_the_safety_check_notices_actions_allowed_without_a_log(monkeypatch):
    monkeypatch.setattr(v, "AuditLog", lambda path: type("Silent", (), {"write": lambda self, event: None})())
    r = v.check_safety(POLICY)
    assert r.status == v.FAIL and "could not be logged" in r.detail


def test_the_safety_check_says_which_blocked_capability_became_selectable():
    raw = {"rules": {k: v_.label for k, v_ in POLICY.rules.items() if k != "get_time"}, "blocked": {"get_time": {"risk": "high", "reason": "x"}}}
    r = v.check_safety(Policy.from_dict(raw))
    assert r.status == v.FAIL and r.detail.startswith("a capability that must stay blocked can be chosen by the model: get_time")


def test_the_files_check_notices_a_preview_that_moves_files(monkeypatch):
    from organizer.service import OrganizerService
    original = OrganizerService.prepare

    def careless(self, tool, args, confirm=False, dry_run=None):
        prepared = original(self, tool, args, confirm, dry_run)
        next((self.home / "Desktop").iterdir()).unlink()          # a "preview" that deletes something
        return prepared

    monkeypatch.setattr(OrganizerService, "prepare", careless)
    world = make_world(Path(__import__("tempfile").mkdtemp()))
    r = v.check_files(world.service)
    assert r.status == v.FAIL and "changed files" in r.detail


def test_the_browser_check_notices_a_wrong_title_even_if_the_rest_works():
    class FirstTitleWrong(PageWorker):
        reads = 0
        def do_get_title(self):
            type(self).reads += 1
            return {"title": "Something else"} if type(self).reads == 1 else super().do_get_title()
    r = v.check_browser(BrowserService(FirstTitleWrong()))
    assert r.status == v.FAIL and "wrong title" in r.detail


# ---- the Gemini check --------------------------------------------------------------------------------------------

def _brain(*, wanted=True, with_key=True, fallback=True, reply=None, status=200):
    from backend.brain import Brain
    from backend.gemini import GeminiClient
    def server(request):
        return httpx.Response(status, json=reply if reply is not None else {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]})
    gemini = GeminiClient("k" * 30, "gemini-test", transport=httpx.MockTransport(server)) if with_key else None
    return Brain(None, gemini, gemini_wanted=wanted, use_local_fallback=fallback)


def test_the_gemini_check_is_skipped_when_gemini_is_off():
    r = v.check_gemini(_brain(wanted=False, with_key=False))
    assert r.status == v.SKIP and "USE_GEMINI=false" in r.detail


def test_the_gemini_check_says_when_the_key_is_missing():
    r = v.check_gemini(_brain(with_key=False))
    assert r.status == v.FAIL and "no GEMINI_API_KEY" in r.detail and ".env" in r.fix


def test_the_gemini_check_passes_with_a_working_key_and_names_the_fallback():
    r = v.check_gemini(_brain())
    assert r.status == v.OK and "gemini-test answered" in r.detail and "local model is the fallback" in r.detail
    assert "NO local fallback" in v.check_gemini(_brain(fallback=False)).detail


@pytest.mark.parametrize("status,body,phrase", [(429, {"error": {"status": "RESOURCE_EXHAUSTED"}}, "quota"), (403, {"error": {"status": "PERMISSION_DENIED"}}, "GEMINI_API_KEY"),
                                                (404, {"error": {"status": "NOT_FOUND"}}, "GEMINI_MODEL"), (503, {"error": {"status": "UNAVAILABLE"}}, "trouble")])
def test_the_gemini_check_explains_each_failure_and_what_to_do(status, body, phrase):
    r = v.check_gemini(_brain(reply=body, status=status))
    assert r.status == v.FAIL and phrase in (r.detail + r.fix)


def test_the_gemini_check_warns_when_failure_means_no_answers_at_all():
    r = v.check_gemini(_brain(reply={"error": {"status": "RESOURCE_EXHAUSTED"}}, status=429, fallback=False))
    assert "USE_LOCAL_FALLBACK is off" in r.detail


def test_the_gemini_check_does_not_disturb_the_live_fallback_state():
    brain = _brain(reply={"error": {"status": "RESOURCE_EXHAUSTED"}}, status=429)
    v.check_gemini(brain)
    assert brain.status()["gemini"]["state"] == "ready" and brain.switches == 0


def test_the_voice_check_understands_macos_dictation():
    from backend.settings import apply_to_config, load_settings
    from voice import voice_service_from_config
    cfg = apply_to_config({"voice": {}}, load_settings({}, {"STT_ENGINE": "macos"}))
    service = voice_service_from_config(cfg, ROOT)
    service.synthesizers = [FakeEngine("Piper", wav=make_wav(1.0))]
    r = v.check_voice(service)
    assert r.status == v.OK and "macOS Dictation" in r.detail and "by hand" in r.detail
    service.synthesizers = [FakeEngine("Piper", ok=False)]
    assert v.check_voice(service).status == v.FAIL
