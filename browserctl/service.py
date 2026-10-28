"""Browser actions, in the same two steps as the desktop ones:

    prepare()  read-only: find the real tab, button or box the words refer to (ask the user if unsure)
    -- the safety layer then judges that real target, not the words --
    execute()  only called for a call the safety layer authorized

Page text (titles, button labels) is shown to the user and judged by the safety layer. It is never
given back to the language model, so a web page cannot give the assistant instructions.
"""
import logging
import re
import unicodedata
from urllib.parse import urlparse

from automation.choices import ChoiceStore
from automation.fuzzy import Ranked, normalize, pick, rank
from automation.results import ActionResult, Prepared
from browserctl.client import WorkerClient, WorkerError, WorkerUnavailable
from browserctl.sites import to_address

log = logging.getLogger("assistant.browser")

# tool name -> the worker action that does it
ACTIONS = {
    "open_chrome": "open_chrome",
    "open_url": "open_url",
    "search_chrome": "search",
    "new_tab": "new_tab",
    "switch_tab": "switch_tab",
    "close_tab": "close_tab",
    "click_text": "click",
    "fill_field": "fill",
    "scroll_page": "scroll",
    "wait_for_page_load": "wait_load",
    "get_page_title": "get_title",
    "get_current_url": "get_url",
    "play_youtube_video": "youtube_play",
}
BROWSER_TOOLS = tuple(ACTIONS)
DIRECTIONS = {"down": "down", "up": "up", "top": "top", "bottom": "bottom",
              "page down": "down", "page up": "up", "start": "top", "end": "bottom", "to the top": "top", "to the bottom": "bottom"}
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
            "eighth": 8, "ninth": 9, "tenth": 10}
MAX_TEXT = 500
MAX_VALUE = 2000
CLEAR_GAP = 0.1          # a best match this far ahead of the next one is chosen without asking
MAX_OPTIONS = 5


class _Stop(Exception):
    """Preparing cannot go on; `result` is what to tell the user."""

    def __init__(self, result: ActionResult) -> None:
        self.result = result


def clean(text, limit: int = 80) -> str:
    """Page text made safe to show: one line, no control characters, not too long."""
    visible = "".join(c for c in str(text or "") if unicodedata.category(c) != "Cf")      # no invisible or direction-flipping marks
    out = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", visible).split())
    return out if len(out) <= limit else out[: limit - 1] + "…"


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def short_url(url: str) -> str:
    """Address without the query or fragment, which can hold tokens."""
    try:
        p = urlparse(url)
    except ValueError:
        return ""
    return clean(f"{p.scheme}://{p.netloc}{p.path}", 200)


def _error_result(error: Exception) -> ActionResult:
    if isinstance(error, WorkerUnavailable):
        return ActionResult("not_available", str(error))
    code, message = error.code, error.message
    if code == "no_chrome":
        return ActionResult("not_available", message)
    if code == "no_browser":
        return ActionResult("failed", "Chrome isn't open yet. Ask me to open Chrome first.")
    return ActionResult("failed", message)


