"""All the parts together, through the real HTTP API: one scripted session that uses the voice engines, the model
(scripted), the tool router, the safety layer, apps and files, the browser, reminders, notes, memory and the organizer.
Only the outside world is replaced: Ollama, Chrome, whisper/Piper, and the Mac itself."""
import json
import os
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from automation.choices import ChoiceStore
from backend.actions import ActionRouter
from backend.config import ROOT
from backend.llm import OllamaClient
from backend.main import create_app, startup_problem
from browserctl import BrowserService
from fake_browser import FakeWorker, cand
from fakes import make_world
from memory import MemoryService
from memory.scheduler import ReminderScheduler
from memory.store import MemoryStore
from organizer import OrganizerService
from organizer.movelog import MoveLog
from safety import AuditLog, GuardrailManager, Policy
from safety.policy import PolicyError
from test_memory import FakeNotifier
from test_voice import FakeEngine, make_wav
from voice import VoiceService

POLICY = Policy.load(ROOT / "safety" / "policy.yaml")
NOW = datetime(2026, 10, 29, 14, 0, 0)


def call(tool, **args):
    return json.dumps({"tool": tool, "args": args, "requires_confirmation": False})


class Model:
    """A stand-in for Ollama that answers by what the user said, and can be told to misbehave."""

    def __init__(self):
        self.answers: dict[str, str] = {}
        self.prompts: list[str] = []          # the system prompts it was sent
        self.failures: list[Exception | int] = []      # consumed one per call, before answering
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/chat":
            return httpx.Response(200, json={"version": "0.1", "models": [{"name": "m"}]})
        self.calls += 1
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, int):
                return httpx.Response(failure, json={"error": "hiccup"})
            raise failure
        messages = json.loads(request.content)["messages"]
        self.prompts.append(messages[0]["content"])
        said = next(m["content"] for m in messages if m["role"] == "user")      # the request, not a corrective retry message
        content = self.answers.get(said, call("reply", text="I didn't understand."))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


class System:
    def __init__(self, tmp_path, monkeypatch):
        self.model = Model()
        self.world = make_world(tmp_path / "mac")
        self.home = self.world.home
        monkeypatch.setenv("HOME", str(self.home))
        choices = ChoiceStore()
        self.world.service.choices = choices
        self.worker = FakeWorker()
        self.browser = BrowserService(self.worker, choices=choices)
        self.notifier = FakeNotifier()
        self.store = MemoryStore(tmp_path / "data" / "assistant.db")
        self.scheduler = ReminderScheduler(self.store, self.notifier, clock=lambda: NOW)
        self.memory = MemoryService(self.store, self.notifier, self.scheduler, choices=choices, clock=lambda: NOW, notifications=False)
        self.organizer = OrganizerService(MoveLog(tmp_path / "data" / "moves.jsonl"), files=self.world.service.files,
                                          home=self.home, clock=lambda: NOW)
        self.router = ActionRouter(self.world.service, self.browser, choices, self.memory, self.organizer)
        self.stt = FakeEngine("whisper", wav=b"open chrome")
        self.tts = FakeEngine("Piper", wav=make_wav(0.3))
        self.voice = VoiceService(self.stt, [self.tts])
        self.audit = tmp_path / "audit.jsonl"
        self.guard = GuardrailManager(POLICY, AuditLog(self.audit))
        self.llm = OllamaClient("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(self.model.handler), retries=1,
                                retry_delay=0, sleep=lambda s: None)
        self.app = create_app({"ollama": {}, "router": {"max_retries": 1}}, self.llm, guard=self.guard, voice=self.voice, actions=self.router)

    def say(self, client, text, **extra):
        return client.post("/route", json={"text": text, **extra}).json()


@pytest.fixture
def sys_(tmp_path, monkeypatch):
    return System(tmp_path, monkeypatch)


def make_old(path, text="x"):
    path.write_text(text)
    when = (NOW - timedelta(days=2)).timestamp()
    os.utime(path, (when, when))


# ---- one session through every module ---------------------------------------------------------------------

