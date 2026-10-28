"""A pretend browser worker: the same calls and answers as browser/src/server.js, with no Chrome."""
from urllib.parse import urlparse

from browserctl.client import WorkerError, WorkerUnavailable


class FakeWorker:
    def __init__(self, login_hosts=(), payment_hosts=()):
        self.calls: list[tuple[str, dict]] = []
        self.running = False
        self.tabs: list[dict] = []
        self.next_id = 1
        self.login_hosts = set(login_hosts)
        self.payment_hosts = set(payment_hosts)
        self.buttons: list[dict] = []       # what find_clickable returns
        self.fields: list[dict] = []        # what find_field returns
        self.page_flags: dict = {}          # extra "page" facts for find_*: {"login": True}
        self.errors: dict[str, Exception] = {}
        self.slow = False
        self.scroll_moves = True
        self.loaded = True
        self.pinged = True
        self.filter_by_query = False        # True: only return candidates whose label contains the query

    # the interface BrowserService uses
    def ping(self) -> bool:
        return self.pinged

    def call(self, action: str, **args) -> dict:
        self.calls.append((action, args))
        if action in self.errors:
            raise self.errors[action]
        return getattr(self, f"do_{action}")(**args)

    def names(self) -> list[str]:
        return [a for a, _ in self.calls]

    def last(self, action: str) -> dict:
        return next(args for name, args in reversed(self.calls) if name == action)

    # helpers
    def _tab(self, url="about:blank", title="") -> dict:
        tab = {"id": self.next_id, "index": len(self.tabs) + 1, "title": title, "url": url, "active": True}
        self.next_id += 1
        for t in self.tabs:
            t["active"] = False
        self.tabs.append(tab)
        return tab

    def _active(self) -> dict:
        if not self.running:
            raise WorkerError("no_browser", "Chrome isn't open yet.")
        return next(t for t in self.tabs if t["active"])

    def _go(self, tab: dict, url: str, steps: list) -> dict:
        host = urlparse(url).hostname or ""
        tab["url"] = url
        tab["title"] = f"Page of {host}"
        steps += [f"Opening {host}", "Page loaded"]
        return {"url": url, "title": tab["title"], "login": host in self.login_hosts,
                "login_reason": "a password box" if host in self.login_hosts else "",
                "payment": host in self.payment_hosts, "steps": steps, "timed_out": self.slow}

    def _launch(self, steps: list) -> None:
        if not self.running:
            self.running = True
            self._tab()
            steps.append("Started Chrome")

    # worker actions
    def do_open_chrome(self):
        steps: list = []
        launched = not self.running
        self._launch(steps)
        return {"launched": launched, "tabs": len(self.tabs), "steps": steps}

    def do_open_url(self, url, new_tab=False):
        steps: list = []
        self._launch(steps)
        return self._go(self._active(), url, steps)

    def do_search(self, query):
        steps: list = ["Searching Google"]
        self._launch(steps)
        return self._go(self._active(), "https://www.google.com/search?q=" + query.replace(" ", "%20"), steps)

    def do_new_tab(self, url=None):
        steps: list = []
        self._launch(steps)
        tab = self._tab()
        steps.append("Opened a new tab")
        if url:
            return self._go(tab, url, steps)
        return {"url": "about:blank", "title": "", "login": False, "login_reason": "", "payment": False,
                "steps": steps, "timed_out": False}

    def do_list_tabs(self):
        return {"tabs": [dict(t) for t in self.tabs]}

    def _by_id(self, tab_id):
        tab = next((t for t in self.tabs if t["id"] == tab_id), None)
        if tab is None:
            raise WorkerError("not_found", "That tab isn't open any more.")
        return tab

    def do_switch_tab(self, tab_id):
        tab = self._by_id(tab_id)
        for t in self.tabs:
            t["active"] = t is tab
        host = urlparse(tab["url"]).hostname or ""
        return {"url": tab["url"], "title": tab["title"], "login": host in self.login_hosts, "login_reason": "",
                "payment": False, "steps": ["Switched tab"]}

    def do_close_tab(self, tab_id):
        tab = self._by_id(tab_id)
        self.tabs.remove(tab)
        for i, t in enumerate(self.tabs, 1):
            t["index"] = i
        if self.tabs and not any(t["active"] for t in self.tabs):
            self.tabs[-1]["active"] = True
        return {"closed": tab["title"]}

    def _page(self):
        tab = self._active()
        host = urlparse(tab["url"]).hostname or ""
        return {"url": tab["url"], "title": tab["title"], "login": host in self.login_hosts, "payment": False} | self.page_flags

    def do_find_clickable(self, text, limit=6):
        if not self.running:
            return {"running": False}
        found = [c for c in self.buttons if not self.filter_by_query or text.lower() in c["label"].lower()]
        return {"running": True, "candidates": found, "total": len(found), "page": self._page()}

    def do_find_field(self, label, limit=6):
        if not self.running:
            return {"running": False}
        found = [c for c in self.fields if not self.filter_by_query or label.lower() in c["label"].lower()]
        return {"running": True, "candidates": found, "total": len(found), "page": self._page()}

    def do_click(self, text, nth=0, expect=None):
        self._active()
        return {"clicked": expect or text, "navigated": False, "new_tab": False, **self._page(), "login_reason": "",
                "steps": [f"Clicked “{expect or text}”"]}

    def do_fill(self, label, value, nth=0, expect=None):
        self._active()
        return {"filled": expect or label, "characters": len(value), "steps": ["Typed"]}

    def do_scroll(self, direction):
        self._active()
        return {"y": 400, "moved": self.scroll_moves, "atTop": False, "atBottom": False, "steps": [f"Scrolled {direction}"]}

    def do_wait_load(self, timeout_ms=None):
        tab = self._active()
        return {"loaded": self.loaded, "waited_ms": 120 if self.loaded else 10000, "url": tab["url"], "title": tab["title"]}

    def do_get_title(self):
        return {"title": self._active()["title"]}

    def do_get_url(self):
        return {"url": self._active()["url"]}

    def do_youtube_play(self, query=None):
        self._launch([])
        return {"played": True, "video_found": True, "video_title": "Lofi beats", "url": "https://www.youtube.com/watch?v=1",
                "title": "Lofi beats", "login": False, "login_reason": "", "payment": False, "steps": ["Playing"]}


def cand(label, score=1.0, kind="button", context="main", form="", fallback=False):
    return {"n": 0, "label": label, "score": score, "kind": kind, "context": context, "formKind": form, "fallback": fallback}


def field(label, kind="text", score=1.0, type_="text"):
    return {"n": 0, "label": label, "score": score, "type": type_, "kind": kind}


__all__ = ["FakeWorker", "cand", "field", "WorkerError", "WorkerUnavailable"]