class BrowserService:
    def __init__(self, client: WorkerClient, launcher=None, *, choices: ChoiceStore | None = None,
                 enabled: bool = True) -> None:
        self.client = client
        self.launcher = launcher
        self.choices = choices if choices is not None else ChoiceStore()      # not `or`: an empty store is falsy
        self.enabled = enabled

    # -- talking to the worker --------------------------------------------------------------------------------

    def _call(self, action: str, **args) -> dict:
        if self.launcher is not None:
            self.launcher.ensure()
        return self.client.call(action, **args)

    def _query(self, action: str, **args) -> dict:
        try:
            return self._call(action, **args)
        except (WorkerUnavailable, WorkerError) as e:
            raise _Stop(_error_result(e)) from None

    def choose(self, choice_id: str, option_id: str | None):
        return self.choices.resolve(choice_id, option_id)

    def close(self) -> None:
        if self.launcher is not None:
            self.launcher.stop()

    def status(self) -> dict:
        problem = self.launcher.diagnose() if self.launcher is not None else None
        return {"enabled": self.enabled, "ready": problem is None, "problem": problem or "",
                "helper_running": self.client.ping()}

    # -- step 1: find what the words refer to (read-only) ------------------------------------------------------

    def prepare(self, tool: str, args: dict, requires_confirmation: bool = False,
                dry_run: bool | None = None) -> Prepared:
        if not self.enabled:
            return Prepared("failed", result=ActionResult(
                "not_available", "Browser control is turned off. Set browser.enabled: true in config.yaml."))
        try:
            return getattr(self, f"_prepare_{tool}", self._prepare_plain)(tool, args, requires_confirmation, dry_run)
        except _Stop as stop:
            return Prepared("failed", result=stop.result)

    @staticmethod
    def _failed(message: str) -> Prepared:
        return Prepared("failed", result=ActionResult("failed", message))

    def _prepare_plain(self, tool, args, confirm, dry_run) -> Prepared:
        return Prepared("ready", {})       # tools that take no arguments: whatever the model added is dropped

    def _address(self, value) -> str | None:
        return to_address(value) if isinstance(value, str) else None

    def _prepare_open_url(self, tool, args, confirm, dry_run) -> Prepared:
        text = args.get("url")
        address = self._address(text)
        if address is None:
            shown = clean(text, 60) if isinstance(text, str) else ""
            return self._failed(f"I don't know a website called “{shown}”. Tell me its address, like example.com.")
        return Prepared("ready", {"url": address})

    def _prepare_new_tab(self, tool, args, confirm, dry_run) -> Prepared:
        text = args.get("url")
        if text is None or text == "":
            return Prepared("ready", {})
        address = self._address(text)
        if address is None:
            shown = clean(text, 60) if isinstance(text, str) else ""
            return self._failed(f"I don't know a website called “{shown}”. Tell me its address, like example.com.")
        return Prepared("ready", {"url": address})

    def _prepare_search_chrome(self, tool, args, confirm, dry_run) -> Prepared:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return self._failed("What should I search for?")
        return Prepared("ready", {"query": query.strip()})

    def _prepare_play_youtube_video(self, tool, args, confirm, dry_run) -> Prepared:
        query = args.get("query")
        if isinstance(query, str) and query.strip():
            return Prepared("ready", {"query": query.strip()})
        return Prepared("ready", {})

    def _prepare_scroll_page(self, tool, args, confirm, dry_run) -> Prepared:
        raw = args.get("direction", "down")
        direction = DIRECTIONS.get(normalize(raw)) if isinstance(raw, str) else None
        if direction is None:
            return self._failed("I can scroll up, down, to the top or to the bottom.")
        return Prepared("ready", {"direction": direction})

    # tabs

    def _tabs(self) -> list[dict]:
        tabs = self._query("list_tabs").get("tabs") or []
        if not tabs:
            raise _Stop(ActionResult("failed", "There are no tabs open. Ask me to open Chrome first."))
        return tabs

    @staticmethod
    def _tab_number(text: str, count: int) -> int | None:
        """"2", "second", "the 3rd tab", "last" -> a tab number; None if it isn't one."""
        words = [w for w in normalize(text).split() if w not in ("tab", "the", "number", "no")]
        if len(words) != 1:
            return None
        word = words[0]
        if word == "last":
            return count
        m = re.fullmatch(r"(\d{1,3})(?:st|nd|rd|th)?", word)
        if m:
            return int(m.group(1))
        return ORDINALS.get(word)

    def _match_tabs(self, tabs: list[dict], text: str) -> list[dict] | int:
        """An int is a tab number; a list is the tabs whose title or site matches (best first)."""
        number = self._tab_number(text, len(tabs))
        if number is not None:
            return number
        entries = [(t, normalize(name)) for t in tabs for name in (t.get("title", ""), host_of(t.get("url", "")))]
        seen, ranked = set(), []
        for r in rank(normalize(text), entries, name_of=lambda e: e[1]):
            tab = r.item[0]
            if tab["id"] not in seen:
                seen.add(tab["id"])
                ranked.append(Ranked(r.score, tab))
        found = pick(ranked, MAX_OPTIONS)
        return sorted(found.items, key=lambda t: t.get("index", 0))      # several matches: in the order the tabs are shown

    @staticmethod
    def _tab_label(tab: dict) -> str:
        return clean(tab.get("title")) or host_of(tab.get("url", "")) or "a blank tab"

    def _tab_args(self, tab: dict, with_index: bool) -> dict:
        out = {"tab": self._tab_label(tab), "tab_id": tab["id"]}
        if with_index:
            out["index"] = tab["index"]
        return out

    def _pick_tab(self, tool, text, confirm, dry_run, with_index) -> Prepared:
        tabs = self._tabs()
        found = self._match_tabs(tabs, text)
        if isinstance(found, int):
            if not 1 <= found <= len(tabs):
                noun = "tab" if len(tabs) == 1 else "tabs"
                return self._failed(f"There {'is' if len(tabs) == 1 else 'are'} only {len(tabs)} {noun} open.")
            return Prepared("ready", self._tab_args(tabs[found - 1], with_index))
        if not found:
            return self._failed(f"I couldn't find a tab matching “{clean(text, 60)}”.")
        if len(found) == 1:
            return Prepared("ready", self._tab_args(found[0], with_index))
        targets = {str(i): self._tab_args(t, with_index) for i, t in enumerate(found, 1)}
        shown = [{"id": str(i), "label": self._tab_label(t), "detail": host_of(t.get("url", ""))}
                 for i, t in enumerate(found, 1)]
        return self.choices.ask(tool, targets, shown, confirm, dry_run, f"Which tab did you mean by “{clean(text, 60)}”?")

    def _prepare_switch_tab(self, tool, args, confirm, dry_run) -> Prepared:
        text = args.get("tab")
        if not isinstance(text, (str, int)) or isinstance(text, bool) or not str(text).strip():
            return self._failed("Which tab? Tell me its number or part of its title.")
        return self._pick_tab(tool, str(text), confirm, dry_run, with_index=False)

    def _prepare_close_tab(self, tool, args, confirm, dry_run) -> Prepared:
        text = args.get("index")
        if text is None or (isinstance(text, str) and not text.strip()):
            tabs = self._tabs()
            current = next((t for t in tabs if t.get("active")), tabs[-1])
            return Prepared("ready", self._tab_args(current, with_index=True))
        if isinstance(text, bool) or not isinstance(text, (str, int)):
            return self._failed("Which tab should I close?")
        return self._pick_tab(tool, str(text), confirm, dry_run, with_index=True)

    # buttons, links and boxes

    @staticmethod
    def _pool(found: dict) -> list[dict]:
        """The candidates the worker will count `nth` over: exact matches if there are any, else all."""
        candidates = found.get("candidates") or []
        exact = [c for c in candidates if c.get("score", 0) >= 1]
        return exact or candidates

    @staticmethod
    def _close_matches(pool: list[dict]) -> list[dict]:
        """One item if it is clearly the best, otherwise the few that are about equally good."""
        top = pool[0]["score"]
        if len(pool) == 1 or (top < 1 and top - pool[1]["score"] >= CLEAR_GAP):
            return pool[:1]
        return [c for c in pool if c["score"] >= top - CLEAR_GAP][:MAX_OPTIONS]

    @staticmethod
    def _hint(candidate: dict, page: dict) -> str:
        form = candidate.get("formKind")
        if form in ("login", "payment"):
            return form
        if page.get("payment"):
            return "payment"
        return "login" if page.get("login") else ""

    @staticmethod
    def _need_chrome(found: dict) -> None:
        if found.get("running") is False:
            raise _Stop(ActionResult("failed", "Chrome isn't open yet. Ask me to open Chrome first."))

    def _text_arg(self, args: dict, name: str, what: str) -> str:
        value = args.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
            raise _Stop(ActionResult("failed", f"I need {what}."))
        return value.strip()

    def _prepare_click_text(self, tool, args, confirm, dry_run) -> Prepared:
        query = self._text_arg(args, "text", "the words on the button or link to click")
        found = self._query("find_clickable", text=query)
        self._need_chrome(found)
        pool = self._pool(found)
        if not pool:
            return self._failed(f"I couldn't find “{clean(query, 60)}” on this page.")
        page = found.get("page") or {}
        matches = self._close_matches(pool)

        def call(i: int, c: dict) -> dict:
            return {"text": query, "nth": i, "target": clean(c.get("label"), 120), "page_hint": self._hint(c, page)}

        if len(matches) == 1:
            return Prepared("ready", call(0, matches[0]))
        targets = {str(i + 1): call(i, c) for i, c in enumerate(matches)}
        shown = [{"id": str(i + 1), "label": clean(c.get("label")), "detail": self._where(c)} for i, c in enumerate(matches)]
        return self.choices.ask(tool, targets, shown, confirm, dry_run,
                                f"I found {len(matches)} things that could match “{clean(query, 60)}”. Which one?")

    @staticmethod
    def _where(candidate: dict) -> str:
        kind = candidate.get("kind") or "item"
        context = candidate.get("context")
        return f"{kind} in {context}" if context else kind

    def _prepare_fill_field(self, tool, args, confirm, dry_run) -> Prepared:
        label = self._text_arg(args, "label", "the name of the box to type into")
        value = args.get("value")
        if not isinstance(value, str) or not value or len(value) > MAX_VALUE:
            return self._failed("I need the text to type.")
        found = self._query("find_field", label=label)
        self._need_chrome(found)
        pool = self._pool(found)
        if not pool:
            return self._failed(f"I couldn't find a box called “{clean(label, 60)}” on this page.")
        page = found.get("page") or {}
        matches = self._close_matches(pool)

        def call(i: int, c: dict) -> dict:
            return {"label": label, "nth": i, "target": clean(c.get("label"), 120), "value": value,
                    "field_kind": c.get("kind") or "text", "page_hint": self._hint(c, page)}

        if len(matches) == 1:
            return Prepared("ready", call(0, matches[0]))
        targets = {str(i + 1): call(i, c) for i, c in enumerate(matches)}
        shown = [{"id": str(i + 1), "label": clean(c.get("label")), "detail": c.get("type") or "box"} for i, c in enumerate(matches)]
        return self.choices.ask(tool, targets, shown, confirm, dry_run,
                                f"I found {len(matches)} boxes that could match “{clean(label, 60)}”. Which one?")

    # -- step 2: do it (only for calls the safety layer authorized) ---------------------------------------------

    def execute(self, tool: str, args: dict) -> ActionResult | None:
        if tool == "reply":
            return None
        if tool not in ACTIONS:
            return ActionResult("not_available", "That isn't something I can do yet, so nothing was done.")
        if not self.enabled:
            return ActionResult("not_available", "Browser control is turned off. Set browser.enabled: true in config.yaml.")
        try:
            sent = getattr(self, f"_send_{tool}", self._send_none)(args)
            data = self._call(ACTIONS[tool], **sent)
            return getattr(self, f"_done_{tool}")(args, data)
        except (WorkerUnavailable, WorkerError) as e:
            return _error_result(e)
        except Exception:
            log.exception("browser action %s failed unexpectedly", tool)
            return ActionResult("failed", "Something went wrong in the browser. Nothing else was changed.")

    # what is sent to the worker: only the known fields, never the whole argument dict

    @staticmethod
    def _send_none(args: dict) -> dict:
        return {}

    @staticmethod
    def _send_open_url(args: dict) -> dict:
        return {"url": str(args.get("url", ""))}

    @staticmethod
    def _send_new_tab(args: dict) -> dict:
        return {"url": args["url"]} if isinstance(args.get("url"), str) and args["url"] else {}

    @staticmethod
    def _send_search_chrome(args: dict) -> dict:
        return {"query": str(args.get("query", ""))}

    @staticmethod
    def _send_play_youtube_video(args: dict) -> dict:
        return {"query": args["query"]} if isinstance(args.get("query"), str) and args["query"] else {}

    @staticmethod
    def _tab_id(args: dict) -> int:
        tab_id = args.get("tab_id")
        if not isinstance(tab_id, int) or isinstance(tab_id, bool):
            raise WorkerError("bad_args", "I need to know which tab you mean.")
        return tab_id

    def _send_switch_tab(self, args: dict) -> dict:
        return {"tab_id": self._tab_id(args)}

    _send_close_tab = _send_switch_tab

    @staticmethod
    def _nth(args: dict) -> int:
        nth = args.get("nth", 0)
        return nth if isinstance(nth, int) and not isinstance(nth, bool) and nth >= 0 else 0

    def _send_click_text(self, args: dict) -> dict:
        return {"text": str(args.get("text", "")), "nth": self._nth(args), "expect": str(args.get("target", ""))}

    def _send_fill_field(self, args: dict) -> dict:
        return {"label": str(args.get("label", "")), "nth": self._nth(args), "value": str(args.get("value", "")),
                "expect": str(args.get("target", ""))}

    @staticmethod
    def _send_scroll_page(args: dict) -> dict:
        return {"direction": str(args.get("direction", ""))}

    # what the user is told

    @staticmethod
    def _page_name(data: dict) -> str:
        return clean(data.get("title")) or host_of(data.get("url", "")) or "the page"

    def _page_done(self, message: str, data: dict) -> ActionResult:
        """The tail every page-changing action shares: sign-in pages stop here; slow pages are mentioned."""
        steps = [clean(s, 120) for s in data.get("steps") or []]
        if data.get("login"):
            where = host_of(data.get("url", "")) or self._page_name(data)
            return ActionResult(
                "needs_login",
                f"{where} is asking you to sign in. I never type passwords or codes, so please sign in yourself "
                "in the Chrome window, then tell me to continue.",
                steps, via="chrome")
        if data.get("payment"):
            message += " This looks like a payment page, so I won't enter card details or pay without asking."
        if data.get("timed_out"):
            message += " The page is slow, so it may not have finished loading."
        return ActionResult("done", message, steps, via="chrome")

    def _done_open_chrome(self, args, data) -> ActionResult:
        steps = [clean(s, 120) for s in data.get("steps") or []]
        return ActionResult("done", "Chrome is open." if data.get("launched") else "Chrome is already open.", steps, via="chrome")

    def _done_open_url(self, args, data) -> ActionResult:
        return self._page_done(f"Opened {self._page_name(data)}.", data)

    def _done_search_chrome(self, args, data) -> ActionResult:
        return self._page_done(f"Searched Google for “{clean(args.get('query'), 60)}”.", data)

    def _done_new_tab(self, args, data) -> ActionResult:
        if not args.get("url"):
            return self._page_done("Opened a new tab.", data)
        return self._page_done(f"Opened a new tab with {self._page_name(data)}.", data)

    def _done_switch_tab(self, args, data) -> ActionResult:
        return self._page_done(f"Switched to {self._page_name(data)}.", data)

    def _done_close_tab(self, args, data) -> ActionResult:
        return ActionResult("done", f"Closed {clean(data.get('closed')) or 'the tab'}.", via="chrome")

    def _done_click_text(self, args, data) -> ActionResult:
        message = f"Clicked “{clean(data.get('clicked'), 60)}”."
        if data.get("new_tab"):
            message += f" It opened {self._page_name(data)} in a new tab."
        elif data.get("navigated"):
            message += f" Now on {self._page_name(data)}."
        return self._page_done(message, data)

    def _done_fill_field(self, args, data) -> ActionResult:
        return ActionResult("done", f"Typed into “{clean(data.get('filled'), 60)}”.", via="chrome")   # the text itself is never repeated

    def _done_scroll_page(self, args, data) -> ActionResult:
        direction = args.get("direction")
        if not data.get("moved"):
            edge = "top" if direction in ("up", "top") else "bottom"
            return ActionResult("done", f"Already at the {edge} of the page.", via="chrome")
        return ActionResult("done", f"Scrolled {direction}.", via="chrome")

    def _done_wait_for_page_load(self, args, data) -> ActionResult:
        if data.get("loaded"):
            return ActionResult("done", "The page has finished loading.", via="chrome")
        seconds = round((data.get("waited_ms") or 0) / 1000)
        return ActionResult("done", f"The page is still loading after {seconds} seconds.", via="chrome")

    def _done_get_page_title(self, args, data) -> ActionResult:
        title = clean(data.get("title"), 150)
        return ActionResult("done", f"The page title is “{title}”." if title else "This page has no title.", via="chrome")

    def _done_get_current_url(self, args, data) -> ActionResult:
        url = str(data.get("url", ""))
        if url.startswith("chrome-error:"):
            return ActionResult("done", "The last page didn't load, so Chrome is showing an error page.", via="chrome")
        if url in ("", "about:blank"):
            return ActionResult("done", "You're on a blank page.", via="chrome")
        return ActionResult("done", f"You're on {short_url(url)}.", via="chrome")

    def _done_play_youtube_video(self, args, data) -> ActionResult:
        steps = [clean(s, 120) for s in data.get("steps") or []]
        if data.get("consent"):
            return ActionResult("done", "YouTube is asking about cookies. Please accept or reject it yourself, then ask me again.",
                                steps, via="chrome")
        if data.get("opened_only"):
            return ActionResult("done", "Opened YouTube. Tell me what you'd like to play.", steps, via="chrome")
        if data.get("no_video"):
            return ActionResult("done", "I couldn't find a video to play on this page.", steps, via="chrome")
        if data.get("login") and not data.get("played"):
            return self._page_done("", data)
        title = clean(data.get("video_title"), 80)
        if data.get("played"):
            return ActionResult("done", f"Playing “{title}”." if title else "Playing the video.", steps, via="chrome")
        return ActionResult("done", "The video is open but wouldn't start. Press play in Chrome.", steps, via="chrome")
