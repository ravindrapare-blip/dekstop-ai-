import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import ROOT
from backend.llm import OllamaClient
from backend.main import create_app
from backend.tool_defs import build_registry
from backend.toolcall import ToolCall
from fakes import quick_actions
from safety import AuditLog, GuardrailManager, Outcome, Policy, PolicyError, Risk

POLICY = Policy.load(ROOT / "safety" / "policy.yaml")


@dataclass
class Call:
    tool: str
    args: dict = field(default_factory=dict)
    requires_confirmation: bool = False


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make_guard(tmp_path=None, **kw):
    audit = AuditLog(tmp_path / "audit.jsonl") if tmp_path else None
    return GuardrailManager(POLICY, audit, **kw)


def ev(tool, dry_run=None, guard=None, **args):
    return (guard or make_guard()).evaluate(Call(tool, args), dry_run=dry_run)


# ---- policy consistency --------------------------------------------------------

def test_every_tool_has_a_rule_and_every_rule_has_a_tool():
    tools = {t.name for t in build_registry().all()}
    assert tools == set(POLICY.rules)


def test_blocked_capabilities_are_not_tools():
    tools = {t.name for t in build_registry().all()}
    assert not tools & set(POLICY.blocked)
    required = {"delete_file", "delete_folder", "rename_file", "run_shell", "change_system_setting", "login_website"}
    assert required <= set(POLICY.blocked)


def test_registry_confirmation_defaults_agree_with_policy():
    for t in build_registry().all():
        if t.requires_confirmation:
            assert POLICY.rules[t.name] >= Risk.MEDIUM, t.name


def test_bad_policies_are_rejected(tmp_path):
    with pytest.raises(PolicyError):
        Policy.from_dict({"rules": {"open_app": "harmless"}})
    with pytest.raises(PolicyError):
        Policy.from_dict({"rules": {"a": "low"}, "blocked": {"a": {"risk": "high", "reason": "x"}}})
    with pytest.raises(PolicyError):
        Policy.from_dict({"rules": {}})
    with pytest.raises(PolicyError):
        Policy.load(tmp_path / "missing.yaml")


# ---- safe actions are allowed --------------------------------------------------

@pytest.mark.parametrize("tool,args", [
    ("open_app", {"app_name": "Google Chrome"}),
    ("open_app", {"app_name": "Safari"}),
    ("open_file", {"path_or_name": "budget.pdf"}),
    ("open_file", {"path_or_name": "~/Documents/report.pdf"}),
    ("open_folder", {"path_or_name": "Downloads"}),
    ("search_files", {"query": "budget"}),
    ("open_url", {"url": "https://example.com"}),
    ("open_url", {"url": "youtube.com"}),
    ("open_url", {"url": "localhost:3000"}),
    ("search_chrome", {"query": "AI tools"}),
    ("create_reminder", {"text": "call mom", "due_datetime": "2025-03-04T18:00:00"}),
    ("save_note", {"title": "ideas", "content": "line one\nline two"}),
    ("reply", {"text": "hello"}),
    ("get_time", {}),
])
def test_safe_actions_are_allowed(tool, args):
    d = ev(tool, **args)
    assert d.outcome is Outcome.ALLOW and d.authorized and d.risk is Risk.LOW and d.message == ""


# ---- dangerous actions are blocked by default ----------------------------------

@pytest.mark.parametrize("tool", sorted(n for n, b in POLICY.blocked.items() if b.risk is Risk.HIGH))
def test_high_risk_capabilities_are_blocked_until_acknowledged(tool):
    d = ev(tool, path="/tmp/x")
    assert d.outcome is Outcome.CONFIRM and not d.authorized
    assert d.requires_acknowledgment and d.risk is Risk.HIGH
    assert "blocked by default" in d.message


