import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import ROOT
from backend.llm import ModelNotFound, OllamaClient, OllamaError, OllamaUnavailable
from backend.main import create_app
from backend.startup import all_ok, model_installed, run_checks
from fakes import quick_actions
from safety import GuardrailManager, Policy

POLICY = Policy.load(ROOT / "safety" / "policy.yaml")
CFG = {
    "ollama": {"url": "http://127.0.0.1:11434", "model": "qwen2.5:3b", "keep_alive": "5m", "num_ctx": 2048},
    "router": {"max_retries": 1},
}
TOOL_REPLY = json.dumps({"tool": "open_app", "args": {"app_name": "Google Chrome"}, "requires_confirmation": False})


def chat_body(content, **extra):
    return {"message": {"role": "assistant", "content": content}, **extra}


def make_client(handler, model="qwen2.5:3b"):
    return OllamaClient("http://127.0.0.1:11434", model, options={"num_ctx": 2048},
                        transport=httpx.MockTransport(handler))


def ollama(models=("qwen2.5:3b",), chat=None):
    """A fake Ollama server."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.0"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        if request.url.path == "/api/chat":
            return chat(request) if chat else httpx.Response(200, json=chat_body(TOOL_REPLY))
        return httpx.Response(404)
    return handler


# ---- client ------------------------------------------------------------------

def test_client_only_accepts_local_urls():
    for url in ("https://api.example.com", "http://192.168.1.10:11434", "http://0.0.0.0:11434"):
        with pytest.raises(ValueError):
            OllamaClient(url, "m")


def test_chat_request_and_stats():
    seen = {}

    def chat(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=chat_body("{}", eval_count=20, eval_duration=2_000_000_000))

    res = make_client(ollama(chat=chat)).chat([{"role": "user", "content": "hi"}])
    assert seen["model"] == "qwen2.5:3b" and seen["format"] == "json" and seen["stream"] is False
    assert seen["keep_alive"] == "5m" and seen["options"] == {"num_ctx": 2048}
    assert res.content == "{}" and res.tokens_per_second == 10


def test_version_and_models():
    c = make_client(ollama(models=("a:1", "b:2")))
    assert c.version() == "0.5.0" and c.list_models() == ["a:1", "b:2"]


def test_error_mapping():
    def raises(exc):
        def handler(request):
            raise exc(request=request, message="x") if exc is not httpx.ConnectError else exc("x", request=request)
        return handler

    with pytest.raises(OllamaUnavailable):
        make_client(raises(httpx.ConnectError)).version()
    with pytest.raises(OllamaUnavailable):
        make_client(raises(httpx.ConnectTimeout)).version()
    with pytest.raises(OllamaError, match="timed out"):
        make_client(raises(httpx.ReadTimeout)).version()
    not_found = lambda r: httpx.Response(404, json={"error": "model 'x' not found"})  # noqa: E731
    with pytest.raises(ModelNotFound):
        make_client(not_found).chat([])
    with pytest.raises(OllamaError, match="500"):
        make_client(lambda r: httpx.Response(500, text="boom")).version()
    with pytest.raises(OllamaError, match="non-JSON"):
        make_client(lambda r: httpx.Response(200, text="<html>")).version()
    with pytest.raises(OllamaError, match="no message"):
        make_client(lambda r: httpx.Response(200, json={"done": True})).chat([])


# ---- startup checks ----------------------------------------------------------

def test_model_name_matching():
    assert model_installed("llama3.1", ["llama3.1:latest"])
    assert model_installed("llama3.1:latest", ["llama3.1"])
    assert model_installed("qwen2.5:3b", ["qwen2.5:3b"])
    assert not model_installed("qwen2.5:3b", ["qwen2.5:7b", "qwen2.5:1.5b"])


def names(results):
    return {r.name: r for r in results}


def test_checks_pass_when_everything_is_ready():
    results = run_checks(make_client(ollama()), which=lambda _: "/usr/local/bin/ollama")
    assert all_ok(results)


def test_checks_report_a_missing_model_with_the_fix():
    r = names(run_checks(make_client(ollama(models=("llama3.1:8b",))), which=lambda _: "/bin/ollama"))
    assert not r["model"].ok and "ollama pull qwen2.5:3b" in r["model"].fix and "llama3.1:8b" in r["model"].detail


def test_checks_report_server_down():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    r = names(run_checks(make_client(down), which=lambda _: "/bin/ollama"))
    assert r["ollama installed"].ok and not r["ollama server"].ok and not r["model"].ok
    assert "ollama serve" in r["ollama server"].fix


def test_missing_binary_is_fatal_only_if_the_server_is_also_down():
    r = names(run_checks(make_client(ollama()), which=lambda _: None, candidates=()))
    assert r["ollama installed"].ok
    assert "server is reachable" in r["ollama installed"].detail

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    r = names(run_checks(make_client(down), which=lambda _: None, candidates=()))
    assert not r["ollama installed"].ok and "ollama.com" in r["ollama installed"].fix


# ---- HTTP API ----------------------------------------------------------------

def api(handler):
    return TestClient(create_app(CFG, make_client(handler), guard=GuardrailManager(POLICY), actions=quick_actions()))


def test_api_health_status_tools():
    with api(ollama()) as c:
        assert c.get("/health").json() == {"status": "ok", "phase": 9}
        status = c.get("/status").json()
        assert status["ready"] is True and status["model"] == "qwen2.5:3b"
        tools = c.get("/tools").json()
        assert {"open_app", "reply"} <= {t["name"] for t in tools}
        assert all(t["category"] for t in tools)


def test_api_status_when_model_missing():
    with api(ollama(models=())) as c:
        assert c.get("/status").json()["ready"] is False


def test_api_route_success():
    with api(ollama()) as c:
        body = c.post("/route", json={"text": "open chrome"}).json()
    assert {k: body[k] for k in ("tool", "args", "requires_confirmation", "fallback", "note", "attempts")} == {
        "tool": "open_app", "args": {"app_name": "Google Chrome"}, "requires_confirmation": False,
        "fallback": False, "note": "", "attempts": 1,
    }
    assert body["guard"]["outcome"] == "allow" and body["guard"]["authorized"] is True


def test_api_route_invalid_json_falls_back_without_error():
    bad = lambda r: httpx.Response(200, json=chat_body("I cannot do JSON"))  # noqa: E731
    with api(ollama(chat=bad)) as c:
        res = c.post("/route", json={"text": "open chrome"})
    assert res.status_code == 200
    body = res.json()
    assert body["fallback"] is True and body["tool"] == "reply" and body["attempts"] == 2


def test_api_route_errors():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    with api(down) as c:
        assert c.post("/route", json={"text": "hi"}).status_code == 503
        assert c.post("/route", json={"text": ""}).status_code == 422
        assert c.post("/route", json={"text": "x" * 1001}).status_code == 422
    missing = lambda r: httpx.Response(404, json={"error": "model 'qwen2.5:3b' not found"})  # noqa: E731
    with api(ollama(chat=missing)) as c:
        assert c.post("/route", json={"text": "hi"}).status_code == 503
    broken = lambda r: httpx.Response(500, text="boom")  # noqa: E731
    with api(ollama(chat=broken)) as c:
        assert c.post("/route", json={"text": "hi"}).status_code == 502
