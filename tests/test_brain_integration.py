"""Gemini + local fallback through the real HTTP API: a real GeminiClient and a real OllamaClient, each talking to a stand-in server."""
import json
import logging
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from automation.choices import ChoiceStore
from backend.actions import ActionRouter
from backend.brain import Brain, build_brain
from backend.config import ROOT
from backend.gemini import GeminiClient
from backend.llm import OllamaClient
from backend.main import build_guard, create_app
from backend.settings import load_settings
from backend.status import collect
from backend.tool_defs import build_registry
from backend.startup import run_checks
from browserctl import BrowserService
from fake_browser import FakeWorker
from fakes import make_world
from memory import MemoryService
from memory.notify import Notifier
from memory.store import MemoryStore
from safety import AuditLog, GuardrailManager, Outcome, Policy
from test_voice import FakeEngine, make_wav
from voice import VoiceService, voice_service_from_config
from voice.base import VoiceError

POLICY = Policy.load(ROOT / "safety" / "policy.yaml")
KEY = "test-key-not-a-real-key-0123456789"


def call(tool, **args):
    return json.dumps({"tool": tool, "args": args, "requires_confirmation": False})


def gemini_ok(text):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}], "usageMetadata": {"candidatesTokenCount": 5}})


class Rig:
    """Gemini first, Ollama behind it, a pretend Mac and browser, the real safety layer."""

    def __init__(self, tmp_path, monkeypatch, *, fallback=True, safe_mode=False, send_memory=False, speak_notice=False):
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        self.clock = [0.0]
        self.gemini_requests: list[dict] = []
        self.local_requests: list[dict] = []
        self.gemini_reply = lambda: gemini_ok(call("reply", text="Hi from Gemini."))
        self.local_reply = call("reply", text="Hi from the local model.")
        self.local_down = False

        def gemini(request):
            self.gemini_requests.append({"headers": dict(request.headers), "url": str(request.url), "body": json.loads(request.content)})
            return self.gemini_reply()

        def local(request):
            if self.local_down:
                raise httpx.ConnectError("refused", request=request)
            if request.url.path == "/api/chat":
                self.local_requests.append(json.loads(request.content))
                return httpx.Response(200, json={"message": {"role": "assistant", "content": self.local_reply}})
            return httpx.Response(200, json={"version": "0.1", "models": [{"name": "qwen"}]})

        self.llm = OllamaClient("http://127.0.0.1:11434", "qwen", transport=httpx.MockTransport(local), retries=0)
        self.gemini = GeminiClient(KEY, "gemini-test", send_private_context=send_memory, transport=httpx.MockTransport(gemini))
        self.brain = Brain(self.llm, self.gemini, gemini_wanted=True, use_local_fallback=fallback, speak_notice=speak_notice, clock=lambda: self.clock[0])
        self.world = make_world(tmp_path / "mac")
        choices = ChoiceStore()
        self.world.service.choices = choices
        self.worker = FakeWorker()
        self.store = MemoryStore(tmp_path / "m.db")
        self.memory = MemoryService(self.store, Notifier(enabled=False), None, choices=choices, notifications=False,
                                    clock=lambda: datetime(2026, 10, 29, 14, 0))
        self.router = ActionRouter(self.world.service, BrowserService(self.worker, choices=choices), choices, self.memory)
        cfg = {"safety": {"safe_mode": safe_mode}}
        self.audit = tmp_path / "audit.jsonl"
        self.guard = GuardrailManager(POLICY, AuditLog(self.audit),
                                      safe_mode_tools=build_guard(cfg).safe_mode_tools)
        self.voice = VoiceService(FakeEngine("stt", wav=b"hello"), [FakeEngine("Piper", wav=make_wav(0.3))])
        self.app = create_app({"ollama": {}, "router": {"max_retries": 1}}, self.llm, guard=self.guard, voice=self.voice,
                              actions=self.router, brain=self.brain)

    def say(self, client, text, **extra):
        return client.post("/route", json={"text": text, **extra}).json()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


