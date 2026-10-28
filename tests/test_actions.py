import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from automation.apps import AppCatalog, bundle_id
from automation.files import FileFinder, parse_file_query
from automation.fuzzy import normalize, score
from automation.hammerspoon import HammerspoonClient
from automation.service import ActionResult, Chosen
from backend.config import ROOT
from backend.llm import OllamaClient
from backend.main import create_app
from backend.pipeline import Pipeline
from fakes import APPS, FakeRun, make_app, make_world
from safety import AuditLog, GuardrailManager, Outcome, Policy

POLICY = Policy.load(ROOT / "safety" / "policy.yaml")


def touch(path: Path, text: str = "x", mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path)


# ---- fuzzy matching ---------------------------------------------------------------------------------------

def test_normalizing_names():
    assert normalize("  Google-Chrome!! ") == "google chrome"
    assert normalize("Café Résumé.PDF") == "cafe resume pdf"


def test_scores_rank_exact_above_partial_above_typo():
    exact = score("safari", "safari")
    prefix = score("safari", "safari technology preview")
    typo = score("safri", "safari")
    assert exact == 1.0 > prefix > typo >= 0.6
    assert score("safari", "notes") < 0.6


# ---- opening apps by name ------------------------------------------------------------------------------------

@pytest.mark.parametrize("said,found", [
    ("Google Chrome", "Google Chrome"),
    ("chrome", "Google Chrome"),
    ("the Chrome app", "Google Chrome"),
    ("Google Chrome please", "Google Chrome"),
    ("chorme", "Google Chrome"),              # a typo
    ("VS Code", "Visual Studio Code"),
    ("vscode", "Visual Studio Code"),
    ("visual studio", "Visual Studio Code"),
    ("safari", "Safari"),
    ("SAFARI", "Safari"),
    ("settings", "System Preferences"),       # Monterey calls it System Preferences
    ("Open VS Code", "Visual Studio Code"),   # the model sometimes leaves the verb in
    ("launch safari", "Safari"),
    ("switch to Google Chrome", "Google Chrome"),
])
def test_app_names_are_matched_forgivingly(world, said, found):
    prepared = world.service.prepare("open_app", {"app_name": said})
    assert prepared.kind == "ready", said
    assert prepared.args == {"app_name": found}


def test_open_app_by_name_launches_it(world):
    prepared = world.service.prepare("open_app", {"app_name": "Open Google Chrome"})
    assert prepared.args == {"app_name": "Google Chrome"}
    result = world.service.execute("open_app", prepared.args)
    assert (result.status, result.message, result.via) == ("done", "Opened Google Chrome.", "open")
    assert world.run.commands("open") == [["/usr/bin/open", "-a", str(world.apps_dir / "Google Chrome.app")]]


def test_an_unknown_app_is_reported_not_guessed(world):
    prepared = world.service.prepare("open_app", {"app_name": "Photoshop"})
    assert prepared.kind == "failed"
    assert "couldn't find an app called “Photoshop”" in prepared.result.message
    assert world.run.calls == []


def test_the_exact_name_wins_over_longer_names(tmp_path):
    w = make_world(tmp_path, apps={**APPS, "Safari Technology Preview": "com.apple.SafariTechPreview"})
    assert w.service.prepare("open_app", {"app_name": "Safari"}).args == {"app_name": "Safari"}


def test_apps_are_listed_from_the_standard_folders_only(tmp_path):
    inside = tmp_path / "Applications"
    elsewhere = tmp_path / "Downloads"
    make_app(inside, "Notes", "com.apple.Notes")
    make_app(elsewhere, "Sketchy", "evil.sketchy")
    (inside / "NotAnApp").mkdir()
    (inside / ".Hidden.app").mkdir()
    names = [a.name for a in AppCatalog(dirs=[str(inside)], extra={}).apps()]
    assert names == ["Notes"]


def test_bundle_ids_are_read_from_the_app(world):
    assert bundle_id(str(world.apps_dir / "Safari.app")) == "com.apple.Safari"
    assert bundle_id(str(world.apps_dir / "Nope.app")) is None


# ---- several matches: ask the user ----------------------------------------------------------------------------

def test_similar_apps_ask_which_one(tmp_path):
    w = make_world(tmp_path, apps={**APPS, "Google Chrome Canary": "com.google.Chrome.canary"})
    prepared = w.service.prepare("open_app", {"app_name": "chrome"})
    assert prepared.kind == "choose"
    options = prepared.result.choice["options"]
    assert [o["label"] for o in options] == ["Google Chrome", "Google Chrome Canary"]
    assert prepared.result.status == "choose" and "Which app" in prepared.result.choice["prompt"]
    assert w.run.calls == []                                   # nothing opened while we wait

    chosen = w.service.choose(prepared.result.choice["choice_id"], "2")
    assert isinstance(chosen, Chosen)
    assert (chosen.tool, chosen.args) == ("open_app", {"app_name": "Google Chrome Canary"})


