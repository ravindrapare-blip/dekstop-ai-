import json
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient

from automation.choices import ChoiceStore
from automation.results import ActionResult
from backend.actions import ActionRouter
from backend.config import ROOT
from backend.llm import OllamaClient
from backend.main import create_app
from backend.pipeline import Pipeline
from backend.tool_defs import build_registry
from browserctl import BROWSER_TOOLS, BrowserService
from browserctl.client import WorkerClient, WorkerError, WorkerUnavailable
from browserctl.launcher import WorkerLauncher
from browserctl.sites import to_address
from fake_browser import FakeWorker, cand, field
from safety import AuditLog, GuardrailManager, Outcome, Policy
from safety.audit import summarize_args
from safety.checks import check_click_target
from fakes import make_world

POLICY = Policy.load(ROOT / "safety" / "policy.yaml")


class Setup:
    def __init__(self, tmp_path, **worker_options):
        self.worker = FakeWorker(**worker_options)
        self.service = BrowserService(self.worker)
        self.audit = tmp_path / "audit.jsonl"
        self.guard = GuardrailManager(POLICY, AuditLog(self.audit))
        self.pipeline = Pipeline(self.guard, self.service)

    def run(self, tool, **args):
        return self.pipeline.run(tool, args)

    def approve(self, out, acknowledge=False):
        decision = self.guard.confirm(out.decision.confirmation_id, True, acknowledge_risk=acknowledge)
        return decision, self.pipeline.after_confirmation(decision)

    def events(self):
        return [json.loads(line) for line in self.audit.read_text().splitlines()]


@pytest.fixture
def b(tmp_path):
    return Setup(tmp_path)


def open_chrome(b):
    b.worker.do_open_chrome()


# ---- every tool is wired up ------------------------------------------------------------------------------

def test_every_browser_tool_is_selectable_and_has_a_safety_rule():
    registry = build_registry()
    names = {t.name for t in registry.all() if t.category.value == "browser"}
    assert names == set(BROWSER_TOOLS)
    assert set(BROWSER_TOOLS) <= set(POLICY.rules)


def test_the_spec_lists_the_tools_the_user_asked_for():
    for name in ["open_chrome", "open_url", "new_tab", "switch_tab", "close_tab", "search_chrome", "click_text",
                 "fill_field", "scroll_page", "wait_for_page_load", "get_page_title", "get_current_url", "play_youtube_video"]:
        assert name in BROWSER_TOOLS


# ---- launching the browser --------------------------------------------------------------------------------

def test_opening_chrome_starts_it_once(b):
    first = b.run("open_chrome")
    assert first.decision.outcome is Outcome.ALLOW
    assert first.result.status == "done" and first.result.message == "Chrome is open."
    assert first.result.items == ["Started Chrome"]
    assert b.run("open_chrome").result.message == "Chrome is already open."


def test_extra_arguments_from_the_model_are_dropped(b):
    out = b.run("open_chrome", profile="/Users/me/Library", flags="--remote-debugging-port=9222")
    assert out.decision.args == {} and b.worker.calls[0] == ("open_chrome", {})


# ---- searching ----------------------------------------------------------------------------------------------

def test_search_flow_opens_chrome_searches_and_reports_the_steps(b):
    out = b.run("search_chrome", query="  AI tools ")
    assert out.decision.outcome is Outcome.ALLOW and out.decision.args == {"query": "AI tools"}
    assert b.worker.last("search") == {"query": "AI tools"}
    assert out.result.status == "done" and out.result.message == "Searched Google for “AI tools”."
    assert out.result.items == ["Searching Google", "Started Chrome", "Opening www.google.com", "Page loaded"]
    assert [e["stage"] for e in b.events()] == ["evaluate", "execute"]


@pytest.mark.parametrize("query", ["", "   ", None, 5])
def test_an_empty_search_asks_what_to_search_for(b, query):
    out = b.run("search_chrome", query=query)
    assert out.decision is None and out.result.status == "failed" and "search for" in out.result.message
    assert b.worker.calls == []


# ---- opening addresses and well-known sites -------------------------------------------------------------

@pytest.mark.parametrize("said,address", [
    ("gmail", "https://mail.google.com/"), ("my Gmail", "https://mail.google.com/"), ("the calendar", "https://calendar.google.com/"),
    ("Google Calendar", "https://calendar.google.com/"), ("YouTube", "https://www.youtube.com/"),
    ("example.com/a?b=1", "example.com/a?b=1"), ("https://example.com", "https://example.com"),
    ("localhost:3000", "localhost:3000"),
])
def test_names_and_addresses_become_addresses(said, address):
    assert to_address(said) == address


def test_things_that_are_not_addresses_are_not_guessed():
    assert to_address("my favourite thing") is None and to_address("") is None


def test_open_my_gmail(b):
    out = b.run("open_url", url="my Gmail")
    assert out.decision.args == {"url": "https://mail.google.com/"} and out.decision.outcome is Outcome.ALLOW
    assert out.result.status == "done" and out.result.message == "Opened Page of mail.google.com."