def test_a_whole_session_across_all_the_modules(sys_):
    m = sys_.model
    m.answers = {
        "open chrome": call("open_app", app_name="Google Chrome"),
        "remember I prefer Chrome": call("set_preference", key="preferred browser", value="Chrome"),
        "search for AI tools": call("search_chrome", query="AI tools"),
        "remind me at 6 PM to call mom": call("create_reminder", text="call mom", due_datetime="2026-10-29T18:00:00"),
        "make a note about my project": call("save_note", title="My project", content="Build the assistant."),
        "what are my reminders": call("list_reminders"),
        "plan my day": call("plan_my_day"),
        "organize my desktop": json.dumps({"tool": "organize_desktop", "args": {}, "requires_confirmation": True}),
        "hello": call("reply", text="Hi there."),
    }
    make_old(sys_.home / "Desktop" / "report.pdf", "doc")
    with TestClient(sys_.app) as c:
        # status: everything is wired and reported
        status = c.get("/status").json()
        assert status["ready"] is True and status["state"] == "degraded"             # Hammerspoon is not running in this test
        names = {x["name"]: x["state"] for x in status["components"]}
        assert names["language model"] == "ok" and names["safety rules"] == "ok" and names["memory"] == "ok"
        assert names["reminders"] == "ok" and names["browser"] == "ok" and names["organizer"] == "ok"
        assert names["speech to text"] == "ok" and names["text to speech"] == "ok" and names["Hammerspoon"] == "warn"

        # voice in -> model -> router -> safety -> app tool -> voice out
        heard = c.post("/voice/transcribe", content=make_wav(1.0)).json()["text"]
        opened = sys_.say(c, heard)
        assert opened["guard"]["outcome"] == "allow" and opened["result"]["status"] == "done"
        assert sys_.world.run.commands("open")                                        # the (pretend) Mac was asked to open Chrome
        spoken = c.post("/voice/speak", json={"text": opened["result"]["message"]})
        assert spoken.status_code == 200 and spoken.headers["content-type"] == "audio/wav" and sys_.tts.heard == ["Opened Google Chrome."]

        # memory: a preference is saved, and shows up in the next prompt
        saved = sys_.say(c, "remember I prefer Chrome")
        assert saved["result"]["message"] == "Got it. I'll remember that your preferred browser is Chrome."
        sys_.say(c, "hello")
        assert "Remembered about the user" not in m.prompts[0] and "preferred browser: Chrome" in m.prompts[-1]

        # browser: a search, through the safety layer, to the worker
        found = sys_.say(c, "search for AI tools")
        assert found["result"]["message"] == "Searched Google for “AI tools”." and sys_.worker.last("search") == {"query": "AI tools"}

        # reminders + notes + the day plan
        made = sys_.say(c, "remind me at 6 PM to call mom")
        assert made["result"]["message"].startswith("I'll remind you to call mom today 6:00 PM.")
        assert sys_.say(c, "make a note about my project")["result"]["message"] == "Saved the note “My project”."
        assert "call mom" in sys_.say(c, "what are my reminders")["result"]["message"]
        assert "1 reminder" in sys_.say(c, "plan my day")["result"]["message"]

        # the reminder goes off when it is due
        sys_.scheduler.check(datetime(2026, 10, 29, 18, 0, 5))
        assert sys_.notifier.sent == [("Reminder", "call mom")]

        # the organizer: preview, then confirm, then the file is moved
        asked = sys_.say(c, "organize my desktop")
        assert asked["guard"]["outcome"] == "confirm" and asked["guard"]["message"].startswith("In ~/Desktop I would move 1 file")
        assert (sys_.home / "Desktop" / "report.pdf").exists()
        done = c.post("/confirm", json={"confirmation_id": asked["guard"]["confirmation_id"], "approved": True}).json()
        assert done["result"]["status"] == "done" and (sys_.home / "Desktop" / "Documents" / "report.pdf").exists()

        # what happened is on record, and the typed commands were remembered
        events = [json.loads(line) for line in sys_.audit.read_text().splitlines()]
        assert {e["tool"] for e in events if e["stage"] == "execute"} >= {"open_app", "set_preference", "search_chrome", "create_reminder",
                                                                          "save_note", "organize_desktop"}
        assert sys_.store.history(50)[0]["tool"] == "organize_desktop" and sys_.store.history(50)[-1]["text"] == "open chrome"


