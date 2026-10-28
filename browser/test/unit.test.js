"use strict";

// Fast tests that need no Chrome: a fake puppeteer records what would have been done.

const assert = require("node:assert/strict");
const fs = require("fs");
const http = require("http");
const path = require("path");
const { describe, it } = require("node:test");

const { createActions } = require("../src/actions");
const { classifyPage } = require("../src/classify");
const { BASE_ARGS, Session } = require("../src/session");
const { createServer, parseArgs, sameToken } = require("../src/server");
const { hostOf, normalizeUrl, searchUrl } = require("../src/urls");
const { startWorker, tempDir } = require("./helpers");

class FakePage {
  constructor(browser, title = "", url = "about:blank") {
    this.browser = browser;
    this.titleText = title;
    this.href = url;
    this.gone = false;
    this.handlers = {};
    this.visible = true;
    this.gotos = [];
    this.fronted = 0;
  }
  on(event, fn) { this.handlers[event] = fn; }
  isClosed() { return this.gone; }
  url() { return this.href; }
  async title() { return this.titleText; }
  async bringToFront() { this.fronted++; for (const p of this.browser.list) p.visible = p === this; }
  async close() { this.gone = true; this.browser.list = this.browser.list.filter((p) => p !== this); }
  async goto(url, options) {
    this.gotos.push({ url, options });
    if (this.browser.failWith) throw new Error(this.browser.failWith);
    this.href = url;
    this.titleText = `Title of ${url}`;
  }
  async waitForFunction() { return true; }
  async evaluate(fn) {
    const src = String(fn);
    if (src.includes("visibilityState")) return this.visible ? "visible" : "hidden";
    if (src.includes("readyState")) return this.browser.pageFacts(this);
    throw new Error("unexpected evaluate");
  }
}

class FakeBrowser {
  constructor() {
    this.list = [new FakePage(this)];
    this.connected = true;
    this.closed = false;
    this.cdpCalls = [];
    this.listeners = {};
    this.failWith = null;
    this.pageFacts = (page) => ({
      url: page.href, title: page.titleText, readyState: "complete", passwordInputs: 0, cardInputs: 0, heading: "",
    });
  }
  on(event, fn) { this.listeners[event] = fn; }
  async pages() { return [...this.list]; }
  async newPage() { const p = new FakePage(this); this.list.push(p); return p; }
  target() { return { createCDPSession: async () => ({ send: async (m, a) => { this.cdpCalls.push([m, a]); } }) }; }
  async close() { this.closed = true; this.connected = false; }
}

function fakePuppeteer(browser = new FakeBrowser()) {
  const calls = [];
  return { calls, browser, launch: async (options) => { calls.push(options); return browser; } };
}

function setup(extra = {}) {
  const profileDir = path.join(tempDir(), "profile");
  const chromePath = path.join(tempDir(), "chrome");
  fs.writeFileSync(chromePath, "");
  const pp = fakePuppeteer();
  const session = new Session({ puppeteer: pp, chromePath, profileDir, ...extra });
  return { pp, session, profileDir, chromePath, actions: createActions(session) };
}

const fails = (promise) => promise.then(() => null, (e) => e);

