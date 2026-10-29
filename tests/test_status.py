import json
from datetime import datetime

import httpx
import pytest

from automation.choices import ChoiceStore
from backend.actions import ActionRouter
from backend.config import ROOT
from backend.llm import OllamaClient
from backend.startup import CheckResult, run_checks
from backend.status import FAIL, OFF, OK, WARN, Component, collect, from_model_checks, overall
from browserctl import BrowserService
from fake_browser import FakeWorker
from fakes import make_world
from memory import MemoryService
from memory.notify import Notifier
from memory.scheduler import ReminderScheduler
from memory.store import MemoryStore
from organizer import OrganizerService
from organizer.movelog import MoveLog
from safety import GuardrailManager, Policy
from test_voice import FakeEngine
from voice import VoiceService

POLICY = Policy.load(ROOT / "safety" / "policy.yaml")
GOOD_CHECKS = [CheckResult("ollama installed", True, "/usr/local/bin/ollama"), CheckResult("ollama server", True, "running, version 0.1"),
               CheckResult("model", True, "qwen2.5:3b is available")]


def by_name(components):
    return {c.name: c for c in components}


def parts(tmp_path, *, memory_enabled=True, browser=None, organizer_enabled=True, voice=None, hammerspoon=False):
    world = make_world(tmp_path / "w")
    choices = ChoiceStore()
    world.service.choices = choices
    store = MemoryStore(tmp_path / "m.db")
    memory = MemoryService(store, Notifier(enabled=False), ReminderScheduler(store, Notifier(enabled=False)), enabled=memory_enabled,
                           notifications=False)
    organizer = OrganizerService(MoveLog(tmp_path / "moves.jsonl"), home=world.home, enabled=organizer_enabled)
    router = ActionRouter(world.service, browser or BrowserService(FakeWorker(), choices=choices), choices, memory, organizer)
    return router, memory, world


def test_everything_healthy_but_hammerspoon_is_degraded_not_down(tmp_path):
    router, memory, _ = parts(tmp_path)
    memory.start()
    try:
        voice = VoiceService(FakeEngine("stt"), [FakeEngine("Piper")])
        c = by_name(collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=router, voice=voice))
    finally:
        memory.close()
    assert {n: x.state for n, x in c.items()} == {
        "language model server": OK, "language model": OK, "safety rules": OK, "apps and files": OK, "Hammerspoon": WARN,
        "memory": OK, "reminders": OK, "browser": OK, "organizer": OK, "speech to text": OK, "text to speech": OK}
    assert c["Hammerspoon"].fix and "AppleScript" in c["Hammerspoon"].detail
    assert overall(list(c.values())) == "degraded"


def test_with_hammerspoon_running_everything_is_ready(tmp_path):
    router, memory, world = parts(tmp_path)
    world.service.controller.hammerspoon = type("H", (), {"available": lambda self: True})()
    memory.start()
    try:
        components = collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=router,
                             voice=VoiceService(FakeEngine("stt"), [FakeEngine("Piper")]))
    finally:
        memory.close()
    assert overall([c for c in components if c.name != "reminders"] + [Component("reminders", OK, "")]) == "ready"
    assert by_name(components)["Hammerspoon"].state == OK


def test_the_model_and_the_safety_rules_are_the_only_core_parts(tmp_path):
    router, memory, _ = parts(tmp_path)
    components = collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=router, voice=VoiceService(FakeEngine("stt"), [FakeEngine("Piper")]))
    assert sorted(c.name for c in components if c.core) == ["language model", "language model server", "safety rules"]


def test_a_missing_model_means_down(tmp_path):
    router, *_ = parts(tmp_path)
    checks = [CheckResult("ollama server", True, "running"), CheckResult("model", False, "qwen2.5:3b is not pulled", "Run `ollama pull qwen2.5:3b`.")]
    components = collect(model_checks=checks, guard=GuardrailManager(POLICY), actions=router)
    assert overall(components) == "down"
    model = by_name(components)["language model"]
    assert model.state == FAIL and model.core and "ollama pull" in model.fix


def test_a_broken_optional_part_is_degraded_never_down(tmp_path):
    router, memory, _ = parts(tmp_path)
    memory.store.path.write_bytes(b"garbage" * 100)                    # the memory file is damaged
    components = collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=router)
    mem = by_name(components)["memory"]
    assert mem.state == FAIL and not mem.core and "memory_check" in mem.fix
    assert overall(components) == "degraded"


def test_no_safety_rules_means_down(tmp_path):
    empty = GuardrailManager(POLICY)
    empty.policy = type("P", (), {"rules": {}})()
    components = collect(model_checks=GOOD_CHECKS, guard=empty)
    assert by_name(components)["safety rules"].state == FAIL and overall(components) == "down"