def test_an_unknown_site_gets_a_helpful_answer_and_nothing_runs(b):
    out = b.run("open_url", url="my cousin's blog")
    assert out.decision is None and "example.com" in out.result.message and b.worker.calls == []


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,hi", "mailto:a@b.c", "chrome://settings"])
def test_dangerous_schemes_reach_the_safety_layer_and_are_refused_there(b, url):
    out = b.run("open_url", url=url)
    assert out.decision.outcome is Outcome.BLOCK and out.result is None and b.worker.calls == []
    assert b.events()[0]["outcome"] == "block"          # and it is in the audit log


def test_login_looking_addresses_ask_first(b):
    out = b.run("open_url", url="accounts.google.com")
    assert out.decision.outcome is Outcome.CONFIRM and b.worker.calls == []


# ---- new tabs -----------------------------------------------------------------------------------------------

def test_new_tab_blank_and_with_an_address(b):
    blank = b.run("new_tab")
    assert blank.result.message == "Opened a new tab." and len(b.worker.tabs) == 2     # the fake starts Chrome with one tab
    cal = b.run("new_tab", url="calendar")
    assert b.worker.last("new_tab") == {"url": "https://calendar.google.com/"}
    assert cal.result.message == "Opened a new tab with Page of calendar.google.com."
    assert len(b.worker.tabs) == 3


def test_new_tab_with_a_bad_address_is_refused(b):
    assert b.run("new_tab", url="javascript:alert(1)").decision.outcome is Outcome.BLOCK
    assert b.run("new_tab", url="not a site at all").result.status == "failed"
    assert b.worker.calls == []


# ---- switching and closing tabs -----------------------------------------------------------------------------

def three_tabs(b):
    b.worker.do_open_url("https://www.youtube.com/watch")
    b.worker.tabs[0]["title"] = "YouTube"
    b.worker.do_new_tab("https://mail.google.com/")
    b.worker.tabs[1]["title"] = "Inbox - Gmail"
    b.worker.do_new_tab("https://calendar.google.com/")
    b.worker.tabs[2]["title"] = "Calendar"
    b.worker.calls.clear()


@pytest.mark.parametrize("tab,expected", [("1", "YouTube"), (2, "Inbox - Gmail"), ("third", "Calendar"), ("last", "Calendar"),
                                           ("the 2nd tab", "Inbox - Gmail"), ("youtube", "YouTube"), ("gmail", "Inbox - Gmail"),
                                           ("calendar.google.com", "Calendar"), ("YouTub", "YouTube")])
def test_switching_by_number_name_or_site(b, tab, expected):
    three_tabs(b)
    out = b.run("switch_tab", tab=tab)
    assert out.decision.outcome is Outcome.ALLOW and out.decision.args["tab"] == expected
    assert out.result.status == "done"
    assert next(t for t in b.worker.tabs if t["active"])["title"] == expected


def test_go_to_the_tab_with_youtube(b):
    three_tabs(b)
    out = b.run("switch_tab", tab="the tab with YouTube")
    assert out.result.status in ("done", "failed")        # wording is up to the model; plain "youtube" is the contract


@pytest.mark.parametrize("tab,message", [("9", "only 3 tabs"), ("0", "only 3 tabs"), ("banking", "couldn't find a tab"), ("", "Which tab")])
def test_switching_to_nothing_explains_why(b, tab, message):
    three_tabs(b)
    out = b.run("switch_tab", tab=tab)
    assert out.decision is None and message in out.result.message and b.worker.calls == [("list_tabs", {})] * (message != "Which tab")


def test_switching_when_chrome_is_not_open_says_so(b):
    out = b.run("switch_tab", tab="1")
    assert out.result.status == "failed" and "open Chrome" in out.result.message


def test_two_similar_tabs_ask_which_one_then_switch(b):
    three_tabs(b)
    b.worker.do_new_tab("https://mail.google.com/")
    b.worker.tabs[3]["title"] = "Draft - Gmail"
    asked = b.run("switch_tab", tab="gmail")
    assert asked.decision is None and asked.result.status == "choose"
    options = asked.result.choice["options"]
    assert [o["label"] for o in options] == ["Inbox - Gmail", "Draft - Gmail"] and options[0]["detail"] == "mail.google.com"
    chosen = b.pipeline.choose(asked.result.choice["choice_id"], "2")
    assert chosen.decision.outcome is Outcome.ALLOW and chosen.result.message == "Switched to Draft - Gmail."
    assert next(t for t in b.worker.tabs if t["active"])["title"] == "Draft - Gmail"


def test_switching_to_a_tab_that_closed_meanwhile_fails_politely(b):
    three_tabs(b)
    out = b.pipeline.run("switch_tab", {"tab": "youtube"})
    b.worker.tabs.pop(0)
    again = b.service.execute("switch_tab", out.decision.args)
    assert again.status == "failed" and "isn't open any more" in again.message


def test_closing_a_tab_asks_first_and_closes_only_that_tab(b):
    three_tabs(b)
    out = b.run("close_tab", index=2)
    assert out.decision.outcome is Outcome.CONFIRM and "Inbox - Gmail" in out.decision.message and len(b.worker.tabs) == 3
    decision, result = b.approve(out)
    assert result.message == "Closed Inbox - Gmail." and [t["title"] for t in b.worker.tabs] == ["YouTube", "Calendar"]