describe("launching the browser", () => {
  it("starts Chrome with a private pipe and its own profile folder", async () => {
    const { pp, session, profileDir, chromePath } = setup({ headless: true });
    assert.equal(await session.ensure(), true);
    const options = pp.calls[0];
    assert.equal(options.executablePath, chromePath);
    assert.equal(options.userDataDir, profileDir);
    assert.equal(options.pipe, true);                                  // no debugging port for other programs to find
    assert.ok(!options.args.some((a) => a.includes("remote-debugging-port")));
    assert.deepEqual(options.args, BASE_ARGS);
    assert.deepEqual(options.ignoreDefaultArgs, ["--enable-automation"]);
    assert.equal(fs.statSync(profileDir).mode & 0o777, 0o700);
  });

  it("refuses downloads from the start", async () => {
    const { pp, session } = setup();
    await session.ensure();
    assert.deepEqual(pp.browser.cdpCalls, [["Browser.setDownloadBehavior", { behavior: "deny" }]]);
  });

  it("does not start a second Chrome", async () => {
    const { pp, session } = setup();
    assert.equal(await session.ensure(), true);
    assert.equal(await session.ensure(), false);
    assert.equal(pp.calls.length, 1);
  });

  it("starts again if Chrome was closed", async () => {
    const { pp, session } = setup();
    await session.ensure();
    pp.browser.connected = false;
    pp.browser.listeners.disconnected();
    assert.equal(session.running, false);
    assert.equal(await session.ensure(), true);
  });

  it("says plainly when Chrome is not installed", async () => {
    const session = new Session({ puppeteer: fakePuppeteer(), chromePath: "/nope/chrome", profileDir: tempDir() });
    assert.equal((await fails(session.ensure())).code, "no_chrome");
  });

  it("says plainly when Chrome will not start", async () => {
    const { session } = setup();
    session.puppeteer = { launch: async () => { throw new Error("boom: /secret/path"); } };
    const e = await fails(session.ensure());
    assert.equal(e.code, "no_chrome");
    assert.ok(!e.message.includes("secret"));
  });

  it("stops again if downloads cannot be refused", async () => {
    const { pp, session } = setup();
    pp.browser.target = () => { throw new Error("no cdp"); };
    assert.equal((await fails(session.ensure())).code, "failed");
    assert.equal(pp.browser.closed, true);
  });

  it("answers pop-ups with no, and leaves pages that ask before closing", async () => {
    const { pp, session } = setup();
    await session.ensure();
    const page = pp.browser.list[0];
    const seen = [];
    page.handlers.dialog({ type: () => "alert", dismiss: async () => seen.push("dismiss"), accept: async () => seen.push("accept") });
    page.handlers.dialog({ type: () => "beforeunload", dismiss: async () => seen.push("dismiss"), accept: async () => seen.push("accept") });
    assert.deepEqual(seen, ["dismiss", "accept"]);
  });
});

describe("searching", () => {
  it("opens Chrome if needed and searches Google, encoding the question", async () => {
    const { pp, actions } = setup();
    const r = await actions.search({ query: "AI tools & more?" });
    assert.equal(pp.calls.length, 1);
    const goto = pp.browser.list[0].gotos[0];
    assert.equal(goto.url, "https://www.google.com/search?q=AI%20tools%20%26%20more%3F");
    assert.equal(goto.options.waitUntil, "domcontentloaded");
    assert.deepEqual(r.steps, ["Started Chrome", "Searching Google", "Opening google.com", "Page loaded"]);
    assert.equal(r.login, false);
  });

  it("uses the tab that is already open", async () => {
    const { pp, actions } = setup();
    await actions.open_chrome();
    await actions.search({ query: "one" });
    await actions.search({ query: "two" });
    assert.equal(pp.browser.list.length, 1);
  });

  it("rejects an empty or control-character search before touching Chrome", async () => {
    const { pp, actions } = setup();
    for (const query of ["", "   ", "a\u0000b", 5, undefined]) assert.equal((await fails(actions.search({ query }))).code, "bad_args");
    assert.equal(pp.calls.length, 0);
  });

  it("explains network failures, certificate failures and refused downloads", async () => {
    const { pp, actions } = setup();
    await actions.open_chrome();
    const cases = { "net::ERR_NAME_NOT_RESOLVED": "unreachable", "net::ERR_TUNNEL_CONNECTION_FAILED": "unreachable", "net::ERR_PROXY_CONNECTION_FAILED": "unreachable", "net::ERR_CERT_DATE_INVALID": "unsafe_site", "net::ERR_ABORTED": "blocked", "weird": "failed" };
    for (const [message, code] of Object.entries(cases)) {
      pp.browser.failWith = message;
      assert.equal((await fails(actions.open_url({ url: "https://example.com" }))).code, code, message);
    }
  });

  it("does not hang when a page is slow to start", async () => {
    const { pp, actions } = setup();
    await actions.open_chrome();
    pp.browser.failWith = "Navigation timeout of 20000 ms exceeded";
    const r = await actions.open_url({ url: "https://example.com" });
    assert.equal(r.timed_out, true);
  });
});

