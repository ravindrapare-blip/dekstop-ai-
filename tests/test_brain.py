import logging
import threading
from datetime import datetime

import pytest

from backend.brain import Brain, BrainUnavailable, build_brain, human_duration
from backend.gemini import GeminiError
from backend.llm import ChatResult, ModelNotFound, OllamaError, OllamaUnavailable
from backend.router import Router
from backend.settings import load_settings
from backend.tool_defs import build_registry


class Local:
    model = "qwen-test"

    def __init__(self):
        self.calls = 0
        self.failure = None

    def chat(self, messages, *, json_mode=True):
        self.calls += 1
        if self.failure:
            raise self.failure
        return ChatResult(content='{"tool": "reply", "args": {"text": "from local"}}')

    def close(self):
        pass


class Gemini:
    model = "gemini-test"

    def __init__(self):
        self.calls = 0
        self.outcomes = []                  # consumed in order; an Exception is raised, a string is the answer; empty means success
        self.closed = False
        self.gate = None                    # an Event to wait on, to hold a request in flight

    def chat(self, messages, *, json_mode=True):
        self.calls += 1
        if self.gate:
            self.gate.wait(5)
        outcome = self.outcomes.pop(0) if self.outcomes else '{"tool": "reply", "args": {"text": "from gemini"}}'
        if isinstance(outcome, Exception):
            raise outcome
        return ChatResult(content=outcome, engine="gemini")

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(**kw):
    clock = Clock()
    gemini = kw.pop("gemini", Gemini())
    local = kw.pop("local", Local())
    brain = Brain(local, gemini, gemini_wanted=kw.pop("gemini_wanted", gemini is not None), clock=clock,
                  wall_clock=lambda: datetime(2026, 10, 29, 14, 0, 0), **kw)
    return brain, gemini, local, clock


MSG = [{"role": "user", "content": "hi"}]


def text(result):
    return result.content


# ---- the plain cases --------------------------------------------------------------------------------------------

def test_with_gemini_off_everything_is_local_and_silent():
    local = Local()
    brain = Brain(local, None)
    result = brain.chat(MSG)
    assert result.engine == "ollama" and result.notice == "" and local.calls == 1
    assert brain.status()["gemini"]["state"] == "off" and brain.status()["using"] == "ollama" and brain.model == "qwen-test"


def test_gemini_answers_first_and_the_local_model_is_not_touched():
    brain, gemini, local, _ = make()
    result = brain.chat(MSG)
    assert result.engine == "gemini" and "from gemini" in result.content and result.notice == "" and local.calls == 0
    assert brain.status()["using"] == "gemini" and brain.switches == 0


# ---- switching to the local model ----------------------------------------------------------------------------------

@pytest.mark.parametrize("kind,phrase", [("quota", "quota or rate limit"), ("timeout", "didn't answer in time"), ("network", "couldn't be reached"),
                                         ("server", "overloaded"), ("auth", "GEMINI_API_KEY"), ("model", "GEMINI_MODEL")])
def test_every_kind_of_outage_switches_to_the_local_model_and_says_so_once(kind, phrase):
    brain, gemini, local, clock = make()
    gemini.outcomes = [GeminiError(kind)]
    first = brain.chat(MSG)
    assert first.engine == "ollama" and "from local" in first.content and local.calls == 1
    assert phrase in first.notice and "switched to the local model" in first.notice and "try Gemini again in" in first.notice
    second = brain.chat(MSG)                                                  # still cooling down: no Gemini call, no repeat of the news
    assert second.engine == "ollama" and second.notice == "" and gemini.calls == 1 and local.calls == 2


def test_the_notice_names_the_wait():
    brain, gemini, _, _ = make()
    gemini.outcomes = [GeminiError("quota")]
    assert brain.chat(MSG).notice.endswith("I'll try Gemini again in 5 minutes.")
    brain, gemini, _, _ = make()
    gemini.outcomes = [GeminiError("auth")]
    assert brain.chat(MSG).notice.endswith("in 30 minutes.")
    brain, gemini, _, _ = make()
    gemini.outcomes = [GeminiError("quota", retry_after=32)]
    assert brain.chat(MSG).notice.endswith("in 32 seconds.")


def test_the_cooldown_is_held_exactly_until_it_ends():
    brain, gemini, local, clock = make()
    gemini.outcomes = [GeminiError("network")]
    brain.chat(MSG)
    clock.now += 299
    brain.chat(MSG)
    assert gemini.calls == 1                                                  # one second early: still local
    clock.now += 1
    brain.chat(MSG)
    assert gemini.calls == 2                                                  # on time: Gemini is tried again