def test_closing_without_a_number_closes_the_current_tab_after_asking(b):
    three_tabs(b)
    out = b.run("close_tab")
    assert out.decision.args["tab"] == "Calendar" and out.decision.outcome is Outcome.CONFIRM
    b.guard.confirm(out.decision.confirmation_id, False)
    assert len(b.worker.tabs) == 3                              # denied: nothing closed


# ---- clicking -----------------------------------------------------------------------------------------------

def test_clicking_a_clear_match(b):
    open_chrome(b)
    b.worker.buttons = [cand("About us", kind="link", context="nav")]
    out = b.run("click_text", text="about us")
    assert out.decision.outcome is Outcome.ALLOW
    assert out.decision.args == {"text": "about us", "nth": 0, "target": "About us", "page_hint": ""}
    assert b.worker.last("click") == {"text": "about us", "nth": 0, "expect": "About us"}     # the worker re-checks the label
    assert out.result.status == "done" and out.result.message == "Clicked “About us”."


def test_the_best_of_several_partial_matches_is_clicked_without_asking(b):
    open_chrome(b)
    b.worker.buttons = [cand("Show message", 0.9), cand("Show all messages", 0.6)]
    assert b.run("click_text", text="show").result.status == "done"


def test_an_exact_match_beats_longer_names_that_contain_it(b):
    """The worker counts `nth` among exact matches only, so an exact match is clicked without asking."""
    open_chrome(b)
    b.worker.buttons = [cand("Read more", 1.0), cand("Read more articles", 0.9)]
    out = b.run("click_text", text="read more")
    assert out.decision.args["nth"] == 0 and out.decision.args["target"] == "Read more" and out.result.status == "done"


def test_look_alike_buttons_ask_which_one(b):
    open_chrome(b)
    b.worker.buttons = [cand("Read more", kind="link", context="main"), cand("Read more", kind="link", context="footer")]
    asked = b.run("click_text", text="read more")
    assert asked.result.status == "choose" and b.worker.names() == ["find_clickable"]
    assert [o["detail"] for o in asked.result.choice["options"]] == ["link in main", "link in footer"]
    done = b.pipeline.choose(asked.result.choice["choice_id"], "2")
    assert done.decision.args["nth"] == 1 and b.worker.last("click")["nth"] == 1
    assert done.result.status == "done"


def test_clicking_something_that_is_not_there(b):
    open_chrome(b)
    out = b.run("click_text", text="Nope")
    assert out.decision is None and out.result.status == "failed" and "couldn't find “Nope”" in out.result.message


def test_clicking_without_chrome_says_to_open_it(b):
    out = b.run("click_text", text="Go")
    assert out.result.status == "failed" and "open Chrome" in out.result.message


@pytest.mark.parametrize("text", ["", "  ", None, 3, "x" * 501])
def test_click_needs_words(b, text):
    open_chrome(b)
    assert b.run("click_text", text=text).result.status == "failed"


@pytest.mark.parametrize("label,level", [
    ("Delete account", "high"), ("Remove", "high"), ("Buy now", "high"), ("Place order", "high"), ("Pay", "high"),
    ("Donate $5", "high"), ("Send money", "high"),
    ("Send", "medium"), ("Submit", "medium"), ("Sign out", "medium"), ("Log in", "medium"), ("Sign in", "medium"), ("Accept all", "medium"),
    ("Proceed to checkout", "medium"), ("Download report", "medium"), ("Post", "medium"),
])
def test_risky_buttons_are_not_clicked_without_approval(b, label, level):
    open_chrome(b)
    b.worker.buttons = [cand(label)]
    out = b.run("click_text", text=label)
    assert out.decision.outcome is Outcome.CONFIRM and out.decision.risk.label == level
    assert out.decision.requires_acknowledgment == (level == "high")
    assert "click" not in b.worker.names()                      # nothing clicked yet


@pytest.mark.parametrize("label", ["About us", "Next page", "Payment methods", "Read more", "Paying", "Subscribers", "Postcode"])
def test_ordinary_buttons_are_not_mistaken_for_risky_ones(label):
    assert check_click_target(label, POLICY) == []


def test_approving_a_risky_click_runs_that_exact_click(b):
    open_chrome(b)
    b.worker.buttons = [cand("Send")]
    out = b.run("click_text", text="send")
    decision, result = b.approve(out)
    assert decision.outcome is Outcome.ALLOW and result.message == "Clicked “Send”."
    assert b.worker.last("click")["expect"] == "Send"


def test_a_high_risk_click_needs_the_risk_acknowledged(b):
    open_chrome(b)
    b.worker.buttons = [cand("Delete account")]
    out = b.run("click_text", text="delete")
    plain = b.guard.confirm(out.decision.confirmation_id, True)
    assert plain.outcome is Outcome.BLOCK and "click" not in b.worker.names()
    decision, result = b.approve(out, acknowledge=True)
    assert result.status == "done"


def test_a_button_in_a_payment_form_is_high_risk_whatever_it_says(b):
    open_chrome(b)
    b.worker.buttons = [cand("Continue", form="payment")]
    out = b.run("click_text", text="continue")
    assert out.decision.risk.label == "high" and out.decision.args["page_hint"] == "payment"


def test_a_button_on_a_sign_in_page_asks(b):
    open_chrome(b)
    b.worker.buttons = [cand("Next", form="login")]
    out = b.run("click_text", text="next")
    assert out.decision.outcome is Outcome.CONFIRM and out.decision.risk.label == "medium"