describe("tabs", () => {
  it("opens a new tab, with or without an address", async () => {
    const { pp, actions } = setup();
    await actions.open_chrome();
    const blank = await actions.new_tab({});
    assert.equal(pp.browser.list.length, 2);
    assert.ok(blank.steps.includes("Opened a new tab"));
    const withUrl = await actions.new_tab({ url: "calendar.google.com" });
    assert.equal(pp.browser.list.length, 3);
    assert.equal(pp.browser.list[2].gotos[0].url, "https://calendar.google.com/");
    assert.equal(withUrl.title, "Title of https://calendar.google.com/");
  });

  it("checks the address of a new tab before opening anything", async () => {
    const { pp, actions } = setup();
    assert.equal((await fails(actions.new_tab({ url: "javascript:alert(1)" }))).code, "bad_url");
    assert.equal(pp.calls.length, 0);
  });

  it("lists tabs and switches to the one asked for", async () => {
    const { pp, actions } = setup();
    await actions.open_chrome();
    await actions.new_tab({ url: "youtube.com" });
    await actions.new_tab({ url: "mail.google.com" });
    const tabs = (await actions.list_tabs()).tabs;
    assert.equal(tabs.length, 3);
    assert.deepEqual(tabs.map((t) => t.index), [1, 2, 3]);
    assert.deepEqual(tabs.map((t) => t.active), [false, false, true]);
    const youtube = tabs.find((t) => t.url.includes("youtube"));
    const r = await actions.switch_tab({ tab_id: youtube.id });
    assert.equal(r.url, "https://youtube.com/");
    assert.equal((await actions.list_tabs()).tabs.find((t) => t.id === youtube.id).active, true);
    assert.equal((await actions.get_url()).url, "https://youtube.com/");
  });

  it("refuses a tab that is gone, and closes the right one", async () => {
    const { actions } = setup();
    await actions.open_chrome();
    await actions.new_tab({ url: "youtube.com" });
    const tabs = (await actions.list_tabs()).tabs;
    await actions.close_tab({ tab_id: tabs[1].id });
    assert.equal((await fails(actions.switch_tab({ tab_id: tabs[1].id }))).code, "not_found");
    assert.equal((await actions.list_tabs()).tabs.length, 1);
  });

  it("needs Chrome to be open for tab and page actions", async () => {
    const { actions } = setup();
    assert.deepEqual((await actions.list_tabs()).tabs, []);
    assert.equal((await fails(actions.get_title())).code, "no_browser");
    assert.equal((await actions.status()).running, false);
  });
});

describe("web addresses", () => {
  it("adds https to bare domains and keeps the path", () => {
    assert.equal(normalizeUrl("example.com/a?b=1"), "https://example.com/a?b=1");
    assert.equal(normalizeUrl("  http://example.com "), "http://example.com/");
    assert.equal(normalizeUrl("localhost:3000/x"), "https://localhost:3000/x");
  });
  it("refuses other schemes, credentials, spaces and made-up hosts", () => {
    for (const bad of ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,hi", "mailto:a@b.com", "ftp://x.com", "chrome://settings",
      "https://user:pw@example.com", "exa mple.com", "intranet", "", "https://exa\nmple.com", 5, null]) {
      assert.throws(() => normalizeUrl(bad), (e) => e.code === "bad_url", String(bad));
    }
  });
  it("builds search addresses and names hosts", () => {
    assert.equal(searchUrl("a b"), "https://www.google.com/search?q=a%20b");
    assert.throws(() => searchUrl("x".repeat(501)), (e) => e.code === "bad_args");
    assert.equal(hostOf("https://www.youtube.com/watch"), "youtube.com");
    assert.equal(hostOf("garbage"), "that page");
  });
});

