import json
from datetime import datetime

import pytest

from backend.prompt import _examples, build_messages
from backend.tool_defs import REPLY, build_registry
from backend.toolcall import ToolCall, ToolCallError, parse_tool_call
from backend.tools import Category, Param, ToolRegistry, ToolSpec

REG = build_registry()
NOW = datetime(2025, 3, 4, 9, 30)


def parse(obj_or_text):
    text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
    return parse_tool_call(text, REG)


# ---- registry ----------------------------------------------------------------

def test_every_category_has_tools():
    for cat in Category:
        assert REG.by_category(cat), f"no tools in category {cat.value}"


def test_no_destructive_tools_are_registered():
    banned = {"delete_file", "delete_folder", "rename_file", "move_file", "run_shell", "run_command", "eval"}
    assert not banned & {t.name for t in REG.all()}


def test_registry_rejects_bad_specs():
    r = ToolRegistry()
    r.register(ToolSpec("a", Category.APP, "x"))
    with pytest.raises(ValueError):
        r.register(ToolSpec("a", Category.APP, "dup"))
    with pytest.raises(ValueError):
        r.register(ToolSpec("not valid", Category.APP, "x"))
    with pytest.raises(ValueError):
        Param("p", type="float")
    with pytest.raises(ValueError):
        r.register(ToolSpec("b", Category.APP, "x", (Param("p"), Param("p"))))


def test_describe_lists_all_tools_and_flags_confirmation():
    text = REG.describe()
    for t in REG.all():
        assert t.name in text
    assert "close_app(app_name): Quit an application [confirm]" in text
    assert "close_tab(index?:int)" in text


# ---- prompt ------------------------------------------------------------------

def test_prompt_is_small_and_complete():
    system, user = build_messages(REG, "open chrome", NOW)
    assert system["role"] == "system" and user == {"role": "user", "content": "open chrome"}
    assert len(system["content"]) < 5000  # must fit a 2048-token context with room to answer
    assert "2025-03-04 09:30" in system["content"]
    for t in REG.all():
        assert t.name in system["content"]


def test_prompt_examples_are_valid_tool_calls():
    for _, call in _examples(NOW):
        assert parse(call.to_dict()) == call


# ---- parsing and validation --------------------------------------------------

def test_valid_call_has_the_documented_shape():
    call = parse({"tool": "open_app", "args": {"app_name": "Google Chrome"}, "requires_confirmation": False})
    assert call.to_dict() == {
        "tool": "open_app", "args": {"app_name": "Google Chrome"}, "requires_confirmation": False,
    }


def test_fenced_and_wrapped_json_is_accepted():
    body = '{"tool": "get_time", "args": {}}'
    assert parse(f"```json\n{body}\n```").tool == "get_time"
    assert parse(f"Sure! {body} Hope that helps.").tool == "get_time"


def test_missing_args_and_null_args_are_fine_for_parameterless_tools():
    assert parse({"tool": "get_time"}).args == {}
    assert parse({"tool": "get_time", "args": None}).args == {}


def test_values_are_stripped_and_ints_coerced():
    assert parse({"tool": "open_app", "args": {"app_name": "  Safari "}}).args == {"app_name": "Safari"}
    assert parse({"tool": "set_volume", "args": {"level": "30"}}).args == {"level": 30}


def test_optional_args_may_be_omitted():
    assert parse({"tool": "close_tab", "args": {}}).args == {}


@pytest.mark.parametrize("raw", [
    "",
    "not json at all",
    "[]",
    '"open_app"',
    '{"tool": 5}',
    '{"args": {}}',
    '{"tool": "rm_rf", "args": {}}',
    '{"tool": "delete_file", "args": {"path": "x"}}',
    '{"tool": "open_app", "args": {}}',
    '{"tool": "open_app", "args": {"app_name": ""}}',
    '{"tool": "open_app", "args": {"app_name": "x", "extra": 1}}',
    '{"tool": "open_app", "args": {"app_name": 5}}',
    '{"tool": "open_app", "args": "Chrome"}',
    '{"tool": "set_volume", "args": {"level": true}}',
    '{"tool": "set_volume", "args": {"level": "loud"}}',
    '{"tool": "open_app", "args": {"app_name": "' + "x" * 2001 + '"}}',
])
def test_invalid_output_is_rejected(raw):
    with pytest.raises(ToolCallError):
        parse(raw)


def test_tool_name_must_match_exactly():
    with pytest.raises(ToolCallError):
        parse({"tool": "OPEN_APP", "args": {"app_name": "x"}})


@pytest.mark.parametrize("tool,args,model_flag,expected", [
    ("open_app", {"app_name": "x"}, False, False),
    ("open_app", {"app_name": "x"}, True, True),
    ("open_app", {"app_name": "x"}, "yes", True),      # garbage is treated as "ask"
    ("close_app", {"app_name": "x"}, False, True),     # the model cannot waive a required confirmation
    ("delete_reminder", {"id": 3}, False, True),
    ("set_volume", {"level": 10}, False, True),
])
def test_confirmation_can_only_be_raised(tool, args, model_flag, expected):
    call = parse({"tool": tool, "args": args, "requires_confirmation": model_flag})
    assert call.requires_confirmation is expected


def test_missing_confirmation_flag_uses_the_tool_default():
    assert parse({"tool": "open_app", "args": {"app_name": "x"}}).requires_confirmation is False
    assert parse({"tool": "close_app", "args": {"app_name": "x"}}).requires_confirmation is True


def test_reply_tool_exists_and_needs_text():
    assert REPLY in REG
    assert parse({"tool": REPLY, "args": {"text": "hi"}}) == ToolCall(REPLY, {"text": "hi"})
    with pytest.raises(ToolCallError):
        parse({"tool": REPLY, "args": {}})