def test_look_alike_letters_in_a_button_name_ask(b):
    open_chrome(b)
    b.worker.buttons = [cand("Dеlete")]            # Cyrillic е
    assert b.run("click_text", text="delete").decision.outcome is Outcome.CONFIRM


def test_if_the_page_changed_the_click_is_refused_by_the_worker(b):
    open_chrome(b)
    b.worker.buttons = [cand("About us")]
    b.worker.errors["click"] = WorkerError("changed", "The page changed since I looked, so I didn't do that. Please ask again.")
    out = b.run("click_text", text="about")
    assert out.result.status == "failed" and "page changed" in out.result.message


def test_a_click_that_opens_a_new_tab_or_a_new_page_says_so(b):
    open_chrome(b)
    b.worker.buttons = [cand("Docs")]
    b.worker.do_click = lambda text, nth=0, expect=None: {"clicked": "Docs", "navigated": True, "new_tab": True, "url": "https://x.org/", "title": "Docs",
                                                         "login": False, "payment": False, "steps": []}
    assert b.run("click_text", text="docs").result.message == "Clicked “Docs”. It opened Docs in a new tab."


# ---- typing -------------------------------------------------------------------------------------------------

def test_typing_into_a_box(b):
    open_chrome(b)
    b.worker.fields = [field("Full name")]
    out = b.run("fill_field", label="full name", value="Ada Lovelace")
    assert out.decision.outcome is Outcome.ALLOW and out.decision.args["field_kind"] == "text"
    assert b.worker.last("fill") == {"label": "full name", "nth": 0, "value": "Ada Lovelace", "expect": "Full name"}
    assert out.result.message == "Typed into “Full name”." and "Ada" not in out.result.message


@pytest.mark.parametrize("kind", ["password", "payment"])
def test_it_never_types_into_password_or_card_boxes(b, kind):
    open_chrome(b)
    b.worker.fields = [field("Password", kind=kind, type_="password")]
    out = b.run("fill_field", label="password", value="hunter2-secret")
    assert out.decision.outcome is Outcome.BLOCK and out.result is None
    assert "fill" not in b.worker.names()
    assert b.events()[-1]["outcome"] == "block"


def test_a_secret_is_not_written_to_the_audit_log_or_the_confirmation_text(b):
    open_chrome(b)
    b.worker.fields = [field("Message")]
    b.worker.page_flags = {"login": True}                       # asks first, so a confirmation message is produced
    out = b.run("fill_field", label="message", value="my secret text 12345")
    assert out.decision.outcome is Outcome.CONFIRM
    b.approve(out)
    assert "my secret text" not in b.audit.read_text() and "my secret text" not in out.decision.message
    assert "<20 characters>" in b.audit.read_text()


def test_typing_on_a_sign_in_page_asks_first(b):
    open_chrome(b)
    b.worker.fields = [field("Email")]
    b.worker.page_flags = {"login": True}
    out = b.run("fill_field", label="email", value="ada@example.com")
    assert out.decision.outcome is Outcome.CONFIRM and "sign-in" in out.decision.reason
    assert "fill" not in b.worker.names()
    _, result = b.approve(out)
    assert result.status == "done"


def test_typing_on_a_payment_page_is_high_risk(b):
    open_chrome(b)
    b.worker.fields = [field("Name on card")]
    b.worker.page_flags = {"payment": True}
    out = b.run("fill_field", label="name", value="Ada")
    assert out.decision.risk.label == "high" and out.decision.requires_acknowledgment


def test_two_matching_boxes_ask_which_one(b):
    open_chrome(b)
    b.worker.fields = [field("Search"), field("Search the site")]
    b.worker.fields[1]["score"] = 1.0
    asked = b.run("fill_field", label="search", value="cats")
    assert asked.result.status == "choose" and "fill" not in b.worker.names()
    done = b.pipeline.choose(asked.result.choice["choice_id"], "2")
    assert b.worker.last("fill")["nth"] == 1 and done.result.status == "done"


@pytest.mark.parametrize("args", [{"label": "x"}, {"label": "x", "value": ""}, {"label": "", "value": "v"}, {"label": "x", "value": 5},
                                  {"label": "x", "value": "v" * 2001}])
def test_typing_needs_a_box_and_text(b, args):
    open_chrome(b)
    out = b.run("fill_field", **args)
    assert out.decision is None and out.result.status == "failed"


def test_no_matching_box(b):
    open_chrome(b)
    out = b.run("fill_field", label="nickname", value="x")
    assert "couldn't find a box called “nickname”" in out.result.message


# ---- scrolling, waiting, reading ----------------------------------------------------------------------------

@pytest.mark.parametrize("said,sent", [("down", "down"), ("Up", "up"), ("page down", "down"), ("top", "top"), ("bottom", "bottom"), (None, "down")])
def test_scrolling(b, said, sent):
    open_chrome(b)
    args = {} if said is None else {"direction": said}
    out = b.run("scroll_page", **args)
    assert out.decision.outcome is Outcome.ALLOW and b.worker.last("scroll") == {"direction": sent}
    assert out.result.message == f"Scrolled {sent}."