def test_safety_holds_across_the_whole_system(sys_):
    sys_.model.answers = {
        "go to that link": call("open_url", url="javascript:alert(1)"),
        "close safari": call("close_app", app_name="Safari"),
        "run ls": call("run_shell", command="ls"),
        "type my password": call("fill_field", label="password", value="hunter2"),
        "remember my pin": call("set_preference", key="bank pin", value="1234"),
        "organize": json.dumps({"tool": "organize_desktop", "args": {}, "requires_confirmation": True}),
    }
    make_old(sys_.home / "Desktop" / "a.pdf")
    sys_.worker.do_open_chrome()
    sys_.worker.fields = [{"n": 0, "label": "Password", "score": 1.0, "type": "password", "kind": "password"}]
    with TestClient(sys_.app) as c:
        blocked = sys_.say(c, "go to that link")
        assert blocked["guard"]["outcome"] == "block" and blocked["result"] is None and sys_.worker.names().count("open_url") == 0
        ask = sys_.say(c, "close safari")
        assert ask["guard"]["outcome"] == "confirm" and not sys_.world.run.commands("osascript")
        unknown = sys_.say(c, "run ls")
        assert unknown["fallback"] is True and unknown["tool"] == "reply"                  # a tool that doesn't exist can't be chosen
        assert sys_.say(c, "type my password")["guard"]["outcome"] == "block" and "fill" not in sys_.worker.names()
        assert sys_.say(c, "remember my pin")["guard"]["outcome"] == "block" and sys_.store.preferences() == []
        dry = sys_.say(c, "organize", dry_run=True)
        assert dry["guard"]["outcome"] == "dry_run" and (sys_.home / "Desktop" / "a.pdf").exists()


# ---- errors, retries, timeouts -------------------------------------------------------------------------------

def test_the_model_being_down_is_a_clear_error_and_the_assistant_recovers(sys_):
    sys_.model.answers = {"hello": call("reply", text="Hi.")}
    with TestClient(sys_.app) as c:
        sys_.model.failures = [httpx.ConnectError("refused"), httpx.ConnectError("refused")]
        down = c.post("/route", json={"text": "hello"})
        assert down.status_code == 503 and "Ollama" in down.json()["detail"] and sys_.model.calls == 2        # tried, waited, tried again
        assert sys_.say(c, "hello")["result"] is None and sys_.say(c, "hello")["tool"] == "reply"               # back to normal


def test_a_hiccup_in_the_model_is_retried_once_so_the_user_never_sees_it(sys_):
    sys_.model.answers = {"hello": call("reply", text="Hi.")}
    with TestClient(sys_.app) as c:
        sys_.model.failures = [503]
        assert c.post("/route", json={"text": "hello"}).status_code == 200
        sys_.model.failures = [httpx.ConnectError("refused")]
        assert c.post("/route", json={"text": "hello"}).status_code == 200


def test_a_dropped_connection_is_retried_like_any_hiccup(sys_):
    sys_.model.answers = {"hello": call("reply", text="Hi.")}
    with TestClient(sys_.app) as c:
        sys_.model.failures = [httpx.RemoteProtocolError("server disconnected")]
        assert c.post("/route", json={"text": "hello"}).status_code == 200


def test_a_timeout_is_reported_not_retried(sys_):
    with TestClient(sys_.app) as c:
        sys_.model.failures = [httpx.ReadTimeout("slow")]
        before = sys_.model.calls
        res = c.post("/route", json={"text": "hello"})
        assert res.status_code == 502 and "timed out" in res.json()["detail"] and sys_.model.calls == before + 1


def test_a_missing_model_is_a_clear_error(sys_):
    with TestClient(sys_.app) as c:
        sys_.model.failures = [404]
        res = c.post("/route", json={"text": "hello"})
        assert res.status_code == 502


def test_nonsense_from_the_model_becomes_a_polite_reply(sys_):
    sys_.model.answers = {"hello": "this is not json"}
    with TestClient(sys_.app) as c:
        body = sys_.say(c, "hello")
        assert body["fallback"] is True and "rephrase" in body["guard"]["args"]["text"]