@pytest.mark.parametrize("tool", sorted(n for n, b in POLICY.blocked.items() if b.risk is Risk.CRITICAL))
def test_critical_capabilities_can_never_run(tool):
    guard = make_guard()
    d = guard.evaluate(Call(tool, {"command": "rm -rf /"}))
    assert d.outcome is Outcome.BLOCK and not d.authorized and d.confirmation_id is None
    assert guard.pending_count() == 0
    assert not guard.confirm("anything", True, acknowledge_risk=True).authorized


def test_destructive_shell_text_in_a_normal_tool_is_refused():
    d = ev("open_app", app_name="Terminal; rm -rf /")
    assert d.outcome is Outcome.BLOCK and d.risk is Risk.CRITICAL


# ---- invalid tools are rejected --------------------------------------------------

@pytest.mark.parametrize("tool", ["", "format_disk", "OPEN_APP", "open_app ", "../open_app"])
def test_unknown_tools_are_rejected(tool):
    guard = make_guard()
    d = guard.evaluate(Call(tool, {"app_name": "Safari"}))
    assert d.outcome is Outcome.BLOCK and "not an allowed action" in d.reason
    assert guard.pending_count() == 0


def test_a_tool_without_a_rule_is_denied():
    policy = Policy.from_dict({"rules": {"get_time": "low"}})
    d = GuardrailManager(policy).evaluate(Call("open_app", {"app_name": "Safari"}))
    assert d.outcome is Outcome.BLOCK


# ---- confirmation flow -----------------------------------------------------------

def test_destructive_action_asks_first():
    guard = make_guard()
    d = guard.evaluate(Call("close_app", {"app_name": "Safari"}, requires_confirmation=True))
    assert d.outcome is Outcome.CONFIRM and not d.authorized and d.confirmation_id
    assert d.message.startswith("I can do this: close app (app_name: Safari).")
    assert d.message.endswith("Please confirm.")
    assert guard.pending_count() == 1


@pytest.mark.parametrize("tool,args", [
    ("close_app", {"app_name": "Safari"}),
    ("close_tab", {}),
    ("delete_reminder", {"id": 3}),
    ("set_volume", {"level": 30}),
    ("organize_desktop", {}),
])
def test_medium_risk_tools_always_ask(tool, args):
    assert ev(tool, **args).outcome is Outcome.CONFIRM


def test_model_can_ask_for_confirmation_on_a_safe_tool():
    guard = make_guard()
    assert guard.evaluate(Call("open_app", {"app_name": "Safari"}, requires_confirmation=True)).outcome is Outcome.CONFIRM


def test_approval_authorizes_exactly_the_original_call_once():
    guard = make_guard()
    asked = guard.evaluate(Call("close_app", {"app_name": "Safari"}))
    done = guard.confirm(asked.confirmation_id, True)
    assert done.authorized and done.approved and (done.tool, done.args) == ("close_app", {"app_name": "Safari"})
    assert not guard.confirm(asked.confirmation_id, True).authorized  # single use
    assert guard.pending_count() == 0


def test_denial_cancels():
    guard = make_guard()
    asked = guard.evaluate(Call("close_app", {"app_name": "Safari"}))
    d = guard.confirm(asked.confirmation_id, False)
    assert d.outcome is Outcome.BLOCK and "Cancelled" in d.message
    assert not guard.confirm(asked.confirmation_id, True).authorized


def test_unknown_and_expired_confirmations_authorize_nothing():
    clock = Clock()
    guard = make_guard(timeout_seconds=60, clock=clock)
    assert not guard.confirm("nope", True).authorized
    asked = guard.evaluate(Call("close_app", {"app_name": "Safari"}))
    clock.now = 61
    d = guard.confirm(asked.confirmation_id, True)
    assert not d.authorized and "expired" in d.reason


def test_high_risk_needs_explicit_acknowledgment():
    guard = make_guard()
    asked = guard.evaluate(Call("delete_file", {"path_or_name": "x"}))
    no_ack = guard.confirm(asked.confirmation_id, True)
    assert not no_ack.authorized and no_ack.requires_acknowledgment
    assert guard.pending_count() == 1  # can still be acknowledged
    assert guard.confirm(asked.confirmation_id, True, acknowledge_risk=True).authorized