# ---- the happy path and the switch ----------------------------------------------------------------------------------

def test_gemini_answers_and_the_key_travels_only_in_a_header(rig):
    with TestClient(rig.app) as c:
        body = rig.say(c, "hello")
    assert body["brain"] == {"engine": "gemini", "notice": "", "speak": False} and body["guard"]["args"]["text"] == "Hi from Gemini."
    sent = rig.gemini_requests[0]
    assert sent["headers"]["x-goog-api-key"] == KEY and KEY not in sent["url"] and sent["url"].startswith("https://generativelanguage.googleapis.com/")
    assert rig.local_requests == []


def test_quota_trouble_switches_to_the_local_model_and_the_user_is_told(rig):
    rig.gemini_reply = lambda: httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}})
    with TestClient(rig.app) as c:
        first = rig.say(c, "hello")
        second = rig.say(c, "hello")
    assert first["guard"]["args"]["text"] == "Hi from the local model." and first["brain"]["engine"] == "ollama"
    assert first["brain"]["notice"].startswith("Gemini's quota or rate limit was reached, so I switched to the local model.")
    assert second["brain"] == {"engine": "ollama", "notice": "", "speak": False} and len(rig.gemini_requests) == 1


def test_when_gemini_recovers_the_next_request_after_the_cooldown_goes_back_to_it(rig):
    rig.gemini_reply = lambda: httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE"}})
    with TestClient(rig.app) as c:
        rig.say(c, "hello")
        rig.gemini_reply = lambda: gemini_ok(call("reply", text="Hi from Gemini."))
        rig.clock[0] = 301
        back = rig.say(c, "hello")
        later = rig.say(c, "hello")
    assert back["brain"]["engine"] == "gemini" and back["brain"]["notice"] == "Gemini is available again, so I'm using it."
    assert later["brain"]["notice"] == "" and later["brain"]["engine"] == "gemini"


def test_the_switch_notice_can_ask_to_be_spoken(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, speak_notice=True)
    rig.gemini_reply = lambda: httpx.Response(429, json={})
    with TestClient(rig.app) as c:
        assert rig.say(c, "hello")["brain"]["speak"] is True


def test_a_slow_gemini_means_a_bounded_wait_then_the_local_model(rig):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    rig.gemini._http = httpx.Client(base_url="https://generativelanguage.googleapis.com", transport=httpx.MockTransport(slow),
                                    headers={"x-goog-api-key": KEY})
    with TestClient(rig.app) as c:
        body = rig.say(c, "hello")
    assert body["brain"]["engine"] == "ollama" and "didn't answer in time" in body["brain"]["notice"]


def test_shutdown_closes_the_connection_to_gemini(rig):
    closed = []
    real = rig.gemini.close
    rig.gemini.close = lambda: (closed.append("gemini"), real())
    with TestClient(rig.app):
        pass
    assert closed == ["gemini"]


# ---- both down, and no fallback --------------------------------------------------------------------------------------------

def test_with_both_brains_down_the_user_gets_one_clear_error(rig):
    rig.gemini_reply = lambda: httpx.Response(429, json={})
    rig.local_down = True
    with TestClient(rig.app) as c:
        res = c.post("/route", json={"text": "hello"})
    assert res.status_code == 503 and "Ollama" in res.json()["detail"] and "Gemini is also unavailable" in res.json()["detail"]
    assert KEY not in res.text


def test_with_the_fallback_off_a_gemini_failure_is_a_503_and_the_local_model_is_not_used(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, fallback=False)
    rig.gemini_reply = lambda: httpx.Response(429, json={})
    with TestClient(rig.app) as c:
        res = c.post("/route", json={"text": "hello"})
    assert res.status_code == 503 and "USE_LOCAL_FALLBACK=false" in res.json()["detail"] and rig.local_requests == []


