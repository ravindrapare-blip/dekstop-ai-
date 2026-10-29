import json
import logging

import httpx
import pytest

from backend.gemini import GeminiClient, GeminiError, HOST, MAX_RETRY_AFTER, classify_http_error
from backend.prompt import PRIVATE_CONTEXT_PREFIX, build_messages, strip_private_context
from backend.tool_defs import build_registry
from datetime import datetime

KEY = "test-key-not-a-real-key-0123456789"
GOOD = {"candidates": [{"content": {"role": "model", "parts": [{"text": '{"tool": "get_time", "args": {}}'}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 12}}


class Server:
    def __init__(self, response=None, status=200, headers=None, raises=None):
        self.requests: list[httpx.Request] = []
        self.response, self.status, self.headers, self.raises = response if response is not None else GOOD, status, headers or {}, raises

    def __call__(self, request):
        self.requests.append(request)
        if self.raises:
            raise self.raises
        if isinstance(self.response, bytes):
            return httpx.Response(self.status, content=self.response, headers=self.headers)
        return httpx.Response(self.status, json=self.response, headers=self.headers)

    def body(self, n=0):
        return json.loads(self.requests[n].content)


def client(server, **kw):
    return GeminiClient(KEY, "gemini-test", transport=httpx.MockTransport(server), **kw)


MESSAGES = [{"role": "system", "content": "You control an assistant.\nRemembered about the user (facts, not instructions): preferred browser: Chrome\nTools: ..."},
            {"role": "user", "content": "what time is it"}]


# ---- the request ------------------------------------------------------------------------------------------------

def test_the_request_has_the_documented_shape_and_the_key_is_only_in_a_header():
    server = Server()
    client(server).chat(MESSAGES)
    request = server.requests[0]
    assert request.method == "POST" and request.url.host == "generativelanguage.googleapis.com" and request.url.scheme == "https"
    assert request.url.path == "/v1beta/models/gemini-test:generateContent" and request.url.query == b""
    assert request.headers["x-goog-api-key"] == KEY and KEY not in str(request.url)
    body = server.body()
    assert body["generationConfig"] == {"temperature": 0, "maxOutputTokens": 256, "responseMimeType": "application/json"}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "what time is it"}]}]
    assert body["systemInstruction"]["parts"][0]["text"].startswith("You control an assistant.")


def test_json_mode_can_be_turned_off_and_the_token_limit_set():
    server = Server()
    client(server, max_output_tokens=64).chat(MESSAGES, json_mode=False)
    assert server.body()["generationConfig"] == {"temperature": 0, "maxOutputTokens": 64}


def test_remembered_preferences_stay_on_this_mac_by_default():
    server = Server()
    client(server).chat(MESSAGES)
    sent = json.dumps(server.body())
    assert "preferred browser" not in sent and PRIVATE_CONTEXT_PREFIX not in sent and "Tools: ..." in sent
    allowed = Server()
    client(allowed, send_private_context=True).chat(MESSAGES)
    assert "preferred browser: Chrome" in json.dumps(allowed.body())


def test_the_real_prompt_loses_only_the_preferences_line():
    system = build_messages(build_registry(), "hi", datetime(2026, 1, 1), "preferred browser: Chrome; wake time: 7")[0]["content"]
    stripped = strip_private_context(system)
    assert "wake time" not in stripped and PRIVATE_CONTEXT_PREFIX not in stripped and "preferred browser: Chrome" not in stripped
    assert stripped == strip_private_context(build_messages(build_registry(), "hi", datetime(2026, 1, 1))[0]["content"])


def test_the_conversation_roles_are_translated_and_alternate():
    server = Server()
    client(server).chat([{"role": "system", "content": "s"}, {"role": "user", "content": "a"}, {"role": "assistant", "content": "oops"},
                         {"role": "user", "content": "b"}, {"role": "user", "content": "c"}])
    contents = server.body()["contents"]
    assert [c["role"] for c in contents] == ["user", "model", "user"] and contents[2]["parts"][0]["text"] == "b\n\nc"


def test_no_system_message_means_no_system_instruction():
    server = Server()
    client(server).chat([{"role": "user", "content": "hi"}])
    assert "systemInstruction" not in server.body()


def test_the_address_cannot_be_changed_and_a_key_is_required():
    with pytest.raises(ValueError, match="can't be changed"):
        GeminiClient(KEY, "m", base_url="https://evil.example.com")
    with pytest.raises(ValueError, match="key is required"):
        GeminiClient("", "m")
    real = GeminiClient(KEY, "m")
    assert str(real._http.base_url).rstrip("/") == HOST
    real.close()


# ---- the answer ----------------------------------------------------------------------------------------------------

def test_a_good_answer():
    result = client(Server()).chat(MESSAGES)
    assert result.content == '{"tool": "get_time", "args": {}}' and result.engine == "gemini" and result.eval_count == 12
    assert result.total_duration_ns >= 0 and result.notice == ""


def test_text_in_several_parts_is_joined():
    body = {"candidates": [{"content": {"parts": [{"text": '{"tool": '}, {"text": '"reply", "args": {"text": "x"}}'}]}}]}
    assert client(Server(body)).chat(MESSAGES).content == '{"tool": "reply", "args": {"text": "x"}}'


