"use strict";

// These tests drive a REAL Chrome/Chromium against a small local website.
// They are skipped if no browser is found (set ASSISTANT_CHROME_PATH to point at one).

const assert = require("node:assert/strict");
const { after, before, describe, it } = require("node:test");

const { createActions } = require("../src/actions");
const { TIMEOUTS } = require("../src/config");
const { Session } = require("../src/session");
const { findChrome, startSite, startWorker, tempDir } = require("./helpers");

const chrome = findChrome();
const skip = chrome ? false : "no Chrome or Chromium found";

describe("real Chrome that cannot start", { skip: skip || !(process.getuid && process.getuid() === 0) && "only a root user cannot start Chrome with its sandbox" }, () => {
  it("is reported without taking the worker down", async (t) => {
    const w = await startWorker({ chrome });                   // as root, Chrome refuses to start without --no-sandbox
    t.after(() => w.kill());
    const first = await w.request("POST", "/action", { action: "open_chrome", args: {} });
    assert.equal(first.body.ok, false);
    assert.equal(first.body.code, "no_chrome");
    assert.equal((await w.request("GET", "/ping")).status, 200);
    const second = await w.request("POST", "/action", { action: "open_url", args: { url: "https://example.com" } });
    assert.equal(second.body.code, "no_chrome");
  });
});