def test_pending_list_is_bounded():
    guard = make_guard(max_pending=2)
    ids = [guard.evaluate(Call("close_app", {"app_name": f"App{i}"})).confirmation_id for i in range(3)]
    assert guard.pending_count() == 2
    assert not guard.confirm(ids[0], True).authorized  # oldest was dropped
    assert guard.confirm(ids[2], True).authorized


# ---- dry run -----------------------------------------------------------------------

def test_dry_run_describes_but_never_authorizes():
    d = ev("open_app", dry_run=True, app_name="Safari")
    assert d.outcome is Outcome.DRY_RUN and not d.authorized
    assert d.message == "Dry run: I would open app (app_name: Safari). Nothing was done."


def test_dry_run_of_risky_action_creates_no_pending_confirmation():
    guard = make_guard()
    d = guard.evaluate(Call("close_app", {"app_name": "Safari"}), dry_run=True)
    assert d.outcome is Outcome.DRY_RUN and d.confirmation_id is None and guard.pending_count() == 0
    assert "ask for your confirmation" in d.message


def test_dry_run_never_softens_a_block():
    assert ev("run_shell", dry_run=True, command="ls").outcome is Outcome.BLOCK
    assert ev("not_a_tool", dry_run=True).outcome is Outcome.BLOCK


def test_dry_run_default_comes_from_config_and_can_be_overridden():
    guard = make_guard(dry_run=True)
    assert guard.evaluate(Call("get_time")).outcome is Outcome.DRY_RUN
    assert guard.evaluate(Call("get_time"), dry_run=False).outcome is Outcome.ALLOW


# ---- system-level validation ------------------------------------------------------

@pytest.mark.parametrize("tool,path", [
    ("open_file", "/System/Library/CoreServices/Finder.app/x.txt"),
    ("open_folder", "/etc"),
    ("open_folder", "/private/var/db"),
    ("open_folder", "/tmp/../etc"),
    ("open_folder", "~/.ssh"),
    ("open_folder", "~/Library/Keychains"),
    ("open_file", "/usr/bin/python3"),
])
def test_protected_locations_need_acknowledged_approval(tool, path):
    d = ev(tool, path_or_name=path)
    assert d.outcome is Outcome.CONFIRM and d.requires_acknowledgment and d.risk is Risk.HIGH


def test_symlink_into_a_protected_location_is_caught(tmp_path):
    link = tmp_path / "innocent"
    link.symlink_to("/etc")
    assert ev("open_folder", path_or_name=str(link)).risk is Risk.HIGH


@pytest.mark.parametrize("name", ["installer.pkg", "~/Downloads/setup.dmg", "run.sh", "x.command", "Tool.app/", "evil.SCPT"])
def test_opening_files_that_can_run_code_needs_approval(name):
    assert ev("open_file", path_or_name=name).risk is Risk.HIGH


def test_opening_a_folder_with_a_code_like_name_is_fine():
    assert ev("open_folder", path_or_name="scripts.sh").outcome is Outcome.ALLOW


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "file:///etc/passwd", "ftp://example.com", "data:text/html,x", "http://",
    "mailto:a@b.com", "tel:5551234", "slack://open", "x-apple.systempreferences:com.apple.preference.security",
    "http:example.com", "HTTP://",
])
def test_non_web_addresses_are_refused(url):
    d = ev("open_url", url=url)
    assert d.outcome is Outcome.BLOCK and d.risk is Risk.CRITICAL


@pytest.mark.parametrize("url", ["example.com:8080/page", "localhost:3000/app", "HTTPS://Example.com/Path"])
def test_ports_and_uppercase_schemes_are_still_fine(url):
    assert ev("open_url", url=url).outcome is Outcome.ALLOW