def test_an_unexpected_bug_gives_a_plain_error_and_the_next_request_works(sys_, monkeypatch):
    sys_.model.answers = {"list": call("list_tasks"), "hello": call("reply", text="Hi.")}
    monkeypatch.setattr(sys_.store, "tasks", lambda **k: (_ for _ in ()).throw(RuntimeError("secret internals /Users/x")))
    with TestClient(sys_.app, raise_server_exceptions=False) as c:
        res = sys_.say(c, "list")
        assert res["result"]["status"] == "failed" and "secret" not in json.dumps(res)        # the memory service answers politely
        monkeypatch.setattr(sys_.router, "prepare", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret internals /Users/x")))
        crash = c.post("/route", json={"text": "list"})
        assert crash.status_code == 500 and crash.json() == {"detail": "The assistant hit an unexpected problem. Nothing else was changed."}
        monkeypatch.undo()
        assert c.get("/health").status_code == 200


def test_a_failing_browser_helper_leaves_everything_else_working(sys_):
    sys_.model.answers = {"search": call("search_chrome", query="x"), "note": call("save_note", title="t", content="c")}
    from browserctl.client import WorkerUnavailable
    sys_.worker.errors["search"] = WorkerUnavailable("The browser helper isn't installed yet. Run this once:  cd browser && npm install")
    with TestClient(sys_.app) as c:
        browser = sys_.say(c, "search")
        assert browser["result"]["status"] == "not_available" and "npm install" in browser["result"]["message"]
        assert sys_.say(c, "note")["result"]["status"] == "done"


def test_a_missing_voice_engine_does_not_stop_typing(sys_):
    sys_.stt.availability = lambda: type("A", (), {"available": False, "reason": "model missing", "fix": "download it",
                                                   "to_dict": lambda self: {"available": False, "reason": "model missing", "fix": "download it"}})()
    sys_.model.answers = {"hello": call("reply", text="Hi.")}
    with TestClient(sys_.app) as c:
        states = {x["name"]: x["state"] for x in c.get("/status").json()["components"]}
        assert states["speech to text"] == "warn" and states["text to speech"] == "ok"
        assert sys_.say(c, "hello")["tool"] == "reply"


# ---- shutdown ---------------------------------------------------------------------------------------------------

def test_shutdown_stops_the_reminder_watcher_and_the_browser_helper(sys_):
    stopped = []
    sys_.browser.launcher = type("L", (), {"ensure": lambda s: None, "stop": lambda s: stopped.append("browser"),
                                           "diagnose": lambda s: None})()
    with TestClient(sys_.app):
        assert sys_.scheduler.running
    assert not sys_.scheduler.running and stopped == ["browser"]


def test_one_part_failing_to_stop_does_not_leave_the_others_running(sys_):
    class Bad:
        def stop(self): raise RuntimeError("cannot stop")
    sys_.browser.launcher = Bad()
    with TestClient(sys_.app):
        assert sys_.scheduler.running
    assert not sys_.scheduler.running                                   # the watcher still stopped


def test_a_failure_while_stopping_the_helpers_still_closes_the_model_connection(sys_):
    closed = []
    real_close = sys_.llm.close
    sys_.llm.close = lambda: (closed.append("llm"), real_close())
    sys_.router.close = lambda: (_ for _ in ()).throw(RuntimeError("cannot stop"))
    try:
        with TestClient(sys_.app):
            pass                                                # leaving must not raise
    finally:
        sys_.scheduler.stop()                                   # (the broken close above skipped it; don't leak the thread)
    assert closed == ["llm"]


def test_shutdown_works_even_if_nothing_was_ever_used(sys_):
    with TestClient(sys_.app):
        pass
    assert not sys_.scheduler.running


# ---- starting up ------------------------------------------------------------------------------------------------

def test_startup_problems_are_explained_in_plain_words(tmp_path):
    import yaml
    assert "safety/policy.yaml" in startup_problem(PolicyError("policy has no rules"))
    assert "not valid YAML" in startup_problem(yaml.YAMLError("bad"))
    assert "missing the setting 'ollama'" in startup_problem(KeyError("ollama"))
    assert "can't be read" in startup_problem(FileNotFoundError(2, "No such file or directory", "config.yaml"))
    assert startup_problem(ValueError("ollama.url must be a local address")) == "ollama.url must be a local address"


def test_main_stops_with_a_clear_message_when_the_config_is_broken(monkeypatch):
    import backend.main as main_module
    monkeypatch.setattr(main_module, "load_config", lambda: {"backend": {"host": "127.0.0.1", "port": 1}})
    with pytest.raises(SystemExit) as stop:
        main_module.main()
    assert "could not start" in str(stop.value) and "ollama" in str(stop.value)


def test_main_refuses_a_network_address(monkeypatch):
    import backend.main as main_module
    monkeypatch.setattr(main_module, "load_config", lambda: {"backend": {"host": "0.0.0.0", "port": 1}})
    with pytest.raises(SystemExit) as stop:
        main_module.main()
    assert "127.0.0.1" in str(stop.value)


def test_main_starts_the_server_with_a_graceful_shutdown(monkeypatch, sys_):
    import backend.main as main_module
    seen = {}
    monkeypatch.setattr(main_module, "load_config", lambda: {"backend": {"host": "127.0.0.1", "port": 8765}})
    monkeypatch.setattr(main_module, "create_app", lambda cfg, settings=None: sys_.app)
    monkeypatch.setattr(main_module.uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    main_module.main()
    assert seen["app"] is sys_.app and seen["host"] == "127.0.0.1" and seen["port"] == 8765 and seen["timeout_graceful_shutdown"] == 5


def test_importing_the_backend_builds_nothing():
    import importlib
    import backend.main as main_module
    assert "app" not in vars(main_module)              # created only when asked for
    importlib.reload(main_module)
    assert "app" not in vars(main_module)