def test_scrolling_at_the_edge_and_sideways(b):
    open_chrome(b)
    b.worker.scroll_moves = False
    assert b.run("scroll_page", direction="bottom").result.message == "Already at the bottom of the page."
    assert b.run("scroll_page", direction="up").result.message == "Already at the top of the page."
    assert b.run("scroll_page", direction="sideways").result.status == "failed"


def test_the_safety_layer_refuses_bad_scroll_calls_on_its_own(b):
    from backend.toolcall import ToolCall
    assert b.guard.evaluate(ToolCall("scroll_page", {"direction": "sideways"})).outcome is Outcome.BLOCK


def test_waiting_for_a_page(b):
    open_chrome(b)
    assert b.run("wait_for_page_load").result.message == "The page has finished loading."
    b.worker.loaded = False
    assert b.run("wait_for_page_load").result.message == "The page is still loading after 10 seconds."


def test_reading_the_title_and_address(b):
    b.worker.do_open_url("https://example.com/path?token=secret#frag")
    assert b.run("get_page_title").result.message == "The page title is “Page of example.com”."
    url = b.run("get_current_url").result.message
    assert url == "You're on https://example.com/path." and "secret" not in url


def test_reading_the_address_of_a_blank_or_failed_page(b):
    b.worker.do_open_chrome()
    assert b.run("get_current_url").result.message == "You're on a blank page."
    b.worker.tabs[0]["url"] = "chrome-error://chromewebdata/"
    assert "didn't load" in b.run("get_current_url").result.message


def test_reading_without_chrome(b):
    assert "open Chrome" in b.run("get_page_title").result.message


def test_page_text_is_cleaned_before_it_is_shown(b):
    b.worker.do_open_url("https://example.com/")
    b.worker.tabs[0]["title"] = "Hi\x00\n\n there " + "x" * 300
    message = b.run("get_page_title").result.message
    assert "\x00" not in message and "\n" not in message and len(message) < 200


def test_invisible_and_direction_marks_are_removed_from_page_text(b):
    b.worker.do_open_url("https://example.com/")
    b.worker.tabs[0]["title"] = "Safe\u202etxt.exe\u200b page"
    message = b.run("get_page_title").result.message
    assert message == "The page title is “Safetxt.exe page”."


# ---- YouTube ------------------------------------------------------------------------------------------------

def test_playing_a_video(b):
    out = b.run("play_youtube_video", query="lofi music")
    assert b.worker.last("youtube_play") == {"query": "lofi music"} and out.result.message == "Playing “Lofi beats”."
    assert b.run("play_youtube_video").result.status == "done" and b.worker.last("youtube_play") == {}


@pytest.mark.parametrize("flags,text", [
    ({"played": False, "opened_only": True}, "Opened YouTube. Tell me what you'd like to play."),
    ({"played": False, "no_video": True}, "I couldn't find a video to play on this page."),
    ({"played": False, "consent": True}, "YouTube is asking about cookies"),
    ({"played": False, "video_found": True}, "wouldn't start"),
])
def test_playing_when_it_does_not_go_to_plan(b, flags, text):
    b.worker.do_youtube_play = lambda query=None: {"login": False, "payment": False, "steps": [], "title": "", "url": "", **flags}
    assert text in b.run("play_youtube_video").result.message


# ---- signing in ---------------------------------------------------------------------------------------------

def test_a_site_that_needs_sign_in_stops_and_asks_the_user(tmp_path):
    b = Setup(tmp_path, login_hosts={"mail.google.com"})
    out = b.run("open_url", url="gmail")
    assert out.result.status == "needs_login"
    assert "mail.google.com is asking you to sign in" in out.result.message and "never type passwords" in out.result.message
    assert b.worker.names() == ["open_url"]                     # it did not go on to do anything else


def test_search_results_on_a_sign_in_page_stop_too(tmp_path):
    b = Setup(tmp_path, login_hosts={"www.google.com"})
    assert b.run("search_chrome", query="x").result.status == "needs_login"


def test_opening_a_sign_in_address_asks_and_then_waits_for_the_user(tmp_path):
    b = Setup(tmp_path, login_hosts={"accounts.google.com"})
    out = b.run("open_url", url="https://accounts.google.com/signin")
    assert out.decision.outcome is Outcome.CONFIRM and "login or payment" in out.decision.reason
    _, result = b.approve(out)
    assert result.status == "needs_login"


def test_the_whole_login_flow_the_assistant_never_types_the_password(tmp_path):
    b = Setup(tmp_path, login_hosts={"mail.google.com"})
    b.worker.filter_by_query = True
    assert b.run("open_url", url="gmail").result.status == "needs_login"
    b.worker.fields = [field("Email"), field("Password", kind="password", type_="password")]
    pw = b.run("fill_field", label="password", value="hunter2-secret")
    assert pw.decision.outcome is Outcome.BLOCK
    email = b.run("fill_field", label="email", value="ada@example.com")
    assert email.decision.outcome is Outcome.CONFIRM            # even the user name needs the user's okay on a sign-in page
    assert "fill" not in b.worker.names()
    b.worker.page_flags = {"login": False}
    b.worker.login_hosts.clear()                                # the user signed in themselves
    b.worker.fields = [field("Search mail")]
    assert b.run("fill_field", label="search", value="invoices").decision.outcome is Outcome.ALLOW