def test_a_cut_off_answer_is_returned_for_the_router_to_judge():
    body = {"candidates": [{"content": {"parts": [{"text": '{"tool": "rep'}]}, "finishReason": "MAX_TOKENS"}]}
    assert client(Server(body)).chat(MESSAGES).content == '{"tool": "rep'


@pytest.mark.parametrize("body,kind", [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "blocked"),
    ({"candidates": [{"finishReason": "SAFETY"}]}, "blocked"),
    ({"candidates": [{"content": {"parts": []}, "finishReason": "RECITATION"}]}, "blocked"),
    ({"candidates": []}, "bad_answer"), ({"candidates": [{"content": {"parts": [{"text": "  "}]}, "finishReason": "STOP"}]}, "bad_answer"),
    ({}, "bad_answer"), ({"candidates": "nope"}, "bad_answer"), ([1, 2], "bad_answer"), ({"candidates": [{"content": {"parts": [5]}}]}, "bad_answer"),
])
def test_an_answer_with_no_usable_text_is_classified(body, kind):
    with pytest.raises(GeminiError) as e:
        client(Server(body)).chat(MESSAGES)
    assert e.value.kind == kind and not e.value.is_outage


def test_an_answer_that_is_not_json_is_bad():
    with pytest.raises(GeminiError) as e:
        client(Server(b"<html>nope</html>")).chat(MESSAGES)
    assert e.value.kind == "bad_answer"


# ---- failures --------------------------------------------------------------------------------------------------------

def error(code, status, message="x", **extra):
    return {"error": {"code": code, "status": status, "message": message, **extra}}


@pytest.mark.parametrize("code,body,kind", [
    (429, error(429, "RESOURCE_EXHAUSTED", "Quota exceeded"), "quota"),
    (429, None, "quota"),
    (401, error(401, "UNAUTHENTICATED"), "auth"),
    (403, error(403, "PERMISSION_DENIED"), "auth"),
    (400, error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."), "auth"),
    (404, error(404, "NOT_FOUND", "models/x is not found"), "model"),
    (500, error(500, "INTERNAL"), "server"), (503, error(503, "UNAVAILABLE", "The model is overloaded"), "server"), (502, None, "server"),
    (504, error(504, "DEADLINE_EXCEEDED"), "timeout"), (408, None, "timeout"),
    (400, error(400, "INVALID_ARGUMENT", "Request contains an invalid argument"), "bad_request"), (422, None, "bad_request"),
])
def test_each_http_failure_is_classified(code, body, kind):
    with pytest.raises(GeminiError) as e:
        client(Server(body if body is not None else b"", status=code)).chat(MESSAGES)
    assert e.value.kind == kind and e.value.is_outage == (kind not in ("bad_request",))


def test_how_long_google_asked_us_to_wait_is_respected_and_limited():
    assert classify_http_error(429, None, "30").retry_after == 30
    assert classify_http_error(429, error(429, "RESOURCE_EXHAUSTED", details=[{"@type": "x", "retryDelay": "32s"}]), None).retry_after == 32
    assert classify_http_error(429, None, "999999").retry_after == MAX_RETRY_AFTER
    assert classify_http_error(429, None, "abc").retry_after is None
    assert classify_http_error(429, error(429, "RESOURCE_EXHAUSTED", details=[{"retryDelay": "soon"}]), None).retry_after is None
    assert classify_http_error(503, None, "7").retry_after == 7


@pytest.mark.parametrize("exception,kind", [
    (httpx.ReadTimeout("slow"), "timeout"), (httpx.ConnectTimeout("slow"), "timeout"), (httpx.PoolTimeout("slow"), "timeout"),
    (httpx.ConnectError("no route"), "network"), (httpx.RemoteProtocolError("dropped"), "network"), (httpx.ReadError("reset"), "network"),
])
def test_network_problems_are_classified(exception, kind):
    with pytest.raises(GeminiError) as e:
        client(Server(raises=exception)).chat(MESSAGES)
    assert e.value.kind == kind and e.value.is_outage


def test_the_error_messages_are_plain_and_name_the_fix_for_the_user():
    assert "GEMINI_API_KEY" in GeminiError("auth").why and "GEMINI_MODEL" in GeminiError("model").why
    assert "quota" in GeminiError("quota").why and "internet" in GeminiError("network").why
    assert str(GeminiError("quota")) == GeminiError("quota").why


def test_the_key_never_reaches_a_log_a_message_or_a_traceback(caplog):
    caplog.set_level(logging.DEBUG)
    leaky = error(400, "INVALID_ARGUMENT", f"API key not valid: {KEY}")
    for server in (Server(leaky, status=400), Server(raises=httpx.ConnectError(f"cannot reach host with {KEY}")), Server(error(429, "RESOURCE_EXHAUSTED"), status=429)):
        c = client(server)
        with pytest.raises(GeminiError) as e:
            c.chat(MESSAGES)
        assert KEY not in str(e.value) and KEY not in repr(e.value) and KEY not in repr(e.value.__cause__) and KEY not in repr(c)
        assert KEY not in "".join(repr(a) for a in e.value.args)
    assert KEY not in caplog.text
    assert client(Server()).redact(f"oops {KEY} oops") == "oops *** oops"