# ---- switching back ----------------------------------------------------------------------------------------------------

def test_when_gemini_is_back_after_the_cooldown_it_is_used_again_and_the_user_is_told_once():
    brain, gemini, local, clock = make()
    gemini.outcomes = [GeminiError("quota")]
    brain.chat(MSG)
    clock.now += 301
    back = brain.chat(MSG)
    assert back.engine == "gemini" and back.notice == "Gemini is available again, so I'm using it."
    again = brain.chat(MSG)
    assert again.engine == "gemini" and again.notice == "" and local.calls == 1
    assert brain.switches == 2 and brain.status()["gemini"]["state"] == "ready" and brain.status()["using"] == "gemini"


def test_if_gemini_is_still_down_after_the_cooldown_the_user_is_not_told_again_but_the_wait_grows():
    brain, gemini, local, clock = make()
    gemini.outcomes = [GeminiError("network")] * 5
    waits = []
    for i in range(5):
        result = brain.chat(MSG)
        assert result.engine == "ollama"
        assert (result.notice != "") == (i == 0)                              # only the first failure is announced
        waits.append(round(brain.status()["gemini"]["retry_in_seconds"]))
        clock.now += waits[-1] + 1
    assert waits == [300, 600, 1200, 1200, 1200]                              # 5 min, 10, 20, then it stops growing
    assert brain.switches == 1


def test_a_recovery_resets_the_wait():
    brain, gemini, _, clock = make()
    gemini.outcomes = [GeminiError("network"), GeminiError("network")]
    brain.chat(MSG); clock.now += 301
    brain.chat(MSG); clock.now += 601
    assert brain.chat(MSG).engine == "gemini"
    gemini.outcomes = [GeminiError("network")]
    brain.chat(MSG)
    assert round(brain.status()["gemini"]["retry_in_seconds"]) == 300


def test_google_telling_us_how_long_to_wait_is_obeyed_within_limits():
    brain, gemini, _, _ = make()
    gemini.outcomes = [GeminiError("quota", retry_after=90)]
    brain.chat(MSG)
    assert brain.status()["gemini"]["retry_in_seconds"] == 90
    brain, gemini, _, _ = make()
    gemini.outcomes = [GeminiError("quota", retry_after=0)]
    brain.chat(MSG)
    assert brain.status()["gemini"]["retry_in_seconds"] == 5                    # never a tight loop
    brain, gemini, _, _ = make()
    gemini.outcomes = [GeminiError("quota", retry_after=10**7)]
    brain.chat(MSG)
    assert brain.status()["gemini"]["retry_in_seconds"] == 3600


@pytest.mark.parametrize("kind", ["auth", "model"])
def test_a_rejected_key_or_an_unknown_model_waits_the_long_time(kind):
    brain, gemini, _, _ = make()
    gemini.outcomes = [GeminiError(kind)]
    brain.chat(MSG)
    assert brain.status()["gemini"]["retry_in_seconds"] == 1800


def test_a_rejected_key_waits_longer_than_a_busy_server():
    brain, gemini, _, _ = make()
    gemini.outcomes = [GeminiError("auth")]
    brain.chat(MSG)
    assert brain.status()["gemini"]["retry_in_seconds"] == 1800
    brain, gemini, _, _ = make(cooldown_seconds=60, auth_cooldown_seconds=900)
    gemini.outcomes = [GeminiError("server")]
    brain.chat(MSG)
    assert brain.status()["gemini"]["retry_in_seconds"] == 60


# ---- problems with one request only ---------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["blocked", "bad_answer", "bad_request"])
def test_a_problem_with_one_request_uses_the_local_model_for_it_without_a_cooldown_or_a_notice(kind):
    brain, gemini, local, _ = make()
    gemini.outcomes = [GeminiError(kind)]
    result = brain.chat(MSG)
    assert result.engine == "ollama" and result.notice == "" and local.calls == 1
    assert brain.status()["gemini"]["state"] == "ready" and brain.switches == 0
    assert brain.chat(MSG).engine == "gemini"                                  # the very next request goes to Gemini again


# ---- when there is no fallback ----------------------------------------------------------------------------------------