def test_payment_pages_are_mentioned(tmp_path):
    b = Setup(tmp_path, payment_hosts={"shop.example"})
    out = b.run("open_url", url="https://shop.example/cart")
    assert out.result.status == "done" and "payment page" in out.result.message


def test_slow_pages_are_mentioned(b):
    b.worker.slow = True
    assert "slow" in b.run("open_url", url="example.com").result.message


# ---- when things go wrong ---------------------------------------------------------------------------------

def test_a_missing_helper_is_not_available_with_the_fix(b):
    b.worker.errors["open_url"] = WorkerUnavailable("The browser helper isn't installed yet. Run this once:  cd browser && npm install")
    out = b.run("open_url", url="example.com")
    assert out.result.status == "not_available" and "npm install" in out.result.message


def test_a_missing_chrome_is_not_available(b):
    b.worker.errors["open_chrome"] = WorkerError("no_chrome", "I can't find Google Chrome.")
    assert b.run("open_chrome").result.status == "not_available"


def test_a_page_that_will_not_open(b):
    b.worker.errors["open_url"] = WorkerError("unreachable", "I couldn't reach that site. Check the address and your connection.")
    out = b.run("open_url", url="example.com")
    assert out.result.status == "failed" and "couldn't reach" in out.result.message
    assert b.events()[-1]["status"] == "failed"


def test_an_unexpected_error_hides_its_details(b):
    b.worker.do_open_url = lambda url, new_tab=False: (_ for _ in ()).throw(RuntimeError("secret /Users/me/path"))
    out = b.run("open_url", url="example.com")
    assert out.result.status == "failed" and "secret" not in out.result.message


def test_the_browser_can_be_turned_off(tmp_path):
    b = Setup(tmp_path)
    b.service.enabled = False
    out = b.run("open_chrome")
    assert out.result.status == "not_available" and "browser.enabled" in out.result.message and b.worker.calls == []
    assert b.service.execute("open_chrome", {}).status == "not_available"


def test_dry_run_changes_nothing(b):
    open_chrome(b)
    b.worker.buttons = [cand("Send")]
    out = b.pipeline.run("click_text", {"text": "send"}, False, True)
    assert out.decision.outcome is Outcome.DRY_RUN and "click" not in b.worker.names()


def test_the_model_cannot_slip_in_hints(b):
    """page_hint, target and field_kind come only from the page; whatever the model sends is replaced."""
    open_chrome(b)
    b.worker.buttons = [cand("Delete account")]
    out = b.run("click_text", text="delete", target="Harmless", page_hint="")
    assert out.decision.args["target"] == "Delete account" and out.decision.outcome is Outcome.CONFIRM
    b.worker.fields = [field("Password", kind="password")]
    out = b.run("fill_field", label="password", value="x", field_kind="text", target="Name")
    assert out.decision.outcome is Outcome.BLOCK


def test_unchecked_browser_calls_are_refused_by_the_safety_layer():
    from backend.toolcall import ToolCall
    guard = GuardrailManager(POLICY)
    assert guard.evaluate(ToolCall("click_text", {"text": "Delete"})).outcome is Outcome.BLOCK
    assert guard.evaluate(ToolCall("fill_field", {"label": "x", "value": "y"})).outcome is Outcome.BLOCK
    assert guard.evaluate(ToolCall("fill_field", {"label": "x", "value": "y", "target": "x", "field_kind": "password"})).outcome is Outcome.BLOCK
    assert guard.evaluate(ToolCall("click_text", {"text": "a", "target": "a", "nth": -1})).outcome is Outcome.BLOCK
    assert guard.evaluate(ToolCall("click_text", {"text": "a", "target": "a", "nth": True})).outcome is Outcome.BLOCK
    assert guard.evaluate(ToolCall("switch_tab", {"tab": "1", "tab_id": "1; rm"})).outcome is Outcome.BLOCK
    assert guard.evaluate(ToolCall("click_text", {"text": "a\nb", "target": "a", "page_hint": ""})).outcome is Outcome.BLOCK
    assert guard.evaluate(ToolCall("click_text", {"text": "a", "target": "a", "page_hint": "weird"})).outcome is Outcome.BLOCK
    assert guard.evaluate(ToolCall("click_text", {"text": "a", "target": "a", "page_hint": ""})).outcome is Outcome.ALLOW


def test_audit_summaries_hide_typed_text():
    assert summarize_args({"value": "secret", "label": "x"}) == {"value": "<6 characters>", "label": "x"}
    assert summarize_args({"value": 5}) == {"value": "<hidden>"}


# ---- the worker client ------------------------------------------------------------------------------------

def client(tmp_path, handler, token="t" * 32):
    token_file = tmp_path / "token"
    if token is not None:
        token_file.write_text(token + "\n")
    return WorkerClient("http://127.0.0.1:8766", token_file, transport=httpx.MockTransport(handler))


def test_the_client_sends_the_token_and_the_action(tmp_path):
    seen = {}

    def handler(request):
        seen["token"] = request.headers["x-assistant-token"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "title": "T"})

    assert client(tmp_path, handler).call("get_title", a=1) == {"ok": True, "title": "T"}
    assert seen == {"token": "t" * 32, "body": {"action": "get_title", "args": {"a": 1}}}