def test_the_exact_name_does_not_ask_even_when_longer_names_exist(tmp_path):
    w = make_world(tmp_path, apps={**APPS, "Google Chrome Canary": "com.google.Chrome.canary"})
    assert w.service.prepare("open_app", {"app_name": "Google Chrome"}).kind == "ready"


def test_choices_are_single_use_and_cannot_be_forged(tmp_path):
    w = make_world(tmp_path, apps={**APPS, "Google Chrome Canary": "com.google.Chrome.canary"})
    cid = w.service.prepare("open_app", {"app_name": "chrome"}).result.choice["choice_id"]
    bad = w.service.choose(cid, "99")
    assert isinstance(bad, ActionResult) and "wasn't one of the options" in bad.message
    again = w.service.choose(cid, "1")                          # the failed attempt used it up
    assert isinstance(again, ActionResult) and "expired" in again.message
    assert "expired" in w.service.choose("not-a-real-id", "1").message


def test_choices_expire_and_can_be_cancelled(tmp_path):
    clock = [0.0]
    w = make_world(tmp_path, apps={**APPS, "Google Chrome Canary": "com.google.Chrome.canary"})
    w.service._clock = lambda: clock[0]
    cid = w.service.prepare("open_app", {"app_name": "chrome"}).result.choice["choice_id"]
    clock[0] = 500
    assert "expired" in w.service.choose(cid, "1").message
    cid = w.service.prepare("open_app", {"app_name": "chrome"}).result.choice["choice_id"]
    assert w.service.choose(cid, None).message == "Cancelled."


def test_the_list_of_pending_choices_is_bounded(tmp_path):
    w = make_world(tmp_path, apps={**APPS, "Google Chrome Canary": "com.google.Chrome.canary"})
    ids = [w.service.prepare("open_app", {"app_name": "chrome"}).result.choice["choice_id"] for _ in range(7)]
    assert len(w.service._choices) == 5
    assert "expired" in w.service.choose(ids[0], "1").message


# ---- focusing and closing apps -----------------------------------------------------------------------------------

def test_focus_uses_applescript_arguments_not_pasted_names(world):
    result = world.service.execute("focus_app", {"app_name": "Safari"})
    assert result.status == "done" and result.message == "Switched to Safari."
    activate = [c for c in world.run.calls if any("activate" in part for part in c)]
    assert activate and activate[0][-1] == "com.apple.Safari"        # the app is a trailing argument...
    assert all("Safari" not in part for part in activate[0][:-1])     # ...never part of the script text


def test_focus_does_not_start_an_app_that_is_not_running(tmp_path):
    w = make_world(tmp_path, run=FakeRun(running=False))
    result = w.service.execute("focus_app", {"app_name": "Safari"})
    assert (result.status, result.message) == ("failed", "Safari isn't running.")
    assert not any(any("activate" in part for part in c) for c in w.run.calls)


def test_close_asks_the_app_to_quit_only_if_it_is_running(tmp_path, world):
    result = world.service.execute("close_app", {"app_name": "Safari"})
    assert (result.status, result.message) == ("done", "Asked Safari to quit.")
    assert any(any("to quit" in part for part in c) for c in world.run.calls)

    idle = make_world(tmp_path / "second", run=FakeRun(running=False))
    result = idle.service.execute("close_app", {"app_name": "Safari"})
    assert result.message == "Safari isn't running."
    assert not any(any("to quit" in part for part in c) for c in idle.run.calls)


def test_a_failed_launch_is_reported(tmp_path):
    w = make_world(tmp_path, run=FakeRun(returncode=1))
    result = w.service.execute("open_app", {"app_name": "Safari"})
    assert (result.status, result.message) == ("failed", "I couldn't open Safari.")


# ---- Hammerspoon first, with fallbacks ----------------------------------------------------------------------------

TOKEN = "t" * 40


def hs_client(tmp_path, handler):
    token = tmp_path / ".token"
    token.write_text(TOKEN)
    return HammerspoonClient("http://127.0.0.1:8767", token, transport=httpx.MockTransport(handler))


def test_hammerspoon_launches_apps_when_it_is_running(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers.get("x-assistant-token"), json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "launched": True})

    w = make_world(tmp_path, hammerspoon=hs_client(tmp_path, handler))
    result = w.service.execute("open_app", {"app_name": "Google Chrome"})
    assert (result.status, result.via) == ("done", "Hammerspoon")
    assert w.run.calls == []                                      # no fallback was needed
    token, body = seen[0]
    assert token == TOKEN
    assert body == {"action": "open_app",
                    "args": {"bundle_id": "com.google.Chrome", "path": str(w.apps_dir / "Google Chrome.app")}}


