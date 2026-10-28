"""The whole chain with nothing pretended: safety layer -> BrowserService -> real Node worker -> real Chromium,
against a small website on this computer. Skipped when Node, the worker's packages or a Chrome/Chromium are missing."""
import json
import os
import shutil
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.config import ROOT
from backend.pipeline import Pipeline
from browserctl.client import WorkerClient
from browserctl.launcher import WorkerLauncher
from browserctl.service import BrowserService
from safety import AuditLog, GuardrailManager, Outcome, Policy

POLICY = Policy.load(ROOT / "safety" / "policy.yaml")
SCRIPT = ROOT / "browser" / "src" / "server.js"


def find_chrome() -> str | None:
    for candidate in [os.environ.get("ASSISTANT_CHROME_PATH"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]:
        if candidate and Path(candidate).exists():
            return candidate
    for path in sorted(Path("/opt/pw-browsers").glob("chromium-*/*/chrome")):
        return str(path)
    return None


CHROME = find_chrome()
pytestmark = pytest.mark.skipif(
    not (shutil.which("node") and (ROOT / "browser" / "node_modules" / "puppeteer-core").is_dir() and CHROME),
    reason="needs Node, 'npm install' in browser/, and Chrome or Chromium")

PAGES = {
    "/": '<title>Home</title><nav><a href="/about">About us</a></nav><button onclick="document.title=\'Clicked\'">Show message</button>'
         '<a href="/login">Sign in</a><a href="/file.zip">Download report</a><div style="height:3000px">tall</div>',
    "/about": "<title>About</title><h1>About us</h1>",
    "/form": '<title>Form</title><label for="n">Full name</label><input id="n"><label for="c">Verification code</label><input id="c" autocomplete="one-time-code">'
             '<button>Send</button>',
    "/login": '<title>Sign in</title><form><input type="email" placeholder="Email"><input type="password" placeholder="Password"><button>Next</button></form>',
}


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/file.zip":
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", "attachment; filename=report.zip")
            self.end_headers()
            self.wfile.write(b"PK")
            return
        body = PAGES.get(self.path.split("?")[0])
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(("<!doctype html><meta charset=utf-8>" + (body or "<title>Not found</title>")).encode())

    def log_message(self, *args):
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    token_file = tmp / "token"
    token_file.write_text("e" * 32 + "\n")
    port = free_port()
    client = WorkerClient(f"http://127.0.0.1:{port}", token_file)
    extra = ("--headless", "--no-sandbox") if os.getuid() == 0 else ("--headless",)
    launcher = WorkerLauncher(client=client, node="node", script=SCRIPT, port=port, token_file=token_file, chrome_path=CHROME,
                              profile_dir=tmp / "profile", log_file=tmp / "browser.log", idle_minutes=5, extra_args=extra)
    service = BrowserService(client, launcher)
    audit = tmp / "audit.jsonl"
    pipeline = Pipeline(GuardrailManager(POLICY, AuditLog(audit)), service)
    yield pipeline, service, audit, port
    service.close()


def test_the_whole_journey(stack, site):
    p, service, audit, port = stack
    run = lambda tool, **args: p.run(tool, args)
    approve = lambda out, ack=False: p.after_confirmation(p.guard.confirm(out.decision.confirmation_id, True, acknowledge_risk=ack))

    assert not service.client.ping()                              # nothing runs until it is needed
    assert "open Chrome" in run("get_page_title").result.message          # nothing is open yet
    opened = run("open_chrome")
    assert opened.result.status == "done" and "Started Chrome" in opened.result.items
    assert service.client.ping()

    home = run("open_url", url=site + "/")
    assert home.decision.outcome is Outcome.ALLOW and home.result.message == "Opened Home."
    assert run("get_page_title").result.message == "The page title is “Home”."
    assert run("get_current_url").result.message == f"You're on {site}/."

    # a safe click follows the link
    about = run("click_text", text="about us")
    assert about.decision.outcome is Outcome.ALLOW and about.result.status == "done" and "About" in about.result.message
    run("open_url", url=site + "/")

    # a click that only changes the page
    assert run("click_text", text="show message").result.status == "done"
    assert run("get_page_title").result.message == "The page title is “Clicked”."

    # scrolling and waiting
    assert run("scroll_page", direction="down").result.message == "Scrolled down."
    assert run("scroll_page", direction="top").result.message == "Scrolled top."
    assert run("wait_for_page_load").result.message == "The page has finished loading."

    # tabs
    tab = run("new_tab", url=site + "/form")
    assert tab.result.message == "Opened a new tab with Form."
    assert run("switch_tab", tab="1").result.message == "Switched to Clicked."
    assert run("switch_tab", tab="form").result.message == "Switched to Form."
    close = run("close_tab", index=1)
    assert close.decision.outcome is Outcome.CONFIRM
    assert approve(close).message == "Closed Clicked."
    assert run("switch_tab", tab="9").result.message == "There is only 1 tab open."

    # typing: a normal box works, a password box and a risky button need the user
    run("open_url", url=site + "/form")
    assert run("fill_field", label="full name", value="Ada Lovelace").result.message == "Typed into “Full name”."
    blocked = run("fill_field", label="verification code", value="hunter2-secret")
    assert blocked.decision.outcome is Outcome.BLOCK and blocked.result is None
    send = run("click_text", text="send")
    assert send.decision.outcome is Outcome.CONFIRM
    assert approve(send).status == "done"

    # a sign-in page stops the assistant
    login = run("open_url", url=site + "/login")
    assert login.decision.outcome is Outcome.CONFIRM and login.result is None      # a sign-in looking address asks first
    result = approve(login)
    assert result.status == "needs_login" and "never type passwords" in result.message
    asked = run("fill_field", label="email", value="ada@example.com")
    assert asked.decision.outcome is Outcome.CONFIRM and "sign-in page" in asked.decision.reason
    assert run("fill_field", label="password", value="x").decision.outcome is Outcome.BLOCK

    # downloads are refused
    run("open_url", url=site + "/")
    download = run("click_text", text="download report")
    assert download.decision.outcome is Outcome.CONFIRM          # "download" is a risky word, so it asks first
    assert approve(download).status in ("done", "failed")
    assert "report.zip" not in " ".join(os.listdir(Path.home())) if (Path.home()).exists() else True

    # an address that is not allowed never reaches the browser
    assert run("open_url", url="file:///etc/passwd").decision.outcome is Outcome.BLOCK

    # the audit log recorded all of it and none of the typed text
    log = audit.read_text()
    assert "Ada Lovelace" not in log and "hunter2" not in log and "ada@example.com" not in log
    events = [json.loads(line) for line in log.splitlines()]
    assert any(e["stage"] == "execute" and e["tool"] == "click_text" for e in events)


def test_stopping_the_helper_closes_chrome(stack):
    p, service, *_ = stack
    service.close()
    for _ in range(50):
        if not service.client.ping():
            break
        import time
        time.sleep(0.1)
    assert not service.client.ping()