# ---- the safety layer judges Gemini exactly as it judges the local model -----------------------------------------------------------

@pytest.mark.parametrize("tool,args,outcome", [
    ("open_url", {"url": "javascript:alert(1)"}, "block"),
    ("open_url", {"url": "file:///etc/passwd"}, "block"),
    ("close_app", {"app_name": "Safari"}, "confirm"),
    ("close_app", {"app_name": "Finder"}, "confirm"),
    ("open_folder", {"path_or_name": "/etc"}, "confirm"),
    ("set_preference", {"key": "my password", "value": "hunter2"}, "block"),
    ("organize_desktop_trusted", {"folder": "/x", "rule": "all"}, None),             # not a tool at all
    ("run_shell", {"command": "rm -rf ~"}, None),
])
def test_what_gemini_proposes_is_judged_by_the_same_safety_rules(rig, tool, args, outcome):
    rig.gemini_reply = lambda: gemini_ok(json.dumps({"tool": tool, "args": args, "requires_confirmation": False}))
    with TestClient(rig.app) as c:
        body = rig.say(c, "do it")
    if outcome is None:
        assert body["fallback"] is True and body["tool"] == "reply"                    # unknown tools never reach the executor
    else:
        assert body["guard"]["outcome"] == outcome and (outcome != "block" or body["result"] is None)
    assert rig.world.run.calls == [] and rig.worker.names() == []                       # nothing was run on the Mac or in Chrome


def test_gemini_cannot_waive_a_confirmation(rig):
    rig.gemini_reply = lambda: gemini_ok(json.dumps({"tool": "close_app", "args": {"app_name": "Safari"}, "requires_confirmation": False}))
    with TestClient(rig.app) as c:
        asked = rig.say(c, "quit safari")
        assert asked["guard"]["outcome"] == "confirm" and rig.world.run.calls == []
        done = c.post("/confirm", json={"confirmation_id": asked["guard"]["confirmation_id"], "approved": True}).json()
    assert done["result"]["status"] == "done"


def test_a_dry_run_is_still_a_dry_run_with_gemini(rig):
    rig.gemini_reply = lambda: gemini_ok(call("open_app", app_name="Safari"))
    with TestClient(rig.app) as c:
        body = rig.say(c, "open safari", dry_run=True)
    assert body["guard"]["outcome"] == "dry_run" and rig.world.run.calls == []


# ---- privacy ----------------------------------------------------------------------------------------------------------------------------

def test_saved_preferences_are_not_sent_to_gemini_by_default_but_the_local_model_still_gets_them(rig):
    rig.memory.store.set_preference("preferred_browser", "Walnut", datetime(2026, 10, 29))
    rig.gemini_reply = lambda: httpx.Response(429, json={})
    with TestClient(rig.app) as c:
        rig.say(c, "hello")
    assert "Walnut" not in json.dumps(rig.gemini_requests[0]["body"]) and "Remembered about" not in json.dumps(rig.gemini_requests[0]["body"])
    assert "preferred browser: Walnut" in rig.local_requests[0]["messages"][0]["content"]


def test_preferences_are_sent_only_if_the_user_opted_in(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, send_memory=True)
    rig.memory.store.set_preference("preferred_browser", "Walnut", datetime(2026, 10, 29))
    with TestClient(rig.app) as c:
        rig.say(c, "hello")
    assert "preferred browser: Walnut" in json.dumps(rig.gemini_requests[0]["body"])


def test_the_key_appears_nowhere_a_client_or_a_log_can_see(rig, caplog):
    caplog.set_level(logging.DEBUG)
    rig.gemini_reply = lambda: httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": f"API key not valid: {KEY}"}})
    with TestClient(rig.app) as c:
        responses = [rig.say(c, "hello"), c.get("/status").json(), c.get("/health").json(), c.get("/tools").json()]
    assert KEY not in json.dumps(responses) and KEY not in caplog.text and KEY not in rig.audit.read_text()
    assert KEY not in json.dumps(rig.brain.status())