def test_with_the_fallback_off_a_gemini_failure_is_an_error_not_a_silent_local_answer():
    brain, gemini, local, clock = make(use_local_fallback=False)
    gemini.outcomes = [GeminiError("quota")]
    with pytest.raises(BrainUnavailable) as e:
        brain.chat(MSG)
    assert "quota" in str(e.value) and "USE_LOCAL_FALLBACK=false" in str(e.value) and isinstance(e.value, OllamaUnavailable) and local.calls == 0
    with pytest.raises(BrainUnavailable, match="retrying in"):
        brain.chat(MSG)                                                        # cooling down: no Gemini call either
    assert gemini.calls == 1
    clock.now += 301
    assert brain.chat(MSG).engine == "gemini"                                  # and it recovers by itself


def test_with_the_fallback_off_and_no_key_it_says_so():
    brain = Brain(Local(), None, gemini_wanted=True, use_local_fallback=False)
    with pytest.raises(BrainUnavailable, match="no Gemini API key"):
        brain.chat(MSG)


def test_with_gemini_off_the_fallback_setting_means_nothing():
    local = Local()
    assert Brain(local, None, gemini_wanted=False, use_local_fallback=False).chat(MSG).engine == "ollama"


def test_without_a_key_the_local_model_is_used_and_the_user_hears_why_once():
    local = Local()
    brain = Brain(local, None, gemini_wanted=True)
    first, second = brain.chat(MSG), brain.chat(MSG)
    assert "no API key" in first.notice and second.notice == "" and local.calls == 2
    assert brain.status()["gemini"]["state"] == "no_key"


# ---- both brains failing ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("error", [OllamaUnavailable("cannot connect to Ollama at http://127.0.0.1:11434"), ModelNotFound("model not found"), OllamaError("timed out")])
def test_when_the_local_model_also_fails_the_error_keeps_its_type_and_mentions_gemini(error):
    brain, gemini, local, _ = make()
    gemini.outcomes = [GeminiError("quota")]
    local.failure = error
    with pytest.raises(type(error)) as e:
        brain.chat(MSG)
    assert str(error) in str(e.value) and "Gemini is also unavailable" in str(e.value) and "quota" in str(e.value)


def test_a_local_failure_without_any_gemini_trouble_is_passed_on_unchanged():
    brain = Brain(Local(), None)
    brain.local.failure = OllamaUnavailable("down")
    with pytest.raises(OllamaUnavailable) as e:
        brain.chat(MSG)
    assert str(e.value) == "down"


def test_with_no_local_model_at_all():
    brain = Brain(None, None)
    with pytest.raises(BrainUnavailable, match="No language model"):
        brain.chat(MSG)


# ---- one probe at a time -----------------------------------------------------------------------------------------------

def test_while_one_request_is_testing_a_recovering_gemini_the_others_use_the_local_model():
    brain, gemini, local, clock = make()
    gemini.outcomes = [GeminiError("quota")]
    brain.chat(MSG)
    clock.now += 301
    gemini.gate = threading.Event()
    results = []
    probe = threading.Thread(target=lambda: results.append(brain.chat(MSG)))
    probe.start()
    while gemini.calls < 2:
        pass                                                                   # the probe is now waiting inside Gemini
    other = brain.chat(MSG)
    assert other.engine == "ollama" and gemini.calls == 2                      # it did not pile on
    gemini.gate.set()
    probe.join(5)
    assert results[0].engine == "gemini" and "available again" in results[0].notice


# ---- announcements and reporting -----------------------------------------------------------------------------------------

def test_the_notice_can_be_marked_for_reading_aloud():
    brain, gemini, _, clock = make(speak_notice=True)
    gemini.outcomes = [GeminiError("quota")]
    assert brain.chat(MSG).speak_notice is True
    clock.now += 301
    assert brain.chat(MSG).speak_notice is True                               # "available again" too
    assert brain.chat(MSG).speak_notice is False                              # nothing to say, nothing to read
    quiet, g2, _, _ = make()
    g2.outcomes = [GeminiError("quota")]
    assert quiet.chat(MSG).speak_notice is False


def test_the_status_report_has_no_secrets_and_tells_the_story():
    brain, gemini, _, _ = make()
    assert brain.status()["gemini"] == {"state": "ready", "model": "gemini-test", "reason": "", "kind": "", "retry_in_seconds": 0}
    gemini.outcomes = [GeminiError("quota")]
    brain.chat(MSG)
    s = brain.status()
    assert s["gemini"]["state"] == "cooling_down" and s["gemini"]["kind"] == "quota" and "quota" in s["gemini"]["reason"]
    assert s["using"] == "ollama" and s["local"] == {"available": True, "model": "qwen-test"} and s["fallback_enabled"] is True
    assert s["switches"] == 1 and s["last_switch"] == "2026-10-29T14:00:00" and not brain.gemini_usable


