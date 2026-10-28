"""The SwiftUI app decodes the backend's JSON. These fixtures are shared with the Swift tests, so if the
backend's response shape changes this test fails until the fixtures (and the Swift models) are updated.

Regenerate with:  UPDATE_APP_FIXTURES=1 .venv/bin/pytest tests/test_app_contract.py
"""
import json
import os

import httpx
from fastapi.testclient import TestClient

from backend.config import ROOT
from backend.llm import OllamaClient
from backend.main import create_app
from automation.choices import ChoiceStore
from backend.actions import ActionRouter
from browserctl import BrowserService
from fake_browser import FakeWorker, cand
from fakes import APPS, make_world
from safety import GuardrailManager, Policy
from test_voice import FakeEngine, make_wav
from voice import VoiceError, VoiceService

FIXTURES = ROOT / "app" / "Tests" / "Fixtures"
POLICY = Policy.load(ROOT / "safety" / "policy.yaml")
FIXED_ID = "abc123def456"
FIXED_CHOICE = "c0ffee123456"


def tool_json(tool, **args):
    return json.dumps({"tool": tool, "args": args, "requires_confirmation": False})


class Scene:
    """A pretend Mac plus a backend wired to it."""

    def __init__(self, tmp_path, apps=APPS):
        self.world = make_world(tmp_path, apps=apps)
        self.home = self.world.home

    def browser(self, worker):
        """The desktop service plus a browser service backed by a pretend worker, sharing one choice store."""
        choices = ChoiceStore()
        self.world.service.choices = choices
        return ActionRouter(self.world.service, BrowserService(worker, choices=choices), choices)

    def client(self, model_output, ollama_down=False, voice=None, actions=None):
        def handler(request: httpx.Request) -> httpx.Response:
            if ollama_down:
                raise httpx.ConnectError("refused", request=request)
            if request.url.path == "/api/chat":
                return httpx.Response(200, json={"message": {"role": "assistant", "content": model_output}})
            return httpx.Response(200, json={"version": "0", "models": [{"name": "m"}]})

        llm = OllamaClient("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(handler))
        cfg = {"ollama": {}, "router": {"max_retries": 0}}
        return TestClient(create_app(cfg, llm, guard=GuardrailManager(POLICY), voice=voice, actions=actions or self.world.service))

    def clean(self, body):
        """Random ids and temporary folders are replaced by fixed values, so fixtures are stable."""
        text = json.dumps(body)
        text = text.replace(str(self.world.home), "/Users/me").replace(str(self.world.apps_dir), "/Applications")
        body = json.loads(text)

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "confirmation_id" and value:
                        node[key] = FIXED_ID
                    elif key == "choice_id" and value:
                        node[key] = FIXED_CHOICE
                    else:
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(body)
        return body


def write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def scenarios(tmp_path):
    out = {}
    scene = Scene(tmp_path / "a")
    docs = scene.home / "Documents"

    with scene.client(tool_json("open_app", app_name="Google Chrome")) as c:
        out["route_allow_open_app"] = c.post("/route", json={"text": "open chrome"}).json()
    with scene.client(tool_json("reply", text="Hello there.")) as c:
        out["route_reply"] = c.post("/route", json={"text": "hi"}).json()
    with scene.client(tool_json("close_app", app_name="Safari")) as c:
        route = c.post("/route", json={"text": "quit safari"}).json()
        out["route_confirm_close_app"] = route
        out["confirm_done"] = c.post("/confirm", json={
            "confirmation_id": route["guard"]["confirmation_id"], "approved": True}).json()
        out["confirm_expired"] = c.post("/confirm", json={
            "confirmation_id": route["guard"]["confirmation_id"], "approved": True}).json()
        out["route_dry_run"] = c.post("/route", json={"text": "quit safari", "dry_run": True}).json()
    with scene.client(tool_json("open_folder", path_or_name="/etc")) as c:
        out["route_confirm_high_risk"] = c.post("/route", json={"text": "open etc"}).json()
    with scene.client(tool_json("open_url", url="javascript:alert(1)")) as c:
        out["route_block_url"] = c.post("/route", json={"text": "go there"}).json()
    with scene.client("not json at all") as c:
        out["route_fallback"] = c.post("/route", json={"text": "???"}).json()
    with scene.client("", ollama_down=True) as c:
        res = c.post("/route", json={"text": "hi"})
        assert res.status_code == 503
        out["error_503"] = res.json()

    # actions: a missing app, an unbuilt tool, a search, an exact open
    with scene.client(tool_json("open_app", app_name="Photoshop")) as c:
        out["route_not_found"] = c.post("/route", json={"text": "open photoshop"}).json()
    with scene.client(tool_json("create_reminder", text="call mom", due_datetime="2025-01-01T18:00:00")) as c:
        out["route_not_available"] = c.post("/route", json={"text": "remind me"}).json()
    write(docs / "budget.pdf")
    write(docs / "budget old.pdf")
    with scene.client(tool_json("search_files", query="budget")) as c:
        out["route_search"] = c.post("/route", json={"text": "find budget"}).json()
    with scene.client(tool_json("open_file", path_or_name="budget.pdf")) as c:
        out["route_open_file"] = c.post("/route", json={"text": "open budget.pdf"}).json()

    # "which one did you mean?"
    other = Scene(tmp_path / "b")
    for folder in ("Documents", "Downloads"):
        write(other.home / folder / "report.pdf")
    with other.client(tool_json("open_file", path_or_name="report.pdf")) as c:
        asked = c.post("/route", json={"text": "open report"}).json()
        out["route_choose_file"] = asked
        choice = asked["result"]["choice"]
        pick = next(o["id"] for o in choice["options"] if o["detail"] == "~/Documents")
        out["choose_done"] = c.post("/choose", json={"choice_id": choice["choice_id"], "option_id": pick}).json()
        out["choose_expired"] = c.post("/choose", json={"choice_id": choice["choice_id"], "option_id": pick}).json()

    # browser: a search, a sign-in page, and a risky button
    with scene.client(tool_json("search_chrome", query="AI tools"), actions=scene.browser(FakeWorker())) as c:
        out["route_browser_search"] = c.post("/route", json={"text": "open chrome and search for AI tools"}).json()
    login_worker = FakeWorker(login_hosts={"mail.google.com"})
    with scene.client(tool_json("open_url", url="gmail"), actions=scene.browser(login_worker)) as c:
        out["route_needs_login"] = c.post("/route", json={"text": "open my gmail"}).json()
    click_worker = FakeWorker()
    click_worker.do_open_chrome()
    click_worker.buttons = [cand("Send")]
    with scene.client(tool_json("click_text", text="send"), actions=scene.browser(click_worker)) as c:
        out["route_browser_confirm_click"] = c.post("/route", json={"text": "click send"}).json()

    heard = VoiceService(FakeEngine("stt", wav=b"open chrome"), [FakeEngine("Piper", wav=make_wav(0.2))])
    with scene.client("", voice=heard) as c:
        out["voice_transcribe"] = c.post("/voice/transcribe", content=make_wav(1.0)).json()
    silent = VoiceService(FakeEngine("stt", wav=b""), [])
    with scene.client("", voice=silent) as c:
        out["voice_transcribe_empty"] = c.post("/voice/transcribe", content=make_wav(1.0)).json()
    broken = VoiceService(
        FakeEngine("stt", error=VoiceError("model_missing", "The speech model is missing.", "Download one.")), [])
    with scene.client("", voice=broken) as c:
        res = c.post("/voice/transcribe", content=make_wav(1.0))
        assert res.status_code == 503
        out["voice_error_503"] = res.json()

    second = {"route_choose_file", "choose_done", "choose_expired"}
    return {name: (other if name in second else scene).clean(body) for name, body in out.items()}


def test_fixtures_match_the_backend(tmp_path):
    actual = scenarios(tmp_path)
    if os.environ.get("UPDATE_APP_FIXTURES"):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        for name, body in actual.items():
            (FIXTURES / f"{name}.json").write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    for name, body in actual.items():
        path = FIXTURES / f"{name}.json"
        assert path.exists(), f"missing fixture {name}; run with UPDATE_APP_FIXTURES=1"
        assert json.loads(path.read_text()) == body, f"fixture {name} is out of date"
    extra = {p.stem for p in FIXTURES.glob("*.json")} - set(actual)
    assert not extra, f"unused fixtures: {sorted(extra)}"


def test_the_fields_the_app_decodes_are_present(tmp_path):
    """Mirrors the Codable models in app/Sources/CapsuleCore/Models.swift."""
    envelope_keys = {"tool", "args", "requires_confirmation", "fallback", "note", "attempts", "guard", "result"}
    guard_keys = {"outcome", "authorized", "risk", "reason", "message", "tool", "args",
                  "confirmation_id", "requires_acknowledgment"}
    result_keys = {"status", "message", "items", "choice", "via"}
    choice_keys = {"choice_id", "prompt", "options"}
    option_keys = {"id", "label", "detail"}
    outcomes = {"allow", "confirm", "block", "dry_run"}
    statuses = {"done", "failed", "not_available", "choose", "needs_login"}

    for name, body in scenarios(tmp_path).items():
        if name == "error_503":
            assert set(body) == {"detail"}
            continue
        if name.startswith("voice_"):
            assert set(body) in ({"text"}, {"detail", "code"}), name
            continue
        if name.startswith("route_") or name.startswith("choose_"):
            assert set(body) == envelope_keys, name
            decision, result = body["guard"], body["result"]
        else:    # /confirm answers with the decision plus the result
            assert set(body) == guard_keys | {"result"}, name
            decision, result = {k: v for k, v in body.items() if k != "result"}, body["result"]
        if decision is not None:
            assert set(decision) == guard_keys, name
            assert decision["outcome"] in outcomes, name
        if result is not None:
            assert set(result) == result_keys, name
            assert result["status"] in statuses, name
            assert isinstance(result["items"], list), name
            if result["status"] == "choose":
                assert set(result["choice"]) == choice_keys and decision is None, name
                assert all(set(o) == option_keys for o in result["choice"]["options"]), name
            else:
                assert result["choice"] is None, name


def test_a_done_result_always_comes_with_an_authorizing_decision(tmp_path):
    """The app relies on this: "done" is never shown unless the safety layer said yes."""
    for name, body in scenarios(tmp_path).items():
        result = body.get("result")
        if not result or name.startswith("voice_"):
            continue
        decision = body["guard"] if "guard" in body else {k: v for k, v in body.items() if k != "result"}
        if result["status"] in ("done", "not_available", "needs_login"):
            assert decision is not None and decision["authorized"] is True, name
        if result["status"] == "choose":
            assert decision is None, name