def test_the_audit_log_records_the_decision_not_which_brain_or_what_it_said(rig):
    rig.gemini_reply = lambda: gemini_ok(call("get_time"))
    with TestClient(rig.app) as c:
        rig.say(c, "what time is it? my secret is swordfish")
    log = rig.audit.read_text()
    assert "swordfish" not in log and "gemini" not in log.lower()


# ---- status ---------------------------------------------------------------------------------------------------------------------------------

def test_the_status_page_shows_gemini_and_the_fallback_story(rig):
    with TestClient(rig.app) as c:
        ready = c.get("/status").json()
        gem = next(x for x in ready["components"] if x["name"] == "Gemini")
        assert gem["state"] == "ok" and "gemini-test" in gem["detail"] and ready["brain"]["gemini"]["state"] == "ready"
        rig.gemini_reply = lambda: httpx.Response(429, json={})
        rig.say(c, "hello")
        cooling = c.get("/status").json()
    gem = next(x for x in cooling["components"] if x["name"] == "Gemini")
    assert gem["state"] == "warn" and "quota" in gem["detail"] and "local model is answering" in gem["detail"] and gem["fix"]
    assert cooling["brain"]["using"] == "ollama" and cooling["brain"]["gemini"]["retry_in_seconds"] == 300 and cooling["ready"] is True


def test_a_working_gemini_means_a_dead_local_model_is_only_degraded(rig):
    rig.local_down = True
    with TestClient(rig.app) as c:
        status = c.get("/status").json()
    assert status["ready"] is True and status["state"] == "degraded"
    assert {x["name"]: x["core"] for x in status["components"] if x["name"].startswith("language model")} == {"language model server": False, "language model": False}


def test_when_both_brains_are_out_the_assistant_is_down(rig):
    rig.local_down = True
    rig.gemini_reply = lambda: httpx.Response(429, json={})
    with TestClient(rig.app) as c:
        c.post("/route", json={"text": "hello"})
        status = c.get("/status").json()
    assert status["ready"] is False and status["state"] == "down"


def test_with_no_fallback_gemini_is_the_core_part(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, fallback=False)
    rig.gemini_reply = lambda: httpx.Response(401, json={"error": {"code": 401, "status": "UNAUTHENTICATED"}})
    with TestClient(rig.app) as c:
        c.post("/route", json={"text": "hello"})
        status = c.get("/status").json()
    gem = next(x for x in status["components"] if x["name"] == "Gemini")
    assert gem["state"] == "fail" and gem["core"] is True and "GEMINI_API_KEY" in gem["fix"] + gem["detail"]
    assert status["state"] == "down"
    local = next(x for x in status["components"] if x["name"] == "language model")
    assert local["state"] == "off" and "USE_LOCAL_FALLBACK=false" in local["detail"]


def test_with_gemini_off_the_status_says_everything_is_local(tmp_path):
    local = OllamaClient("http://127.0.0.1:11434", "qwen", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"version": "0.1", "models": [{"name": "qwen"}]})))
    brain = build_brain({}, load_settings({}, {}), local)
    components = {c.name: c for c in collect(model_checks=run_checks(local), guard=GuardrailManager(POLICY), brain=brain)}
    assert components["Gemini"].state == "off" and "local model does everything" in components["Gemini"].detail
    assert components["language model"].core is True


def test_with_a_missing_key_the_status_explains(tmp_path):
    local = OllamaClient("http://127.0.0.1:11434", "qwen", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"version": "0.1", "models": [{"name": "qwen"}]})))
    brain = build_brain({}, load_settings({}, {"USE_GEMINI": "true"}), local)
    gem = {c.name: c for c in collect(model_checks=run_checks(local), guard=GuardrailManager(POLICY), brain=brain)}["Gemini"]
    assert gem.state == "warn" and "no GEMINI_API_KEY" in gem.detail and ".env" in gem.fix and not gem.core