def test_parts_that_are_turned_off_say_so_and_are_not_a_problem(tmp_path):
    class Off:
        def status(self): return {"enabled": False}
    router, memory, _ = parts(tmp_path, memory_enabled=False, organizer_enabled=False)
    router.browser = Off()
    voice = VoiceService(FakeEngine("stt"), [], enabled=False)
    c = by_name(collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=router, voice=voice))
    assert [c[n].state for n in ("memory", "browser", "organizer", "voice")] == [OFF] * 4
    assert "config.yaml" in c["voice"].detail


def test_missing_browser_pieces_and_voice_engines_are_warnings_with_a_fix(tmp_path):
    class Broken:
        def status(self): return {"enabled": True, "ready": False, "problem": "The browser helper isn't installed yet. Run this once:  cd browser && npm install"}
    router, *_ = parts(tmp_path)
    router.browser = Broken()
    voice = VoiceService(FakeEngine("stt", ok=False), [FakeEngine("Piper", ok=False)])
    c = by_name(collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=router, voice=voice))
    assert c["browser"].state == WARN and "npm install" in c["browser"].detail
    assert c["speech to text"].state == WARN and "Typing still works" in c["speech to text"].fix
    assert c["text to speech"].state == WARN and "not spoken" in c["text to speech"].fix


def test_a_check_that_breaks_is_reported_not_raised(tmp_path):
    class Exploding:
        def status(self): raise RuntimeError("boom")
    router, *_ = parts(tmp_path)
    router.organizer = Exploding()
    c = by_name(collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=router))
    assert c["organizer"].state == FAIL and "RuntimeError" in c["organizer"].detail and "boom" not in c["organizer"].detail
    assert c["memory"].state == OK                                       # the others are unaffected


def test_a_desktop_only_setup_still_reports(tmp_path):
    world = make_world(tmp_path)
    names = [c.name for c in collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=world.service)]
    assert names == ["language model server", "language model", "safety rules", "apps and files", "Hammerspoon"]


def test_no_apps_found_is_a_failure(tmp_path):
    world = make_world(tmp_path, apps={})
    c = by_name(collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=world.service))
    assert c["apps and files"].state == FAIL


def test_reminders_that_are_not_watching_yet_are_a_warning(tmp_path):
    router, memory, _ = parts(tmp_path)
    c = by_name(collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=router))      # never started
    assert c["reminders"].state == WARN


def test_the_installed_binary_check_is_not_shown_and_overall_rules():
    comps = from_model_checks(GOOD_CHECKS)
    assert [c.name for c in comps] == ["language model server", "language model"]
    ok, warn, fail = Component("a", OK, ""), Component("b", WARN, ""), Component("c", FAIL, "")
    assert overall([ok]) == "ready" and overall([ok, warn]) == "degraded" and overall([ok, fail]) == "degraded"
    assert overall([ok, Component("m", FAIL, "", core=True)]) == "down" and overall([Component("o", OFF, "")]) == "ready"
    assert overall([]) == "ready"


def test_a_slow_or_absent_ollama_gives_a_down_status_without_raising():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)
    llm = OllamaClient("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(handler))
    components = from_model_checks(run_checks(llm, which=lambda n: None, candidates=()))
    assert overall(components) == "down" and any("Start it" in c.fix for c in components)


def test_reminders_say_when_this_system_cannot_show_notifications(tmp_path, monkeypatch):
    from backend import status as status_module
    router, memory, _ = parts(tmp_path)
    memory.notifications = True
    memory.start()
    try:
        monkeypatch.setattr(status_module.os.path, "exists", lambda p: False)
        monkeypatch.setattr(status_module.shutil, "which", lambda name: None)
        missing = by_name(collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=router))["reminders"]
        assert missing.state == WARN and "notifications" in missing.detail
        monkeypatch.setattr(status_module.os.path, "exists", lambda p: True)
        present = by_name(collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=router))["reminders"]
        assert present.state == OK and "notifications are off" not in present.detail
    finally:
        memory.close()


def test_an_organizer_with_no_usable_folders_is_a_warning(tmp_path):
    router, memory, world = parts(tmp_path)
    router.organizer = OrganizerService(MoveLog(tmp_path / "moves.jsonl"), home=world.home, folders=["~/Nowhere"])
    c = by_name(collect(model_checks=GOOD_CHECKS, guard=GuardrailManager(POLICY), actions=router))["organizer"]
    assert c.state == WARN and "organizer.folders" in c.fix