describe("login and payment detection", () => {
  const c = (o) => classifyPage(o);
  it("a visible password box means sign-in", () => {
    const r = c({ url: "https://example.com/", passwordInputs: 1 });
    assert.equal(r.login, true);
    assert.match(r.loginReason, /password box/);
  });
  it("known sign-in addresses count even before the form shows", () => {
    for (const url of ["https://accounts.google.com/v3/signin", "https://login.microsoftonline.com/x", "https://example.com/login", "https://example.com/users/sign-in", "https://x.com/Account/Login.aspx", "https://x.com/ServiceLogin"]) {
      assert.equal(c({ url }).login, true, url);
    }
  });
  it("ordinary pages are not sign-in pages, whatever their title says", () => {
    for (const url of ["https://example.com/", "https://example.com/blog/login-tips-for-beginners-guide", "https://mail.google.com/mail/u/0/#inbox"]) {
      assert.equal(c({ url, title: "Log in to your account" }).login, false, url);
    }
  });
  it("card boxes and checkout addresses mean payment", () => {
    assert.equal(c({ url: "https://shop.example/x", cardInputs: 1 }).payment, true);
    assert.equal(c({ url: "https://shop.example/checkout" }).payment, true);
    assert.equal(c({ url: "https://shop.example/" }).payment, false);
  });
  it("copes with missing or broken input", () => {
    assert.equal(c({}).login, false);
    assert.equal(c({ url: "not a url" }).login, false);
    assert.equal(classifyPage().payment, false);
  });
});

describe("the worker process survives a Chrome that will not start", () => {
  const broken = (body) => {
    const file = path.join(tempDir(), "chrome");
    fs.writeFileSync(file, `#!/bin/sh\n${body}\n`, { mode: 0o755 });
    return file;
  };
  for (const [name, body] of [["exits at once", "exit 1"], ["prints and dies", "echo boom >&2; exit 127"], ["hangs up the pipe", "exec 3<&- 4<&-; exit 0"]]) {
    it(`a Chrome that ${name}`, async (t) => {
      const w = await startWorker({ chrome: broken(body) });
      t.after(() => w.kill());
      for (let i = 0; i < 2; i++) {                      // the second request proves the worker is still there
        const r = await w.request("POST", "/action", { action: "open_chrome", args: {} });
        assert.equal(r.body.ok, false);
        assert.equal(r.body.code, "no_chrome");
        assert.ok(!JSON.stringify(r.body).includes("boom"));
      }
      assert.equal((await w.request("GET", "/ping")).status, 200);
    });
  }
});

describe("the worker process", () => {
  it("listens on this computer only and wants the token", async (t) => {
    const { spawn } = require("child_process");
    const net = require("net");
    const os = require("os");
    const dir = tempDir();
    const tokenFile = path.join(dir, "token");
    fs.writeFileSync(tokenFile, "w".repeat(32) + "\n");
    const port = await new Promise((resolve) => {
      const probe = net.createServer().listen(0, "127.0.0.1", () => { const p = probe.address().port; probe.close(() => resolve(p)); });
    });
    const child = spawn(process.execPath, [path.join(__dirname, "..", "src", "server.js"), "--port", String(port),
      "--token-file", tokenFile, "--chrome", "/nonexistent/chrome", "--profile", path.join(dir, "profile")], { stdio: "ignore" });
    t.after(() => child.kill());
    const get = (host, token) => new Promise((resolve, reject) => {
      const req = http.get({ host, port, path: "/ping", headers: token ? { "X-Assistant-Token": token } : {}, timeout: 1000 },
        (res) => { res.resume(); resolve(res.statusCode); });
      req.on("error", reject);
      req.on("timeout", () => req.destroy(new Error("timeout")));
    });
    let status = null;
    for (let i = 0; i < 50 && status === null; i++) {
      status = await get("127.0.0.1", "w".repeat(32)).catch(() => null);
      if (status === null) await new Promise((r) => setTimeout(r, 100));
    }
    assert.equal(status, 200);
    assert.equal(await get("127.0.0.1", "wrong"), 401);
    const outside = Object.values(os.networkInterfaces()).flat().find((i) => i && i.family === "IPv4" && !i.internal);
    if (outside) await assert.rejects(get(outside.address, "w".repeat(32)));        // not reachable from the network
  });
});