# ---- SAFE_MODE -----------------------------------------------------------------------------------------------------------------------------

BROWSER_TOOLS = ["open_chrome", "open_url", "search_chrome", "new_tab", "switch_tab", "click_text", "fill_field", "scroll_page",
                 "wait_for_page_load", "get_page_title", "get_current_url", "play_youtube_video"]


def test_safe_mode_makes_every_browser_action_ask_first(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, safe_mode=True)
    rig.gemini_reply = lambda: gemini_ok(call("search_chrome", query="AI tools"))
    with TestClient(rig.app) as c:
        asked = rig.say(c, "search for AI tools")
        assert asked["guard"]["outcome"] == "confirm" and "safe mode" in asked["guard"]["reason"] and rig.worker.names() == []
        done = c.post("/confirm", json={"confirmation_id": asked["guard"]["confirmation_id"], "approved": True}).json()
    assert done["result"]["status"] == "done" and rig.worker.names() == ["search"]


def test_without_safe_mode_browser_actions_run_directly(rig):
    rig.gemini_reply = lambda: gemini_ok(call("search_chrome", query="AI tools"))
    with TestClient(rig.app) as c:
        assert rig.say(c, "search")["guard"]["outcome"] == "allow"


def test_safe_mode_covers_every_browser_tool_and_nothing_else():
    guard = build_guard({"safety": {"safe_mode": True}})
    from backend.tools import Category
    expected = {t.name for t in build_registry().by_category(Category.BROWSER)}
    assert guard.safe_mode_tools == expected and set(BROWSER_TOOLS) <= expected and "open_app" not in expected
    assert build_guard({"safety": {}}).safe_mode_tools == frozenset()


def test_safe_mode_only_raises_the_level_never_lowers_it():
    from backend.toolcall import ToolCall
    guard = GuardrailManager(POLICY, safe_mode_tools=frozenset({"open_url", "close_tab", "run_shell"}))
    assert guard.evaluate(ToolCall("open_url", {"url": "https://example.com"})).outcome is Outcome.CONFIRM
    assert guard.evaluate(ToolCall("open_url", {"url": "javascript:alert(1)"})).outcome is Outcome.BLOCK       # still blocked
    close = guard.evaluate(ToolCall("close_tab", {"index": 1, "tab_id": 1, "tab": "x"}))
    assert close.outcome is Outcome.CONFIRM and close.risk.label == "medium"
    assert guard.evaluate(ToolCall("run_shell", {"command": "ls"})).outcome is Outcome.BLOCK
    assert guard.evaluate(ToolCall("open_app", {"app_name": "Safari"})).outcome is Outcome.ALLOW


def test_safe_mode_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("SAFE_MODE", "true")
    from backend.settings import apply_to_config
    cfg = apply_to_config({"safety": {}}, load_settings({}))
    assert build_guard(cfg).safe_mode_tools


# ---- speech engines ---------------------------------------------------------------------------------------------------------------------

def voice_for(**settings_env):
    from backend.settings import apply_to_config
    cfg = apply_to_config({"voice": {}}, load_settings({}, settings_env))
    return voice_service_from_config(cfg, ROOT)


def test_the_default_speech_path_is_local_whisper_then_piper_then_the_mac_voice():
    v = voice_for()
    assert v.stt.name == "whisper.cpp" and [s.name for s in v.synthesizers] == ["Piper", "the macOS voice"]


def test_piper_stays_the_default_even_if_google_is_asked_for():
    assert [s.name for s in voice_for(TTS_ENGINE="google").synthesizers][0] == "Piper"


def test_the_mac_voice_can_be_chosen_on_its_own():
    assert [s.name for s in voice_for(TTS_ENGINE="say").synthesizers] == ["the macOS voice"]