describe("real browser", { skip }, () => {
  let site, session, a;

  before(async () => {
    site = await startSite();
    session = new Session({
      puppeteer: require("puppeteer-core"),
      chromePath: chrome,
      profileDir: tempDir(),
      headless: true,
      extraArgs: process.getuid && process.getuid() === 0 ? ["--no-sandbox"] : [],
    });
    a = createActions(session, { youtubeOrigin: site.origin });
  });

  after(async () => {
    await session.shutdown();
    await site.close();
  });

  const error = async (promise) => promise.then(() => null, (e) => e);

  it("starts Chrome once and reuses it", async () => {
    const first = await a.open_chrome();
    assert.equal(first.launched, true);
    assert.ok(first.steps.includes("Started Chrome"));
    const second = await a.open_chrome();
    assert.equal(second.launched, false);
    assert.equal((await a.status()).running, true);
  });

  it("opens a page and reports its title", async () => {
    const r = await a.open_url({ url: site.url("/") });
    assert.equal(r.title, "Home");
    assert.equal(r.login, false);
    assert.equal(r.timed_out, false);
    assert.ok(r.steps.some((s) => s.startsWith("Opening 127.0.0.1")));
    assert.equal((await a.get_title()).title, "Home");
    assert.equal((await a.get_url()).url, site.url("/"));
  });

  it("reports an unreachable site plainly", async () => {
    const closed = await startSite();
    const url = closed.url("/");
    await closed.close();                                   // nothing listens on this port any more
    const e = await error(a.open_url({ url }));
    assert.equal(e.code, "unreachable");
  });

  it("refuses to follow a download", async () => {
    await a.open_url({ url: site.url("/") });
    const e = await error(a.open_url({ url: site.url("/file.zip") }));
    assert.equal(e.code, "blocked");
  });

  it("is not stuck by alert and confirm pop-ups", async () => {
    const r = await a.open_url({ url: site.url("/alert") });
    assert.equal(r.title, "Alerts");
  });

  describe("clicking", () => {
    it("finds a link by its text and follows it", async () => {
      await a.open_url({ url: site.url("/") });
      const found = await a.find_clickable({ text: "about us" });
      assert.equal(found.candidates.length, 1);
      assert.equal(found.candidates[0].label, "About us");
      assert.equal(found.candidates[0].kind, "link");
      assert.equal(found.candidates[0].context, "nav");
      const r = await a.click({ text: "About us" });
      assert.equal(r.navigated, true);
      assert.equal(r.title, "About");
    });

    it("lists look-alike links separately and clicks the one asked for", async () => {
      await a.open_url({ url: site.url("/") });
      const found = await a.find_clickable({ text: "read more" });
      assert.deepEqual(found.candidates.map((c) => c.label).slice(0, 2), ["Read more", "Read more"]);
      assert.equal(found.candidates[2].label, "Read more articles");           // a longer name ranks after the exact ones
      const r = await a.click({ text: "Read more", nth: 1 });
      assert.equal(r.title, "Page B");
      await a.open_url({ url: site.url("/") });
      // `nth` counts among the exact matches only, so the longer name can never be clicked by mistake
      assert.equal((await error(a.click({ text: "Read more", nth: 2 }))).code, "not_found");
    });

    it("clicks a button that changes the page without leaving it", async () => {
      await a.open_url({ url: site.url("/") });
      const started = Date.now();
      const r = await a.click({ text: "Show message" });
      assert.equal(r.navigated, false);
      assert.ok(Date.now() - started < 1200, "should not wait for a navigation that is not coming");
      const text = await (await session.current()).evaluate(() => document.getElementById("out").textContent);
      assert.equal(text, "Hello!");
    });

    it("ignores hidden and disabled things", async () => {
      await a.open_url({ url: site.url("/") });
      assert.equal((await a.find_clickable({ text: "secret" })).candidates.length, 0);
      assert.equal((await a.find_clickable({ text: "disabled one" })).candidates.length, 0);
    });

    it("falls back to plain visible text", async () => {
      await a.open_url({ url: site.url("/") });
      const found = await a.find_clickable({ text: "plain text item" });
      assert.equal(found.candidates.length, 1);
      assert.equal(found.candidates[0].fallback, true);
    });

    it("notices when a click opens a new tab", async () => {
      await a.open_url({ url: site.url("/") });
      const before = (await session.pages()).length;
      const r = await a.click({ text: "Open docs" });
      assert.equal(r.new_tab, true);
      assert.equal(r.title, "Docs");
      assert.equal((await session.pages()).length, before + 1);
      const docs = await session.current();
      await docs.close();
      session.active = null;
    });

    it("says what a button belongs to, so risky ones can be recognised", async () => {
      await a.open_url({ url: site.url("/") });
      const pay = await a.find_clickable({ text: "pay now" });
      assert.equal(pay.candidates[0].formKind, "payment");
      await a.open_url({ url: site.url("/login") });
      const login = await a.find_clickable({ text: "log in" });
      assert.equal(login.candidates[0].formKind, "login");
      assert.equal(login.page.login, true);
    });

    it("refuses to click when the thing found is not what was approved", async () => {
      await a.open_url({ url: site.url("/") });
      const e = await error(a.click({ text: "read more", expect: "Delete everything" }));
      assert.equal(e.code, "changed");
      assert.equal((await a.get_url()).url, site.url("/"));            // nothing was clicked
      const ok = await a.click({ text: "read more", nth: 0, expect: "  READ   more " });
      assert.equal(ok.navigated, true);
    });

    it("tells the caller when the thing is gone", async () => {
      await a.open_url({ url: site.url("/plain") });
      const e = await error(a.click({ text: "Not here" }));
      assert.equal(e.code, "not_found");
    });
  });

  describe("typing", () => {
    before(async () => {
      await a.open_url({ url: site.url("/form") });
    });
    const value = async (selector) => (await session.current()).$eval(selector, (el) => el.value);

    it("finds a box by its label", async () => {
      const r = await a.fill({ label: "full name", value: "Ada Lovelace" });
      assert.equal(r.filled, "Full name");
      assert.equal(await value("#name"), "Ada Lovelace");
    });

    it("replaces what was there before", async () => {
      await a.fill({ label: "Full name", value: "Grace" });
      assert.equal(await value("#name"), "Grace");
    });

    it("finds a box by its placeholder, aria-label or a wrapping label", async () => {
      await a.fill({ label: "email address", value: "ada@example.com" });
      assert.equal(await value("input[placeholder='Email address']"), "ada@example.com");
      await a.fill({ label: "search", value: "cats" });
      assert.equal(await value("input[aria-label='Search the site']"), "cats");
      await a.fill({ label: "message", value: "line one\nline two" });
      assert.equal(await value("textarea"), "line one\nline two");
    });

    it("never types into password, card or one-time-code boxes", async () => {
      for (const label of ["password", "card number", "one-time code"]) {
        const e = await error(a.fill({ label, value: "secret" }));
        assert.equal(e.code, "sensitive_field", label);
      }
      assert.equal(await value("#pw"), "");
      assert.equal(await value("input[autocomplete='cc-number']"), "");
    });

    it("refuses to type when the box found is not the one approved", async () => {
      const e = await error(a.fill({ label: "full name", value: "x", expect: "Email address" }));
      assert.equal(e.code, "changed");
      assert.equal(await value("#name"), "Grace");
    });

    it("reports a box that does not exist", async () => {
      assert.equal((await error(a.fill({ label: "nickname", value: "x" }))).code, "not_found");
    });

    it("treats a one-time-code box as sensitive even when nothing else says so", async () => {
      assert.equal((await a.find_field({ label: "sms digits" })).candidates[0].kind, "password");
      assert.equal((await error(a.fill({ label: "sms digits", value: "123456" }))).code, "sensitive_field");
    });

    it("does not offer checkboxes or buttons as boxes to type into", async () => {
      assert.equal((await a.find_field({ label: "remember me" })).candidates.length, 0);
      assert.equal((await a.find_field({ label: "save details" })).candidates.length, 0);
    });

    it("marks sensitive boxes so they can be recognised before typing", async () => {
      const found = await a.find_field({ label: "password" });
      assert.equal(found.candidates[0].kind, "password");
      assert.equal((await a.find_field({ label: "full name" })).candidates[0].kind, "text");
    });
  });

  describe("scrolling", () => {
    it("moves down, up, to the top and to the bottom", async () => {
      await a.open_url({ url: site.url("/") });
      const down = await a.scroll({ direction: "down" });
      assert.ok(down.y > 0 && down.moved);
      const up = await a.scroll({ direction: "up" });
      assert.ok(up.y < down.y);
      const bottom = await a.scroll({ direction: "bottom" });
      assert.equal(bottom.atBottom, true);
      const top = await a.scroll({ direction: "top" });
      assert.equal(top.atTop, true);
      assert.equal((await a.scroll({ direction: "up" })).moved, false);
    });

    it("rejects directions it does not know", async () => {
      assert.equal((await error(a.scroll({ direction: "sideways" }))).code, "bad_args");
    });
  });

  describe("tabs", () => {
    it("opens, lists, switches and closes tabs", async () => {
      await a.open_url({ url: site.url("/about") });
      const created = await a.new_tab({ url: site.url("/plain") });
      assert.equal(created.title, "Plain page");
      const tabs = (await a.list_tabs()).tabs;
      assert.ok(tabs.length >= 2);
      const plain = tabs.find((t) => t.title === "Plain page");
      const about = tabs.find((t) => t.title === "About");
      assert.equal(plain.active, true);

      const switched = await a.switch_tab({ tab_id: about.id });
      assert.equal(switched.title, "About");
      assert.equal((await a.list_tabs()).tabs.find((t) => t.id === about.id).active, true);
      assert.equal((await a.get_title()).title, "About");       // actions now apply to the tab that was switched to

      const closed = await a.close_tab({ tab_id: plain.id });
      assert.equal(closed.closed, "Plain page");
      assert.equal((await error(a.switch_tab({ tab_id: plain.id }))).code, "not_found");
    });

    it("opens a blank new tab", async () => {
      const before = (await a.list_tabs()).tabs.length;
      const r = await a.new_tab({});
      assert.ok(r.steps.includes("Opened a new tab"));
      assert.equal((await a.list_tabs()).tabs.length, before + 1);
    });
  });

  describe("waiting for a page", () => {
    it("waits until the page has finished loading, then stops waiting", async () => {
      const quick = createActions(session, { timeouts: { ...TIMEOUTS, settleMs: 30 } });
      const opened = await quick.open_url({ url: site.url("/slow") });
      assert.match(opened.steps.join(" "), /still finishing/);       // returned before the slow image arrived
      const r = await quick.wait_load({});
      assert.equal(r.loaded, true);
      assert.ok(r.waited_ms > 300 && r.waited_ms < 3000, `waited ${r.waited_ms} ms`);
      const again = await quick.wait_load({});
      assert.equal(again.loaded, true);
      assert.ok(again.waited_ms < 600, "an already loaded page needs no waiting");
    });

    it("gives up politely when a page never finishes", async () => {
      const impatient = createActions(session, { timeouts: { ...TIMEOUTS, settleMs: 30, loadWaitMs: 400 } });
      await impatient.open_url({ url: site.url("/slower") });
      const r = await impatient.wait_load({});
      assert.equal(r.loaded, false);
      assert.ok(r.waited_ms >= 350 && r.waited_ms < 1500, `waited ${r.waited_ms} ms`);
    });
  });

  describe("signing in and paying", () => {
    it("recognises a sign-in page", async () => {
      const r = await a.open_url({ url: site.url("/login") });
      assert.equal(r.login, true);
      assert.match(r.login_reason, /password box/);
      assert.equal((await a.page_state()).page.login, true);
    });

    it("recognises a payment page", async () => {
      const r = await a.open_url({ url: site.url("/checkout") });
      assert.equal(r.payment, true);
      assert.equal(r.login, false);
    });

    it("does not call an ordinary page a sign-in page", async () => {
      const r = await a.open_url({ url: site.url("/plain") });
      assert.equal(r.login, false);
      assert.equal(r.payment, false);
    });
  });

  describe("YouTube", () => {
    it("searches, skips ads and shorts, and plays the first real video", async () => {
      // the test site stands in for YouTube: /results lists videos (an ad and a Short first) and /watch plays one
      const r = await a.youtube_play({ query: "lofi" });
      assert.equal(r.played, true);
      assert.equal(r.video_title, "Lofi beats to relax");
      assert.match((await a.get_url()).url, /\/watch\?v=1$/);
    });

    it("plays the first video on a results page", async () => {
      await a.open_url({ url: site.url("/results") });
      const r = await a.youtube_play({});
      assert.equal(r.played, true);
      assert.equal(r.video_title, "Lofi beats to relax");
      assert.match((await a.get_url()).url, /\/watch\?v=1$/);
    });

    it("presses play on a video page that is already open", async () => {
      await a.open_url({ url: site.url("/watch") });
      assert.equal((await a.youtube_play({})).played, true);
    });

    it("does not guess when there is no video", async () => {
      await a.open_url({ url: site.url("/novideo") });
      const r = await a.youtube_play({});
      assert.equal(r.played, false);
      assert.equal(r.no_video, true);
    });

    it("opens YouTube and stops when it is not on YouTube and was not told what to play", async () => {
      await a.open_url({ url: "http://localhost:" + new URL(site.origin).port + "/plain" });   // a different host
      const r = await a.youtube_play({});
      assert.equal(r.played, false);
      assert.equal(r.opened_only, true);
    });
  });
});