describe("the worker's local server", () => {
  async function serve(actions = { ping_test: async () => ({ hello: "world" }), boom: async () => { throw new Error("secret details"); } }) {
    const dir = tempDir();
    const tokenFile = path.join(dir, ".token");
    fs.writeFileSync(tokenFile, "s3cret-token-0123456789\n");
    const server = createServer({ actions, tokenFile, requestTimeoutMs: 300 });
    await new Promise((r) => server.listen(0, "127.0.0.1", r));
    const port = server.address().port;
    const call = (method, url, { token = "s3cret-token-0123456789", body } = {}) =>
      new Promise((resolve, reject) => {
        const req = http.request({ host: "127.0.0.1", port, method, path: url, headers: token === null ? {} : { "X-Assistant-Token": token } }, (res) => {
          let data = "";
          res.on("data", (d) => (data += d));
          res.on("end", () => resolve({ status: res.statusCode, body: JSON.parse(data) }));
        });
        req.on("error", reject);
        req.end(body === undefined ? undefined : typeof body === "string" ? body : JSON.stringify(body));
      });
    return { call, tokenFile, close: () => new Promise((r) => server.close(r)) };
  }

  it("answers only with the right token", async (t) => {
    const s = await serve();
    t.after(() => s.close());
    assert.equal((await s.call("GET", "/ping")).status, 200);
    assert.equal((await s.call("GET", "/ping", { token: null })).status, 401);
    assert.equal((await s.call("GET", "/ping", { token: "wrong" })).status, 401);
    assert.equal((await s.call("POST", "/action", { token: "", body: { action: "ping_test" } })).status, 401);
    fs.unlinkSync(s.tokenFile);
    assert.equal((await s.call("GET", "/ping")).status, 503);
  });

  it("runs only the named actions", async (t) => {
    const s = await serve();
    t.after(() => s.close());
    const ok = await s.call("POST", "/action", { body: { action: "ping_test", args: {} } });
    assert.deepEqual(ok.body, { ok: true, hello: "world" });
    for (const name of ["constructor", "__proto__", "toString", "eval", 5]) {
      assert.equal((await s.call("POST", "/action", { body: { action: name } })).status, 400, String(name));
    }
    assert.equal((await s.call("POST", "/action", { body: "not json" })).status, 400);
    assert.equal((await s.call("POST", "/action", { body: { action: "ping_test", args: [1] } })).status, 400);
    assert.equal((await s.call("GET", "/action")).status, 404);
  });

  it("does not leak error details and gives up on a stuck action", async (t) => {
    const s = await serve({ boom: async () => { throw new Error("secret details"); }, stuck: () => new Promise(() => {}) });
    t.after(() => s.close());
    const boom = await s.call("POST", "/action", { body: { action: "boom" } });
    assert.equal(boom.status, 500);
    assert.ok(!JSON.stringify(boom.body).includes("secret"));
    const stuck = await s.call("POST", "/action", { body: { action: "stuck" } });
    assert.equal(stuck.body.ok, false);
    assert.equal(stuck.body.code, "timeout");
  });

  it("compares tokens safely and reads its options", () => {
    assert.equal(sameToken("abc", "abc"), true);
    assert.equal(sameToken("abc", "abd"), false);
    assert.equal(sameToken("abc", "abcd"), false);
    const args = parseArgs(["--port", "9000", "--headless", "--no-sandbox", "--token-file", "/t"]);
    assert.equal(args.port, 9000);
    assert.equal(args.headless && args.noSandbox, true);
    assert.equal(args.tokenFile, "/t");
  });
});
