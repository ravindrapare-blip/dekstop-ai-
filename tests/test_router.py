import json
from datetime import datetime

import pytest

from backend.llm import ChatResult, OllamaUnavailable
from backend.router import Router
from backend.tool_defs import build_registry

CLOCK = lambda: datetime(2025, 3, 4, 9, 30)  # noqa: E731


class FakeLLM:
    """Returns scripted replies and records every prompt it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, json_mode=True):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(reply, eval_count=10, eval_duration_ns=1_000_000_000)


def make(*replies, retries=1):
    llm = FakeLLM(*replies)
    return Router(llm, build_registry(), max_retries=retries, clock=CLOCK), llm


def call_json(tool, **args):
    return json.dumps({"tool": tool, "args": args, "requires_confirmation": False})


# ---- tool selection: one realistic model output per category ------------------

@pytest.mark.parametrize("request_text,model_output,tool,args", [
    ("open chrome", call_json("open_app", app_name="Google Chrome"), "open_app", {"app_name": "Google Chrome"}),
    ("open my downloads folder", call_json("open_folder", path_or_name="Downloads"), "open_folder", {"path_or_name": "Downloads"}),
    ("search for ai tools", call_json("search_chrome", query="AI tools"), "search_chrome", {"query": "AI tools"}),
    ("remind me at 6 pm to call mom",
     call_json("create_reminder", text="call mom", due_datetime="2025-03-04T18:00:00"),
     "create_reminder", {"text": "call mom", "due_datetime": "2025-03-04T18:00:00"}),
    ("save a note called ideas", call_json("save_note", title="ideas", content="ideas"), "save_note",
     {"title": "ideas", "content": "ideas"}),
    ("what time is it", call_json("get_time"), "get_time", {}),
])
def test_request_is_routed_to_the_chosen_tool(request_text, model_output, tool, args):
    router, llm = make(model_output)
    res = router.route(request_text)
    assert (res.call.tool, res.call.args) == (tool, args)
    assert not res.fallback and res.attempts == 1 and len(llm.calls) == 1
    assert res.stats.tokens_per_second == 10


def test_user_text_reaches_the_model_as_the_user_message():
    router, llm = make(call_json("get_time"))
    router.route("  what time is it  ")
    assert llm.calls[0][-1] == {"role": "user", "content": "what time is it"}


# ---- invalid output: retry, then a safe fallback ------------------------------

def test_one_corrective_retry_recovers():
    router, llm = make("Sure, I'll open Chrome for you!", call_json("open_app", app_name="Google Chrome"))
    res = router.route("open chrome")
    assert res.call.tool == "open_app" and not res.fallback and res.attempts == 2
    retry_prompt = llm.calls[1]
    assert retry_prompt[-2]["role"] == "assistant"
    assert "not valid JSON" in retry_prompt[-1]["content"]


def test_two_invalid_outputs_fall_back_to_a_harmless_reply():
    router, llm = make("nope", '{"tool": "delete_file", "args": {"path": "/"}}')
    res = router.route("wipe my disk")
    assert res.fallback and res.attempts == 2
    assert res.call.tool == "reply" and not res.call.requires_confirmation
    assert "unknown tool" in res.note


def test_no_retries_when_configured():
    router, llm = make("garbage", retries=0)
    res = router.route("open chrome")
    assert res.fallback and res.attempts == 1 and len(llm.calls) == 1


def test_model_never_turns_prose_into_an_action():
    router, _ = make("I will open Google Chrome now", "open_app Google Chrome")
    res = router.route("open chrome")
    assert res.fallback and res.call.tool == "reply"


# ---- input limits and model failures ------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", None, "x" * 1001])
def test_bad_input_never_calls_the_model(text):
    router, llm = make()
    res = router.route(text)
    assert res.fallback and res.call.tool == "reply" and llm.calls == []


def test_model_errors_propagate_to_the_caller():
    router, _ = make(OllamaUnavailable("down"))
    with pytest.raises(OllamaUnavailable):
        router.route("open chrome")