def test_without_the_system_voice_fallback_piper_is_alone():
    from backend.settings import apply_to_config
    cfg = apply_to_config({"voice": {"system_voice_fallback": False}}, load_settings({}))
    assert [s.name for s in voice_service_from_config(cfg, ROOT).synthesizers] == ["Piper"]


def test_macos_dictation_mode_explains_itself_instead_of_transcribing(tmp_path):
    v = voice_for(STT_ENGINE="macos")
    assert v.stt.name == "macOS Dictation"
    with pytest.raises(VoiceError) as e:
        v.transcribe(make_wav(1.0))
    assert e.value.code == "engine_missing" and "Dictation" in e.value.detail and "Fn" in e.value.detail
    status = v.status()["stt"]
    assert status["external"] is True and status["available"] is False


def test_the_mic_endpoint_tells_the_app_to_use_dictation(rig):
    rig.voice.stt = voice_for(STT_ENGINE="macos").stt
    with TestClient(rig.app) as c:
        res = c.post("/voice/transcribe", content=make_wav(1.0))
    assert res.status_code == 503 and "Dictation" in res.json()["detail"] and res.json()["code"] == "engine_missing"


def test_dictation_is_a_healthy_choice_not_a_warning_on_the_status_page(rig):
    rig.voice.stt = voice_for(STT_ENGINE="macos").stt
    with TestClient(rig.app) as c:
        comps = {x["name"]: x for x in c.get("/status").json()["components"]}
    assert comps["speech to text"]["state"] == "ok" and "macOS Dictation" in comps["speech to text"]["detail"]
    assert comps["text to speech"]["state"] == "ok"


# ---- start-up --------------------------------------------------------------------------------------------------------------------------------

def test_create_app_reads_the_settings_and_builds_the_brain(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("USE_GEMINI", "true")
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-env")
    monkeypatch.setenv("LOCAL_MODEL_NAME", "llama-env")
    monkeypatch.setenv("TTS_ENGINE", "google")
    monkeypatch.setenv("HOME", str(tmp_path))
    local = OllamaClient("http://127.0.0.1:11434", "qwen", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"version": "0", "models": []})))
    seen = {}

    def gemini_handler(request):
        seen["url"] = str(request.url)
        return gemini_ok(call("reply", text="env gemini"))

    import backend.brain as brain_module
    original = brain_module.GeminiClient
    monkeypatch.setattr(brain_module, "GeminiClient", lambda key, model, **kw: original(key, model, transport=httpx.MockTransport(gemini_handler), **kw))
    app = create_app({"ollama": {"model": "qwen"}, "router": {}, "brain": {"timeout_seconds": 3}}, local, guard=GuardrailManager(POLICY),
                     voice=VoiceService(FakeEngine("s"), [FakeEngine("p")]), actions=make_world(tmp_path).service)
    with TestClient(app) as c:
        body = c.post("/route", json={"text": "hello"}).json()
    assert body["brain"]["engine"] == "gemini" and "gemini-env:generateContent" in seen["url"]
    assert "TTS_ENGINE=google isn't built yet" in caplog.text and KEY not in caplog.text


def test_with_nothing_set_create_app_is_the_same_local_only_assistant_as_before(tmp_path):
    local = OllamaClient("http://127.0.0.1:11434", "qwen", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"message": {"content": call("reply", text="local")}, "version": "0", "models": [{"name": "qwen"}]})))
    app = create_app({"ollama": {"model": "qwen"}, "router": {}}, local, guard=GuardrailManager(POLICY),
                     voice=VoiceService(FakeEngine("s"), [FakeEngine("p")]), actions=make_world(tmp_path).service)
    with TestClient(app) as c:
        body = c.post("/route", json={"text": "hi"}).json()
        status = c.get("/status").json()
    assert body["brain"] == {"engine": "ollama", "notice": "", "speak": False}
    gem = next(x for x in status["components"] if x["name"] == "Gemini")
    assert gem["state"] == "off" and status["brain"]["gemini"]["state"] == "off"