def test_hammerspoon_focus_and_quit(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        action = json.loads(request.content)["action"]
        return httpx.Response(200, json={"ok": True, "running": action != "never"})

    w = make_world(tmp_path, hammerspoon=hs_client(tmp_path, handler))
    assert w.service.execute("focus_app", {"app_name": "Safari"}).via == "Hammerspoon"
    assert w.service.execute("close_app", {"app_name": "Safari"}).message == "Asked Safari to quit."
    assert w.run.calls == []


def test_without_hammerspoon_apps_still_open(tmp_path):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    w = make_world(tmp_path, hammerspoon=hs_client(tmp_path, down))
    result = w.service.execute("open_app", {"app_name": "Safari"})
    assert (result.status, result.via) == ("done", "open")
    assert w.run.commands("open")


def test_when_hammerspoon_cannot_launch_the_fallback_is_used(tmp_path):
    w = make_world(tmp_path, hammerspoon=hs_client(tmp_path, lambda r: httpx.Response(200, json={"ok": True, "launched": False})))
    assert w.service.execute("open_app", {"app_name": "Safari"}).via == "open"


def test_hammerspoon_client_is_strict(tmp_path):
    token = tmp_path / ".token"
    with pytest.raises(ValueError):
        HammerspoonClient("http://192.168.1.5:8767", token)
    with pytest.raises(ValueError):
        HammerspoonClient("https://example.com", token)
    ok = lambda r: httpx.Response(200, json={"ok": True})        # noqa: E731
    assert not HammerspoonClient("http://127.0.0.1:8767", token, transport=httpx.MockTransport(ok)).available()   # no token yet
    token.write_text(TOKEN)
    for response in (httpx.Response(401, json={"ok": False}), httpx.Response(200, text="<html>"),
                     httpx.Response(200, json={"ok": False, "error": "nope"}), httpx.Response(500, json={})):
        client = HammerspoonClient("http://127.0.0.1:8767", token, transport=httpx.MockTransport(lambda r, x=response: x))
        assert not client.available()
    assert HammerspoonClient("http://127.0.0.1:8767", token, transport=httpx.MockTransport(ok)).available()


# ---- opening files by fuzzy name -------------------------------------------------------------------------------------

def test_the_pdf_called_budget_is_found_and_opened(world):
    pdf = touch(world.home / "Documents" / "Budget 2024.pdf")
    touch(world.home / "Documents" / "Budget 2024.xlsx")           # same name, other type: the PDF is what was asked for
    prepared = world.service.prepare("open_file", {"path_or_name": "the PDF called budget"})
    assert prepared.kind == "ready" and prepared.args == {"path_or_name": str(pdf)}
    result = world.service.execute("open_file", prepared.args)
    assert (result.status, result.message) == ("done", "Opened Budget 2024.pdf.")
    assert world.run.commands("open") == [["/usr/bin/open", str(pdf)]]


@pytest.mark.parametrize("said", ["budget", "budget.pdf", "Budget 2024", "budget pdf", "my budget file", "budjet pdf"])
def test_file_names_are_matched_forgivingly(world, said):
    pdf = touch(world.home / "Documents" / "Budget 2024.pdf")
    prepared = world.service.prepare("open_file", {"path_or_name": said})
    assert prepared.kind == "ready", said
    assert prepared.args == {"path_or_name": str(pdf)}


def test_file_type_words_narrow_the_search():
    q = parse_file_query("the PDF called budget")
    assert (q.terms, q.extensions) == ("budget", frozenset({".pdf"}))
    assert parse_file_query("budget.xlsx").extensions == frozenset({".xlsx"})
    assert parse_file_query("report v1.2").terms == "report v1 2"      # a version number is not an extension
    assert parse_file_query("photo of the beach").extensions >= {".jpg", ".png"}


def test_an_exact_path_is_opened_as_is(world):
    f = touch(world.home / "Documents" / "plan.txt")
    other = touch(world.home / "Documents" / "plan notes.txt")
    prepared = world.service.prepare("open_file", {"path_or_name": str(f)})
    assert prepared.args == {"path_or_name": str(f)}          # no guessing, even though a similar name exists
    assert world.service.execute("open_file", prepared.args).message == "Opened plan.txt."
    assert world.service.prepare("open_file", {"path_or_name": "~/Documents/plan.txt"}).args == {"path_or_name": str(f)}
    assert world.service.prepare("open_file", {"path_or_name": "Documents/plan.txt"}).args == {"path_or_name": str(f)}
    assert other.exists()


def test_exact_paths_are_cleaned_up(world):
    f = touch(world.home / "Documents" / "plan.txt")
    messy = f"{world.home}/Documents/../Documents/./plan.txt"
    assert world.service.prepare("open_file", {"path_or_name": messy}).args == {"path_or_name": str(f)}


def test_a_path_that_does_not_exist_is_not_guessed(world):
    touch(world.home / "Documents" / "plan.txt")
    prepared = world.service.prepare("open_file", {"path_or_name": str(world.home / "Documents" / "plan2.txt")})
    assert prepared.kind == "failed" and "There is nothing at" in prepared.result.message
    assert world.run.calls == []


def test_executing_needs_a_real_existing_path(world):
    assert world.service.execute("open_file", {"path_or_name": "budget"}).status == "failed"
    gone = world.service.execute("open_file", {"path_or_name": str(world.home / "Documents" / "gone.txt")})
    assert gone.status == "failed" and "any more" in gone.message
    assert world.run.calls == []


def test_folders_by_well_known_name(world):
    for said in ("Downloads", "my Downloads folder", "the downloads directory", "DOWNLOADS"):
        prepared = world.service.prepare("open_folder", {"path_or_name": said})
        assert prepared.args == {"path_or_name": str(world.home / "Downloads")}, said
    assert world.service.prepare("open_folder", {"path_or_name": "home"}).args == {"path_or_name": str(world.home)}
    result = world.service.execute("open_folder", {"path_or_name": str(world.home / "Downloads")})
    assert (result.status, result.message) == ("done", "Opened the Downloads folder.")
    assert world.run.commands("open") == [["/usr/bin/open", str(world.home / "Downloads")]]


def test_folders_are_found_by_name_and_files_are_not_mistaken_for_them(world):
    (world.home / "Documents" / "Projects" / "Alpha").mkdir(parents=True)
    touch(world.home / "Documents" / "alpha.txt")
    prepared = world.service.prepare("open_folder", {"path_or_name": "alpha"})
    assert prepared.args == {"path_or_name": str(world.home / "Documents" / "Projects" / "Alpha")}
    assert world.service.prepare("open_folder", {"path_or_name": str(world.home / "Documents" / "alpha.txt")}).kind == "failed"
    files_only = world.service.prepare("open_file", {"path_or_name": "alpha"})
    assert files_only.args == {"path_or_name": str(world.home / "Documents" / "alpha.txt")}


def test_nothing_found_says_where_it_looked(world):
    prepared = world.service.prepare("open_file", {"path_or_name": "tax return"})
    assert prepared.kind == "failed"
    assert "Desktop, Documents, Downloads and a few other folders" in prepared.result.message


# ---- several matches: ask the user -----------------------------------------------------------------------------------------

def test_the_same_file_in_two_folders_asks_which(world):
    a = touch(world.home / "Documents" / "budget.pdf")
    b = touch(world.home / "Downloads" / "budget.pdf")
    prepared = world.service.prepare("open_file", {"path_or_name": "budget.pdf"})
    assert prepared.kind == "choose"
    options = prepared.result.choice["options"]
    assert {o["detail"] for o in options} == {"~/Documents", "~/Downloads"}
    assert "2 files" in prepared.result.choice["prompt"]
    assert world.run.calls == []

    by_dir = {o["detail"]: o["id"] for o in options}
    chosen = world.service.choose(prepared.result.choice["choice_id"], by_dir["~/Downloads"])
    assert (chosen.tool, chosen.args) == ("open_file", {"path_or_name": str(b)})
    assert a.exists()


def test_different_types_with_the_same_name_ask_when_no_type_was_given(world):
    touch(world.home / "Desktop" / "notes.txt")
    touch(world.home / "Desktop" / "notes.md")
    prepared = world.service.prepare("open_file", {"path_or_name": "notes"})
    assert prepared.kind == "choose" and len(prepared.result.choice["options"]) == 2


def test_the_choice_list_is_short(world):
    for i in range(9):
        touch(world.home / "Documents" / f"report {i}.txt")
    prepared = world.service.prepare("open_file", {"path_or_name": "report"})
    assert prepared.kind == "choose" and len(prepared.result.choice["options"]) == 5


def test_a_confirmation_flag_survives_a_choice(tmp_path):
    w = make_world(tmp_path, apps={**APPS, "Google Chrome Canary": "com.google.Chrome.canary"})
    cid = w.service.prepare("close_app", {"app_name": "chrome"}, requires_confirmation=True).result.choice["choice_id"]
    assert w.service.choose(cid, "1").requires_confirmation is True


# ---- searching only where it is safe ---------------------------------------------------------------------------------------

def test_hidden_noisy_and_package_folders_are_never_searched(world):
    docs = world.home / "Documents"
    touch(docs / ".git" / "budget.pdf")
    touch(docs / ".hidden" / "budget.pdf")
    touch(docs / "node_modules" / "pkg" / "budget.pdf")
    touch(docs / "Library" / "budget.pdf")
    touch(docs / "venv" / "lib" / "budget.pdf")
    touch(docs / "Thing.app" / "Contents" / "budget.pdf")
    touch(docs / "Photos.photoslibrary" / "budget.pdf")
    touch(docs / ".budget.pdf")
    assert world.service.prepare("open_file", {"path_or_name": "budget.pdf"}).kind == "failed"
    safe = touch(docs / "Real" / "budget.pdf")
    assert world.service.prepare("open_file", {"path_or_name": "budget.pdf"}).args == {"path_or_name": str(safe)}


def test_symbolic_links_are_never_followed(world, tmp_path):
    outside = tmp_path / "outside"
    touch(outside / "secret budget.pdf")
    (world.home / "Documents" / "link").symlink_to(outside, target_is_directory=True)
    (world.home / "Documents" / "budget link.pdf").symlink_to(outside / "secret budget.pdf")
    assert world.service.prepare("open_file", {"path_or_name": "budget"}).kind == "failed"


def test_search_roots_outside_the_home_folder_are_dropped(tmp_path):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    touch(outside / "budget.pdf")
    finder = FileFinder(["/", "/etc", str(outside), "~/Documents", "~/Missing"], ["/usr"], home=home)
    assert finder.primary == [(home / "Documents").resolve()]
    assert finder.secondary == []
    assert finder.locate("budget").kind == "none"                 # the file outside is unreachable


def test_the_home_folder_is_listed_but_not_entered(tmp_path):
    home = tmp_path / "home"
    touch(home / "top level.pdf")
    touch(home / "Elsewhere" / "deep" / "buried.pdf")
    finder = FileFinder([], ["~"], home=home)
    assert finder.locate("top level").kind == "one"
    assert finder.locate("buried").kind == "none"


def test_the_first_tier_is_enough_when_it_has_an_answer(world):
    wanted = touch(world.home / "Documents" / "holiday.jpg")
    touch(world.home / "Pictures" / "holiday.jpg")                  # a second copy in the second tier
    prepared = world.service.prepare("open_file", {"path_or_name": "holiday.jpg"})
    assert prepared.kind == "ready" and prepared.args == {"path_or_name": str(wanted)}


def test_the_second_tier_is_used_only_when_needed(world):
    pic = touch(world.home / "Pictures" / "sunset.jpg")
    assert world.service.prepare("open_file", {"path_or_name": "sunset"}).args == {"path_or_name": str(pic)}


def test_searches_stop_at_the_depth_limit(tmp_path):
    home = tmp_path / "home"
    touch(home / "Documents" / "a" / "b" / "c" / "d" / "e" / "too deep.txt")
    touch(home / "Documents" / "a" / "b" / "reachable.txt")
    finder = FileFinder(["~/Documents"], [], home=home, max_depth=3)
    assert finder.locate("reachable").kind == "one"
    assert finder.locate("too deep").kind == "none"


def test_big_folders_stop_the_search_early(tmp_path):
    home = tmp_path / "home"
    for i in range(50):
        touch(home / "Documents" / f"file{i}.txt")
    finder = FileFinder(["~/Documents"], [], home=home, max_entries=10)
    lookup = finder.locate("file49")
    assert lookup.truncated
    assert "stopped early" in make_world(tmp_path / "w").service._not_found("file49", "file", lookup)


def test_a_slow_search_stops_at_its_time_budget(tmp_path):
    home = tmp_path / "home"
    for i in range(20):
        touch(home / "Documents" / f"file{i}.txt")
    ticks = iter(range(0, 1000))
    finder = FileFinder(["~/Documents"], [], home=home, time_budget=3, clock=lambda: next(ticks))
    assert finder.locate("file19").truncated


def test_search_files_lists_what_it_found(world):
    touch(world.home / "Documents" / "budget.pdf")
    touch(world.home / "Downloads" / "budget old.pdf")
    touch(world.home / "Documents" / ".budget secret.pdf")
    result = world.service.execute("search_files", {"query": "budget"})
    assert result.status == "done" and result.message.startswith("Found 2:")
    assert len(result.items) == 2 and any("~/Documents" in item for item in result.items)
    assert world.run.calls == []                                     # searching never opens anything


def test_search_files_with_no_hits_says_so(world):
    result = world.service.execute("search_files", {"query": "nothing like this"})
    assert result.status == "done" and "couldn't find" in result.message


def test_recent_files_are_newest_first_and_limited(world):
    for i in range(12):
        touch(world.home / "Documents" / f"doc{i}.txt", mtime=1_000_000 + i)
    touch(world.home / "Documents" / ".hidden", mtime=9_999_999)
    result = world.service.execute("get_recent_files", {})
    assert result.message.startswith("Most recently changed in Desktop, Documents and Downloads: doc11.txt, doc10.txt")
    assert len(result.items) == 10 and result.items[0].startswith("doc11.txt")


def test_other_tools_are_not_available_yet_and_replies_need_no_action(world):
    assert world.service.execute("create_reminder", {"text": "x", "due_datetime": "2025-01-01T10:00:00"}).status == "not_available"
    assert world.service.execute("reply", {"text": "hi"}) is None


def test_an_unexpected_error_becomes_a_calm_failure(world, monkeypatch, caplog):
    def boom(app):
        raise RuntimeError("secret detail /Users/me/private")

    monkeypatch.setattr(world.service.controller, "open_app", boom)
    result = world.service.execute("open_app", {"app_name": "Safari"})
    assert result.status == "failed" and "secret" not in result.message


# ---- the safety layer judges the real target -----------------------------------------------------------------------------------

@pytest.fixture
def pipeline(world, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(world.home))        # so "~/.ssh" in the policy means the fake home's .ssh
    guard = GuardrailManager(POLICY, AuditLog(tmp_path / "audit.jsonl"))
    return Pipeline(guard, world.service), world, tmp_path / "audit.jsonl"


def test_an_allowed_app_is_resolved_then_judged_then_opened(pipeline):
    p, world, audit = pipeline
    out = p.run("open_app", {"app_name": "chrome"})
    assert out.decision.outcome is Outcome.ALLOW and out.decision.args == {"app_name": "Google Chrome"}
    assert out.result.status == "done"
    assert world.run.commands("open")
    events = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [e["stage"] for e in events] == ["evaluate", "execute"]
    assert events[1]["status"] == "done"


def test_a_vague_name_that_resolves_to_a_protected_app_needs_acknowledgment(pipeline):
    p, world, _ = pipeline
    out = p.run("close_app", {"app_name": "fin"})                    # resolves to Finder
    assert out.decision.args == {"app_name": "Finder"}
    assert out.decision.outcome is Outcome.CONFIRM and out.decision.requires_acknowledgment
    assert out.result is None and world.run.calls == []              # nothing happened yet


def test_closing_an_app_asks_first_then_acts_only_after_approval(pipeline):
    p, world, _ = pipeline
    out = p.run("close_app", {"app_name": "safari"})
    assert out.decision.outcome is Outcome.CONFIRM and "Safari" in out.decision.message
    assert out.result is None and world.run.calls == []

    approved = p.guard.confirm(out.decision.confirmation_id, True)
    result = p.after_confirmation(approved)
    assert result.message == "Asked Safari to quit."
    assert any(any("to quit" in part for part in c) for c in world.run.calls)


def test_denying_never_acts(pipeline):
    p, world, _ = pipeline
    out = p.run("close_app", {"app_name": "safari"})
    denied = p.guard.confirm(out.decision.confirmation_id, False)
    assert p.after_confirmation(denied) is None and world.run.calls == []


def test_a_name_that_resolves_into_a_protected_folder_is_stopped(pipeline):
    p, world, _ = pipeline
    (world.home / ".ssh").mkdir()
    out = p.run("open_folder", {"path_or_name": "~/.ssh"})
    assert out.decision.outcome is Outcome.CONFIRM and out.decision.requires_acknowledgment
    assert out.result is None and world.run.calls == []


def test_a_found_installer_needs_acknowledgment_before_opening(pipeline):
    p, world, _ = pipeline
    touch(world.home / "Downloads" / "setup tool.pkg")
    out = p.run("open_file", {"path_or_name": "setup tool"})
    assert out.decision.outcome is Outcome.CONFIRM and out.decision.requires_acknowledgment
    assert world.run.calls == []
    approved = p.guard.confirm(out.decision.confirmation_id, True, acknowledge_risk=True)
    assert p.after_confirmation(approved).status == "done"
    assert world.run.commands("open")


def test_dry_run_resolves_but_never_acts(pipeline):
    p, world, _ = pipeline
    out = p.run("open_app", {"app_name": "chrome"}, dry_run=True)
    assert out.decision.outcome is Outcome.DRY_RUN and "Google Chrome" in out.decision.message
    assert out.result is None and world.run.calls == []


def test_not_found_is_reported_without_a_safety_decision(pipeline):
    p, world, audit = pipeline
    out = p.run("open_app", {"app_name": "Photoshop"})
    assert out.decision is None and out.result.status == "failed"
    assert not audit.exists()                                          # nothing was judged or done


def test_a_choice_is_judged_like_any_other_call(pipeline):
    p, world, _ = pipeline
    touch(world.home / "Documents" / "setup.pkg")
    touch(world.home / "Downloads" / "setup.pkg")
    asked = p.run("open_file", {"path_or_name": "setup.pkg"})
    assert asked.decision is None and asked.result.status == "choose" and world.run.calls == []
    options = asked.result.choice["options"]
    picked = p.choose(asked.result.choice["choice_id"], options[0]["id"])
    assert picked.decision.outcome is Outcome.CONFIRM and picked.decision.requires_acknowledgment    # .pkg: risky
    assert picked.result is None and world.run.calls == []


def test_choosing_a_safe_file_opens_it(pipeline):
    p, world, _ = pipeline
    a = touch(world.home / "Documents" / "budget.pdf")
    touch(world.home / "Downloads" / "budget.pdf")
    asked = p.run("open_file", {"path_or_name": "budget.pdf"})
    by_detail = {o["detail"]: o["id"] for o in asked.result.choice["options"]}
    done = p.choose(asked.result.choice["choice_id"], by_detail["~/Documents"])
    assert done.decision.outcome is Outcome.ALLOW and done.result.status == "done"
    assert world.run.commands("open") == [["/usr/bin/open", str(a)]]


def test_cancelling_a_choice_does_nothing(pipeline):
    p, world, _ = pipeline
    touch(world.home / "Documents" / "budget.pdf")
    touch(world.home / "Downloads" / "budget.pdf")
    asked = p.run("open_file", {"path_or_name": "budget.pdf"})
    out = p.choose(asked.result.choice["choice_id"], None)
    assert out.decision is None and out.result.message == "Cancelled." and world.run.calls == []


def test_the_action_service_is_only_run_for_authorized_calls(pipeline, monkeypatch):
    p, world, _ = pipeline
    ran = []
    real = p.actions.execute
    monkeypatch.setattr(p.actions, "execute", lambda tool, args: ran.append(tool) or real(tool, args))
    p.run("close_app", {"app_name": "safari"})                           # needs confirmation
    p.run("open_app", {"app_name": "safari"}, dry_run=True)              # dry run
    p.run("open_app", {"app_name": "nonexistent app"})                   # not found
    p.run("run_shell", {"command": "ls"})                                # not a tool at all
    assert ran == []
    p.run("open_app", {"app_name": "safari"})
    assert ran == ["open_app"]


# ---- over HTTP ---------------------------------------------------------------------------------------------------------------

def api(model_json, world, tmp_path, **kw):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"role": "assistant", "content": model_json}})
        return httpx.Response(200, json={"version": "0", "models": [{"name": "m"}]})

    llm = OllamaClient("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(handler))
    guard = GuardrailManager(POLICY, AuditLog(tmp_path / "audit.jsonl"))
    cfg = {"ollama": {}, "router": {"max_retries": 0}}
    return TestClient(create_app(cfg, llm, guard=guard, actions=world.service, **kw))


def call_json(tool, **args):
    return json.dumps({"tool": tool, "args": args, "requires_confirmation": False})


def test_api_opens_an_app_and_reports_the_result(world, tmp_path):
    with api(call_json("open_app", app_name="Open VS Code"), world, tmp_path) as c:
        body = c.post("/route", json={"text": "open vs code"}).json()
    assert body["tool"] == "open_app" and body["args"] == {"app_name": "Open VS Code"}   # what the model said
    assert body["guard"]["args"] == {"app_name": "Visual Studio Code"}                   # what was judged
    assert body["result"]["status"] == "done" and body["result"]["message"] == "Opened Visual Studio Code."
    assert world.run.commands("open")[0][-1].endswith("Visual Studio Code.app")


def test_api_asks_which_one_and_continues_after_the_answer(world, tmp_path):
    a = touch(world.home / "Documents" / "budget.pdf")
    touch(world.home / "Downloads" / "budget.pdf")
    with api(call_json("open_file", path_or_name="budget.pdf"), world, tmp_path) as c:
        asked = c.post("/route", json={"text": "open budget"}).json()
        assert asked["guard"] is None and asked["result"]["status"] == "choose"
        choice = asked["result"]["choice"]
        assert world.run.calls == []
        pick = next(o["id"] for o in choice["options"] if o["detail"] == "~/Documents")

        done = c.post("/choose", json={"choice_id": choice["choice_id"], "option_id": pick}).json()
        assert done["result"]["status"] == "done" and done["guard"]["outcome"] == "allow"
        assert world.run.commands("open") == [["/usr/bin/open", str(a)]]

        stale = c.post("/choose", json={"choice_id": choice["choice_id"], "option_id": pick}).json()
        assert stale["result"]["status"] == "failed" and "expired" in stale["result"]["message"]
        assert len(world.run.commands("open")) == 1


def test_api_cancelling_a_choice(world, tmp_path):
    touch(world.home / "Documents" / "budget.pdf")
    touch(world.home / "Downloads" / "budget.pdf")
    with api(call_json("open_file", path_or_name="budget.pdf"), world, tmp_path) as c:
        cid = c.post("/route", json={"text": "open budget"}).json()["result"]["choice"]["choice_id"]
        out = c.post("/choose", json={"choice_id": cid}).json()
    assert out["result"]["message"] == "Cancelled." and world.run.calls == []


def test_api_choose_validates_input(world, tmp_path):
    with api(call_json("get_time"), world, tmp_path) as c:
        assert c.post("/choose", json={}).status_code == 422
        assert c.post("/choose", json={"choice_id": ""}).status_code == 422
        assert c.post("/choose", json={"choice_id": "x", "option_id": "y" * 40}).status_code == 422


def test_api_confirmation_then_action(world, tmp_path):
    with api(call_json("close_app", app_name="safari"), world, tmp_path) as c:
        asked = c.post("/route", json={"text": "quit safari"}).json()
        assert asked["guard"]["outcome"] == "confirm" and asked["result"] is None
        assert world.run.calls == []
        done = c.post("/confirm", json={"confirmation_id": asked["guard"]["confirmation_id"], "approved": True}).json()
    assert done["authorized"] is True and done["result"]["message"] == "Asked Safari to quit."


def test_api_denied_confirmation_does_nothing(world, tmp_path):
    with api(call_json("close_app", app_name="safari"), world, tmp_path) as c:
        asked = c.post("/route", json={"text": "quit safari"}).json()
        out = c.post("/confirm", json={"confirmation_id": asked["guard"]["confirmation_id"], "approved": False}).json()
    assert out["authorized"] is False and out["result"] is None and world.run.calls == []


def test_api_dry_run_does_nothing(world, tmp_path):
    with api(call_json("open_app", app_name="safari"), world, tmp_path) as c:
        body = c.post("/route", json={"text": "open safari", "dry_run": True}).json()
    assert body["guard"]["outcome"] == "dry_run" and body["result"] is None and world.run.calls == []


def test_api_replies_and_unbuilt_tools(world, tmp_path):
    with api(call_json("reply", text="Hello."), world, tmp_path) as c:
        body = c.post("/route", json={"text": "hi"}).json()
        assert body["guard"]["outcome"] == "allow" and body["result"] is None
    with api(call_json("create_reminder", text="x", due_datetime="2025-01-01T10:00:00"), world, tmp_path) as c:
        body = c.post("/route", json={"text": "remind me"}).json()
        assert body["result"]["status"] == "not_available"


def test_api_automation_status(world, tmp_path):
    with api(call_json("get_time"), world, tmp_path) as c:
        body = c.get("/automation/status").json()
    assert body["hammerspoon"] is False and body["apps_found"] == len(APPS)
    assert body["searched_first"] == [str(world.home / d) for d in ("Desktop", "Documents", "Downloads")]


def test_the_backend_creates_the_shared_token_once_at_startup(world, tmp_path):
    token = tmp_path / "data" / ".token"
    cfg = {"ollama": {}, "router": {"max_retries": 0}, "automation": {"token_file": str(token)}}
    llm = OllamaClient("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with TestClient(create_app(cfg, llm, guard=GuardrailManager(POLICY), actions=world.service)):
        pass
    first = token.read_text()
    assert len(first.strip()) >= 32 and oct(token.stat().st_mode & 0o777) == "0o600"
    with TestClient(create_app(cfg, llm, guard=GuardrailManager(POLICY), actions=world.service)):
        pass
    assert token.read_text() == first                                     # stable across restarts


def test_leading_verbs_are_removed_but_not_words_inside_names(tmp_path):
    from automation.fuzzy import strip_leading_verbs
    assert strip_leading_verbs("Open the Safari") == "the safari"
    assert strip_leading_verbs("switch to chrome") == "chrome"
    assert strip_leading_verbs("Microsoft To Do") == "microsoft to do"
    w = make_world(tmp_path, apps={"Microsoft To Do": "com.microsoft.todo", "Notes": "com.apple.Notes"})
    assert w.service.prepare("open_app", {"app_name": "microsoft to do"}).args == {"app_name": "Microsoft To Do"}


def test_names_are_never_executed_only_exact_paths(world, monkeypatch):
    """Two layers refuse a bare name even if a file by that name exists in the current folder."""
    monkeypatch.chdir(world.home)
    touch(world.home / "plan.txt")
    result = world.service.execute("open_file", {"path_or_name": "plan.txt"})
    assert result.message == "I need an exact location to open that." and world.run.calls == []
    assert world.service.controller.open_path("plan.txt") is False and world.run.calls == []


def test_a_dry_run_stays_a_dry_run_after_the_user_picks(pipeline):
    p, world, _ = pipeline
    touch(world.home / "Documents" / "budget.pdf")
    touch(world.home / "Downloads" / "budget.pdf")
    asked = p.run("open_file", {"path_or_name": "budget.pdf"}, dry_run=True)
    assert asked.result.status == "choose"
    picked = p.choose(asked.result.choice["choice_id"], asked.result.choice["options"][0]["id"])
    assert picked.decision.outcome is Outcome.DRY_RUN and picked.result is None
    assert world.run.calls == []                                   # nothing was opened


def test_a_dry_run_over_http_stays_a_dry_run_after_choosing(world, tmp_path):
    touch(world.home / "Documents" / "budget.pdf")
    touch(world.home / "Downloads" / "budget.pdf")
    with api(call_json("open_file", path_or_name="budget.pdf"), world, tmp_path) as c:
        asked = c.post("/route", json={"text": "open budget", "dry_run": True}).json()
        choice = asked["result"]["choice"]
        done = c.post("/choose", json={"choice_id": choice["choice_id"], "option_id": choice["options"][0]["id"]}).json()
    assert done["guard"]["outcome"] == "dry_run" and done["result"] is None and world.run.calls == []