@pytest.mark.parametrize("tool,args", [
    ("open_app", {"app_name": 5}),
    ("open_folder", {"path_or_name": ["/etc"]}),
    ("open_url", {"url": None}),
    ("search_chrome", {"query": {"q": "x"}}),
])
def test_non_text_arguments_are_refused(tool, args):
    assert ev(tool, **args).outcome is Outcome.BLOCK


@pytest.mark.parametrize("url", [
    "https://accounts.google.com/signin/v2", "https://mybank.com/login", "https://shop.com/checkout",
    "https://example.com/sign-in", "example.com/log_in",
])
def test_login_and_payment_pages_ask_first(url):
    d = ev("open_url", url=url)
    assert d.outcome is Outcome.CONFIRM and d.risk is Risk.MEDIUM and "login or payment" in d.reason


def test_closing_core_macos_apps_needs_acknowledged_approval():
    for name in ("Finder", "finder", "Dock", "loginwindow", "Finder.app"):
        d = ev("close_app", app_name=name)
        assert d.risk is Risk.HIGH and d.requires_acknowledgment, name
    assert ev("close_app", app_name="Safari").risk is Risk.MEDIUM
    assert ev("open_app", app_name="Finder").outcome is Outcome.ALLOW


@pytest.mark.parametrize("level", [-1, 101, 1000, "loud", True, None, 3.5])
def test_volume_must_be_0_to_100(level):
    assert ev("set_volume", level=level).outcome is Outcome.BLOCK


def test_valid_volume_still_asks():
    assert ev("set_volume", level=0).outcome is Outcome.CONFIRM
    assert ev("set_volume", level=100).outcome is Outcome.CONFIRM


@pytest.mark.parametrize("tool,args", [
    ("open_app", {"app_name": "Safari\n"}),
    ("open_folder", {"path_or_name": "a\x00b"}),
    ("search_chrome", {"query": "x\r\ny"}),
    ("save_note", {"title": "t", "content": "bell\x07"}),
])
def test_control_characters_are_refused(tool, args):
    assert ev(tool, **args).outcome is Outcome.BLOCK


def test_arguments_can_raise_risk_but_never_lower_it():
    # close_app is medium; a harmless name must not make it low
    assert ev("close_app", app_name="Notes").risk is Risk.MEDIUM


# ---- audit log -----------------------------------------------------------------------

def read_audit(tmp_path):
    return [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]


def test_every_decision_is_logged(tmp_path):
    guard = make_guard(tmp_path)
    guard.evaluate(Call("open_app", {"app_name": "Safari"}))
    asked = guard.evaluate(Call("close_app", {"app_name": "Safari"}))
    guard.confirm(asked.confirmation_id, True)
    guard.evaluate(Call("run_shell", {"command": "ls"}))
    events = read_audit(tmp_path)
    assert [(e["stage"], e["outcome"], e["tool"]) for e in events] == [
        ("evaluate", "allow", "open_app"),
        ("evaluate", "confirm", "close_app"),
        ("confirm", "allow", "close_app"),
        ("evaluate", "block", "run_shell"),
    ]
    assert events[2]["approved"] is True and "at" in events[0]


def test_audit_entry_exists_before_the_decision_is_used(tmp_path):
    seen = []

    class Spy(AuditLog):
        def write(self, event):
            seen.append(event["outcome"])
            super().write(event)

    guard = GuardrailManager(POLICY, Spy(tmp_path / "a.jsonl"))
    d = guard.evaluate(Call("open_app", {"app_name": "Safari"}))
    assert seen == ["allow"] and d.authorized


def test_long_values_are_truncated_in_the_log(tmp_path):
    guard = make_guard(tmp_path)
    guard.evaluate(Call("save_note", {"title": "t", "content": "x" * 5000}))
    assert len(read_audit(tmp_path)[0]["args"]["content"]) < 100


