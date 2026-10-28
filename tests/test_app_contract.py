"""The SwiftUI app decodes the backend's JSON. These fixtures are shared with the Swift tests, so if the
backend's response shape changes this test fails until the fixtures (and the Swift models) are updated.

Regenerate with:  UPDATE_APP_FIXTURES=1 .venv/bin/pytest tests/test_app_contract.py
"""
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import ROOT
from backend.llm import OllamaClient
from backend.main import create_app
from safety import GuardrailManager, Policy

FIXTURES = ROOT / "app" / "Tests" / "Fixtures"
POLICY = Policy.load(ROOT / "safety" / "policy.yaml")
FIXED_ID = "abc123def456"


def tool_json(tool, **args):
    return json.dumps({"tool": tool, "args": args, "requires_confirmation": False})


def client_for(model_output, ollama_down=False):
    def handler(request: httpx.Request) -> httpx.Response:
        if ollama_down:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"role": "assistant", "content": model_output}})
        return httpx.Response(200, json={"version": "0", "models": [{"name": "m"}]})

    llm = OllamaClient("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(handler))
    cfg = {"ollama": {}, "router": {"max_retries": 0}}
    return TestClient(create_app(cfg, llm, guard=GuardrailManager(POLICY)))


def normalize(body):
    """Confirmation ids are random; fixtures use a fixed one."""
    if isinstance(body, dict):
        for key, value in body.items():
            if key == "confirmation_id" and value:
                body[key] = FIXED_ID
            else:
                normalize(value)
    return body


def scenarios():
    out = {}
    with client_for(tool_json("open_app", app_name="Google Chrome")) as c:
        out["route_allow_open_app"] = c.post("/route", json={"text": "open chrome"}).json()
    with client_for(tool_json("reply", text="Hello there.")) as c:
        out["route_reply"] = c.post("/route", json={"text": "hi"}).json()
    with client_for(tool_json("close_app", app_name="Safari")) as c:
        route = c.post("/route", json={"text": "quit safari"}).json()
        out["route_confirm_close_app"] = route
        out["confirm_allow"] = c.post("/confirm", json={
            "confirmation_id": route["guard"]["confirmation_id"], "approved": True}).json()
        out["confirm_expired"] = c.post("/confirm", json={
            "confirmation_id": route["guard"]["confirmation_id"], "approved": True}).json()
        out["route_dry_run"] = c.post("/route", json={"text": "quit safari", "dry_run": True}).json()
    with client_for(tool_json("open_folder", path_or_name="~/.ssh")) as c:
        out["route_confirm_high_risk"] = c.post("/route", json={"text": "open ssh"}).json()
    with client_for(tool_json("open_url", url="javascript:alert(1)")) as c:
        out["route_block_url"] = c.post("/route", json={"text": "go there"}).json()
    with client_for("not json at all") as c:
        out["route_fallback"] = c.post("/route", json={"text": "???"}).json()
    with client_for("", ollama_down=True) as c:
        res = c.post("/route", json={"text": "hi"})
        assert res.status_code == 503
        out["error_503"] = res.json()
    return {name: normalize(body) for name, body in out.items()}


def test_fixtures_match_the_backend():
    actual = scenarios()
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


def test_the_fields_the_app_decodes_are_present():
    """Mirrors the Codable models in app/Sources/CapsuleCore/Models.swift."""
    route_keys = {"tool", "args", "requires_confirmation", "fallback", "note", "attempts", "guard"}
    guard_keys = {"outcome", "authorized", "risk", "reason", "message", "tool", "args",
                  "confirmation_id", "requires_acknowledgment"}
    outcomes = {"allow", "confirm", "block", "dry_run"}
    for name, body in scenarios().items():
        if name == "error_503":
            assert set(body) == {"detail"}
            continue
        decision = body["guard"] if name.startswith("route_") else body
        if name.startswith("route_"):
            assert set(body) == route_keys, name
        assert set(decision) == guard_keys, name
        assert decision["outcome"] in outcomes, name