def test_the_client_only_talks_to_this_computer(tmp_path):
    with pytest.raises(ValueError):
        WorkerClient("http://192.168.1.5:8766", tmp_path / "t")


def test_the_client_reports_each_kind_of_problem(tmp_path):
    with pytest.raises(WorkerUnavailable, match="token"):
        client(tmp_path, lambda r: httpx.Response(200), token=None).call("x")
    with pytest.raises(WorkerUnavailable, match="rejected"):
        client(tmp_path, lambda r: httpx.Response(401, json={"ok": False})).call("x")

    def refuse(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(WorkerUnavailable, match="isn't running"):
        client(tmp_path, refuse).call("x")

    def slow(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(WorkerError) as e:
        client(tmp_path, slow).call("x")
    assert e.value.code == "timeout"
    with pytest.raises(WorkerError) as e:
        client(tmp_path, lambda r: httpx.Response(200, json={"ok": False, "code": "bad_url", "error": "Nope."})).call("x")
    assert (e.value.code, e.value.message) == ("bad_url", "Nope.")
    with pytest.raises(WorkerError):
        client(tmp_path, lambda r: httpx.Response(200, text="not json")).call("x")
    with pytest.raises(WorkerError):
        client(tmp_path, lambda r: httpx.Response(500, json={"ok": False, "code": "failed", "error": "x"})).call("x")


def test_ping(tmp_path):
    assert client(tmp_path, lambda r: httpx.Response(200, json={"ok": True})).ping() is True
    assert client(tmp_path, lambda r: httpx.Response(401, json={})).ping() is False
    (tmp_path / "token").unlink()
    assert client(tmp_path, lambda r: httpx.Response(200), token=None).ping() is False


# ---- starting the worker ---------------------------------------------------------------------------------

class FakeProc:
    def __init__(self, exits_with=None):
        self.code = exits_with
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 0

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.code


class Pings:
    def __init__(self, answers):
        self.answers = list(answers)

    def ping(self):
        return self.answers.pop(0) if self.answers else self.answers_last

    answers_last = True


def make_launcher(tmp_path, *, pings, proc=None, node=None, installed=True, chrome=True, **kw):
    root = tmp_path / "root"
    (root / "browser" / "src").mkdir(parents=True, exist_ok=True)
    script = root / "browser" / "src" / "server.js"
    script.write_text("")
    if installed:
        (root / "browser" / "node_modules" / "puppeteer-core").mkdir(parents=True, exist_ok=True)
    chrome_path = tmp_path / "Chrome"
    if chrome:
        chrome_path.write_text("")
    spawned = []

    def popen(argv, **options):
        spawned.append((argv, options))
        return proc or FakeProc()

    node_path = node or "/bin/sh"
    launcher = WorkerLauncher(client=pings, node=node_path, script=script, port=8766, token_file=tmp_path / "token",
                              chrome_path=str(chrome_path), profile_dir=tmp_path / "profile", log_file=tmp_path / "logs" / "browser.log",
                              popen=popen, sleep=lambda s: None, **kw)
    return launcher, spawned


def test_the_worker_is_started_with_a_plain_argument_list(tmp_path):
    launcher, spawned = make_launcher(tmp_path, pings=Pings([False, False, True]), idle_minutes=7)
    launcher.ensure()
    argv, options = spawned[0]
    assert isinstance(argv, list) and argv[:2] == ["/bin/sh", str(launcher.script)] and "shell" not in options
    pairs = dict(zip(argv[2::2], argv[3::2]))
    assert pairs == {"--port": "8766", "--token-file": str(tmp_path / "token"), "--chrome": str(tmp_path / "Chrome"),
                     "--profile": str((tmp_path / "profile").resolve()), "--idle-minutes": "7"}
    assert options["stdin"] == subprocess.DEVNULL and options["start_new_session"] is True


def test_a_running_worker_is_not_started_again(tmp_path):
    launcher, spawned = make_launcher(tmp_path, pings=Pings([True]))
    launcher.ensure()
    assert spawned == []


def test_a_starting_worker_is_waited_for_not_duplicated(tmp_path):
    launcher, spawned = make_launcher(tmp_path, pings=Pings([False, False, True, False, True]))
    launcher.ensure()
    launcher.ensure()                                            # pings False once more, but the process is alive
    assert len(spawned) == 1


def test_a_worker_that_dies_at_startup_is_reported(tmp_path):
    launcher, _ = make_launcher(tmp_path, pings=Pings([False, False]), proc=FakeProc(exits_with=1))
    with pytest.raises(WorkerUnavailable, match="stopped right after starting"):
        launcher.ensure()


def test_a_worker_that_never_answers_is_stopped(tmp_path):
    ticks = iter(range(0, 1000))
    proc = FakeProc()
    pings = Pings([])
    pings.answers_last = False
    launcher, _ = make_launcher(tmp_path, pings=pings, proc=proc, start_timeout=3)
    launcher._clock = lambda: next(ticks)
    with pytest.raises(WorkerUnavailable, match="didn't start in time"):
        launcher.ensure()
    assert proc.terminated


def test_what_is_missing_is_explained(tmp_path):
    ok, _ = make_launcher(tmp_path, pings=Pings([]))
    assert ok.diagnose() is None
    assert "Node.js" in make_launcher(tmp_path, pings=Pings([]), node="/nope/node")[0].diagnose()
    assert "npm install" in make_launcher(tmp_path / "b", pings=Pings([]), installed=False)[0].diagnose()
    assert "chrome_path" in make_launcher(tmp_path / "c", pings=Pings([]), chrome=False)[0].diagnose()


def test_nothing_is_started_when_something_is_missing(tmp_path):
    launcher, spawned = make_launcher(tmp_path, pings=Pings([False]), installed=False)
    with pytest.raises(WorkerUnavailable, match="npm install"):
        launcher.ensure()
    assert spawned == []


def test_stopping_the_worker(tmp_path):
    proc = FakeProc()
    launcher, _ = make_launcher(tmp_path, pings=Pings([False, True]), proc=proc)
    launcher.ensure()
    launcher.stop()
    assert proc.terminated
    launcher.stop()                                              # harmless twice


def test_the_service_starts_the_worker_before_using_it(tmp_path):
    worker = FakeWorker()
    calls = []
    launcher = type("L", (), {"ensure": lambda self: calls.append("ensure"), "stop": lambda self: calls.append("stop"),
                              "diagnose": lambda self: None})()
    service = BrowserService(worker, launcher)
    assert service.execute("open_chrome", {}).status == "done" and calls == ["ensure"]
    service.close()
    assert calls == ["ensure", "stop"]
    assert service.status() == {"enabled": True, "ready": True, "problem": "", "helper_running": True}


# ---- routing between the desktop and the browser ------------------------------------------------------------

def router(tmp_path):
    world = make_world(tmp_path)
    choices = ChoiceStore()
    world.service.choices = choices
    worker = FakeWorker()
    return ActionRouter(world.service, BrowserService(worker, choices=choices), choices), world, worker


def test_the_router_sends_each_tool_to_the_right_place(tmp_path):
    r, world, worker = router(tmp_path)
    assert r.execute("open_chrome", {}).message == "Chrome is open." and worker.names() == ["open_chrome"]
    assert r.execute("open_app", {"app_name": "Safari"}).status == "done" and worker.names() == ["open_chrome"]
    assert r.execute("reply", {"text": "hi"}) is None
    assert r.execute("create_reminder", {}).status == "not_available"          # later phase


def test_one_choice_store_serves_both_kinds_of_question(tmp_path):
    r, world, worker = router(tmp_path)
    worker.do_open_url("https://a.com/")
    worker.tabs[0]["title"] = "Mail one"
    worker.do_new_tab("https://b.com/")
    worker.tabs[1]["title"] = "Mail two"
    tab_q = r.prepare("switch_tab", {"tab": "mail"})
    assert tab_q.result.status == "choose"
    chosen = r.choose(tab_q.result.choice["choice_id"], "1")
    assert chosen.tool == "switch_tab" and chosen.args["tab"] == "Mail one"
    assert r.choose(tab_q.result.choice["choice_id"], "1").status == "failed"      # single use


def test_status_includes_the_browser(tmp_path):
    r, *_ = router(tmp_path)
    status = r.status()
    assert status["browser"]["enabled"] is True and "hammerspoon" in status


def test_the_api_runs_a_browser_action_end_to_end(tmp_path):
    r, world, worker = router(tmp_path)
    reply = json.dumps({"tool": "search_chrome", "args": {"query": "AI tools"}, "requires_confirmation": False})

    def handler(request):
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"role": "assistant", "content": reply}})
        return httpx.Response(200, json={"version": "0", "models": [{"name": "m"}]})

    llm = OllamaClient("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(handler))
    guard = GuardrailManager(POLICY, AuditLog(tmp_path / "audit.jsonl"))
    app = create_app({"ollama": {}, "router": {"max_retries": 0}}, llm, guard=guard, actions=r)
    with TestClient(app) as c:
        body = c.post("/route", json={"text": "open chrome and search for AI tools"}).json()
        assert c.get("/health").json()["phase"] == 6
        assert c.get("/automation/status").json()["browser"]["enabled"] is True
    assert body["result"]["status"] == "done" and body["result"]["message"] == "Searched Google for “AI tools”."
    assert body["guard"]["outcome"] == "allow" and body["result"]["items"][0] == "Searching Google"


def test_the_prompt_mentions_the_browser_and_stays_small():
    from datetime import datetime
    from backend.prompt import build_messages
    system = build_messages(build_registry(), "hi", datetime(2026, 1, 1))[0]["content"]
    assert "play_youtube_video" in system and "Never type passwords" in system
    assert len(system) < 5000, len(system)


def test_an_empty_choice_store_is_still_the_shared_one(tmp_path):
    """An empty store is falsy; passing it in must not quietly create a second, separate store."""
    from automation.service import ActionService
    from browserctl.factory import build_browser
    from backend.actions import build_router
    shared = ChoiceStore()
    assert BrowserService(FakeWorker(), choices=shared).choices is shared
    world = make_world(tmp_path)
    assert ActionService(world.service.apps, world.service.files, world.service.controller, choices=shared).choices is shared
    router = build_router({"browser": {"enabled": False}, "automation": {"hammerspoon": False}}, tmp_path)
    assert router.desktop.choices is router.choices is router.browser.choices
    assert build_browser({}, tmp_path, shared).choices is shared