def test_if_the_log_cannot_be_written_nothing_is_authorized(tmp_path):
    class Broken(AuditLog):
        def write(self, event):
            raise OSError("disk full")

    guard = GuardrailManager(POLICY, Broken(tmp_path / "a.jsonl"))
    assert guard.evaluate(Call("open_app", {"app_name": "Safari"})).outcome is Outcome.BLOCK
    d = guard.evaluate(Call("close_app", {"app_name": "Safari"}))
    assert d.outcome is Outcome.BLOCK and guard.pending_count() == 0


def test_log_rotation_keeps_one_backup(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl", max_bytes=200)
    for i in range(20):
        log.write({"n": i, "pad": "x" * 50})
    assert (tmp_path / "a.jsonl.1").exists() and (tmp_path / "a.jsonl").stat().st_size < 400


# ---- separation of safety and execution -------------------------------------------------

def test_safety_package_does_not_import_backend_or_executors():
    for path in (ROOT / "safety").glob("*.py"):
        text = path.read_text()
        assert not re.search(r"^\s*(from|import)\s+(backend|automation|voice)\b", text, re.M), path.name
        assert not re.search(r"\b(subprocess|os\.system|osascript|shutil\.(rmtree|move))\b", text), path.name


# ---- end to end through the HTTP API ------------------------------------------------------

def api(model_json, tmp_path, **guard_kw):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"role": "assistant", "content": model_json}})
        return httpx.Response(200, json={"version": "0", "models": [{"name": "m"}]})

    llm = OllamaClient("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(handler))
    cfg = {"ollama": {}, "router": {"max_retries": 0}}
    return TestClient(create_app(cfg, llm, guard=make_guard(tmp_path, **guard_kw), actions=quick_actions()))


def call_json(tool, **args):
    return json.dumps({"tool": tool, "args": args, "requires_confirmation": False})


def test_api_safe_request_is_authorized(tmp_path):
    with api(call_json("open_app", app_name="Safari"), tmp_path) as c:
        g = c.post("/route", json={"text": "open safari"}).json()["guard"]
    assert g["outcome"] == "allow" and g["authorized"] is True


def test_api_invented_destructive_tool_never_reaches_execution(tmp_path):
    with api(call_json("delete_file", path="/Users/me/doc.txt"), tmp_path) as c:
        body = c.post("/route", json={"text": "delete my file"}).json()
    assert body["fallback"] is True and body["tool"] == "reply"
    assert body["guard"]["tool"] == "reply"
    assert body["tool"] != "delete_file"


def test_api_confirmation_round_trip(tmp_path):
    with api(call_json("close_app", app_name="Safari"), tmp_path) as c:
        g = c.post("/route", json={"text": "quit safari"}).json()["guard"]
        assert g["outcome"] == "confirm" and g["authorized"] is False
        assert g["message"].endswith("Please confirm.")
        ok = c.post("/confirm", json={"confirmation_id": g["confirmation_id"], "approved": True}).json()
        assert ok["authorized"] is True and ok["args"] == {"app_name": "Safari"}
        again = c.post("/confirm", json={"confirmation_id": g["confirmation_id"], "approved": True}).json()
        assert again["authorized"] is False


def test_api_dry_run(tmp_path):
    with api(call_json("close_app", app_name="Safari"), tmp_path) as c:
        g = c.post("/route", json={"text": "quit safari", "dry_run": True}).json()["guard"]
    assert g["outcome"] == "dry_run" and g["authorized"] is False and g["confirmation_id"] is None


def test_api_confirm_validates_input(tmp_path):
    with api(call_json("get_time"), tmp_path) as c:
        assert c.post("/confirm", json={"approved": True}).status_code == 422
        assert c.post("/confirm", json={"confirmation_id": "", "approved": True}).status_code == 422
        assert c.post("/confirm", json={"confirmation_id": "abc", "approved": True}).json()["authorized"] is False


def test_the_real_tool_call_type_works_with_the_guard():
    d = GuardrailManager(POLICY).evaluate(ToolCall("open_app", {"app_name": "Safari"}))
    assert d.authorized