def test_the_log_tells_the_story_without_the_request_text(caplog):
    caplog.set_level(logging.INFO, logger="assistant.brain")
    brain, gemini, _, clock = make()
    gemini.outcomes = [GeminiError("quota", status="RESOURCE_EXHAUSTED")]
    brain.chat([{"role": "user", "content": "my secret request text"}])
    clock.now += 301
    brain.chat([{"role": "user", "content": "my secret request text"}])
    log = caplog.text
    assert "unavailable (quota, RESOURCE_EXHAUSTED)" in log and "using the local model" in log and "trying again in 5 minutes" in log
    assert "available again; switched back" in log and "secret request text" not in log


def test_closing_closes_gemini():
    brain, gemini, _, _ = make()
    brain.close()
    assert gemini.closed
    Brain(Local(), None).close()


@pytest.mark.parametrize("seconds,text", [(1, "1 second"), (45, "45 seconds"), (89, "89 seconds"), (90, "2 minutes"), (300, "5 minutes"), (3600, "60 minutes"),
                                          (5400, "2 hours"), (0, "1 second")])
def test_durations_read_naturally(seconds, text):
    assert human_duration(seconds) == text


# ---- the router sees the notice -----------------------------------------------------------------------------------------

def test_the_tool_router_passes_the_engine_and_the_notice_along():
    brain, gemini, local, _ = make()
    gemini.outcomes = [GeminiError("quota")]
    routed = Router(brain, build_registry(), max_retries=1).route("hello")
    assert routed.engine == "ollama" and "switched to the local model" in routed.notice and routed.call.tool == "reply"


def test_a_notice_from_the_first_attempt_survives_a_corrective_retry():
    brain, gemini, local, _ = make()
    gemini.outcomes = [GeminiError("quota")]
    local_answers = ["not json", '{"tool": "reply", "args": {"text": "ok"}}']
    local.chat = lambda messages, json_mode=True: ChatResult(content=local_answers.pop(0))
    routed = Router(brain, build_registry(), max_retries=1).route("hello")
    assert routed.attempts == 2 and "switched to the local model" in routed.notice and routed.call.args == {"text": "ok"}


def test_a_notice_survives_even_when_the_final_answer_is_a_polite_fallback():
    brain, gemini, local, _ = make()
    gemini.outcomes = [GeminiError("quota")]
    local.chat = lambda messages, json_mode=True: ChatResult(content="never json")
    routed = Router(brain, build_registry(), max_retries=1).route("hello")
    assert routed.fallback and "switched to the local model" in routed.notice and routed.engine == "ollama"


def test_gemini_proposing_a_nonexistent_tool_is_just_an_unusable_answer():
    brain, gemini, local, _ = make()
    gemini.outcomes = ['{"tool": "run_shell", "args": {"command": "rm -rf ~"}}', '{"tool": "run_shell", "args": {}}']
    routed = Router(brain, build_registry(), max_retries=1).route("clean up")
    assert routed.fallback and routed.call.tool == "reply" and local.calls == 0


# ---- building it from settings ---------------------------------------------------------------------------------------------

def test_building_from_settings():
    key = "test-key-not-a-real-key-0123456789"
    cfg = {"brain": {"timeout_seconds": 7, "cooldown_seconds": 120, "auth_cooldown_seconds": 600, "speak_switch_notice": True, "send_memory_to_gemini": True}}
    on = build_brain(cfg, load_settings(cfg, {"USE_GEMINI": "true", "GEMINI_API_KEY": key, "GEMINI_MODEL": "gemini-x"}), Local())
    assert on.gemini.model == "gemini-x" and on.cooldown_seconds == 120 and on.auth_cooldown_seconds == 600 and on.speak_notice
    assert on.gemini.send_private_context is True and on.use_local_fallback is True
    off = build_brain({}, load_settings({}, {"GEMINI_API_KEY": key}), Local())
    assert off.gemini is None and off.gemini_wanted is False                    # a key alone doesn't turn the cloud on
    nokey = build_brain({}, load_settings({}, {"USE_GEMINI": "true"}), Local())
    assert nokey.gemini is None and nokey.gemini_wanted is True
    private = build_brain({}, load_settings({}, {"USE_GEMINI": "true", "GEMINI_API_KEY": key}), Local())
    assert private.gemini.send_private_context is False
