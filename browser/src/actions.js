"use strict";

const { BrowserError } = require("./errors");
const { TIMEOUTS, MAX_TEXT } = require("./config");
const { normalizeUrl, searchUrl, hostOf } = require("./urls");
const { classifyPage } = require("./classify");
const scripts = require("./page_scripts");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const DIRECTIONS = new Set(["up", "down", "top", "bottom"]);
const MAX_VALUE = 2000;

function mapNavigationError(error) {
  const text = String((error && error.message) || error);
  if (/ERR_NAME_NOT_RESOLVED|ERR_CONNECTION|ERR_INTERNET_DISCONNECTED|ERR_ADDRESS_UNREACHABLE|ERR_NETWORK|ERR_TIMED_OUT|ERR_PROXY|ERR_TUNNEL|ERR_EMPTY_RESPONSE/.test(text)) {
    return new BrowserError("unreachable", "I couldn't reach that site. Check the address and your connection.");
  }
  if (/ERR_CERT|ERR_SSL/.test(text)) {
    return new BrowserError("unsafe_site", "That site's security certificate isn't trusted, so I didn't open it.");
  }
  if (/ERR_ABORTED|ERR_BLOCKED/.test(text)) {
    return new BrowserError("blocked", "That link wanted to download a file or was cancelled, so I didn't follow it.");
  }
  return new BrowserError("failed", "The page wouldn't open.");
}

function text(value, name, max = MAX_TEXT) {
  if (typeof value !== "string" || !value.trim() || value.length > max || /[\u0000-\u0008\u000b-\u001f\u007f]/.test(value)) {
    throw new BrowserError("bad_args", `The ${name} is missing or not usable.`);
  }
  return value;
}

/**
 * The things the assistant can do in Chrome. Each one returns plain data; none decides whether it is
 * allowed (the Python safety layer does) or phrases the answer for the user (the Python side does).
 */
function createActions(session, { timeouts = TIMEOUTS, youtubeOrigin = "https://www.youtube.com" } = {}) {
  async function currentPage() {
    if (!session.running) throw new BrowserError("no_browser", "Chrome isn't open yet.");
    const page = await session.current();
    if (!page) throw new BrowserError("no_page", "There is no tab open.");
    return page;
  }

  async function readState(page) {
    for (let attempt = 0; ; attempt++) {
      try {
        const state = await page.evaluate(scripts.pageStateInPage);
        return { ...state, ...classifyPage(state) };
      } catch (error) {
        if (attempt >= 1) throw new BrowserError("failed", "The page isn't ready yet.");
        await sleep(150);   // the page was changing under us; once more
      }
    }
  }

  const describe = (state) => ({
    url: state.url,
    title: state.title,
    login: state.login,
    login_reason: state.loginReason,
    payment: state.payment,
  });

  // Ends as soon as the page says it is complete; the timeout is only a ceiling.
  async function settle(page, ms) {
    try {
      await page.waitForFunction(() => document.readyState === "complete", { polling: 100, timeout: ms });
      return true;
    } catch {
      return false;
    }
  }

  async function navigate(page, url, steps) {
    steps.push(`Opening ${hostOf(url)}`);
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: timeouts.navigationMs });
    } catch (error) {
      if (/timeout/i.test(String(error && error.message))) {
        steps.push("The page is slow; it may still be loading");
        return { timedOut: true };
      }
      throw mapNavigationError(error);
    }
    const done = await settle(page, timeouts.settleMs);
    steps.push(done ? "Page loaded" : "Page is still finishing");
    return { timedOut: false };
  }

  async function pageFor(newTab, steps) {
    if (await session.ensure()) steps.push("Started Chrome");
    const page = newTab ? await session.newTab() : (await session.current()) || (await session.newTab());
    await session.focus(page);
    return page;
  }

  // ---- opening things ----------------------------------------------------------------------------------------

  async function open_chrome() {
    const steps = [];
    const launched = await session.ensure();
    if (launched) steps.push("Started Chrome");
    let page = await session.current();
    if (!page) page = await session.newTab();
    await session.focus(page);
    return { launched, tabs: (await session.pages()).length, steps };
  }

  async function open_url({ url, new_tab: newTab = false }) {
    const href = normalizeUrl(url);                      // checked before Chrome is touched
    const steps = [];
    const page = await pageFor(newTab === true, steps);
    const { timedOut } = await navigate(page, href, steps);
    return { ...describe(await readState(page)), steps, timed_out: timedOut };
  }

  async function search({ query }) {
    const href = searchUrl(query);
    const steps = [];
    const page = await pageFor(false, steps);
    steps.push("Searching Google");
    const { timedOut } = await navigate(page, href, steps);
    return { ...describe(await readState(page)), steps, timed_out: timedOut };
  }

  async function new_tab({ url } = {}) {
    const href = url === undefined || url === null ? null : normalizeUrl(url);
    const steps = [];
    const page = await pageFor(true, steps);
    steps.push("Opened a new tab");
    let timedOut = false;
    if (href) ({ timedOut } = await navigate(page, href, steps));
    const state = href ? await readState(page) : { url: page.url(), title: "", login: false, loginReason: "", payment: false };
    return { ...describe(state), steps, timed_out: timedOut };
  }

  // ---- tabs ----------------------------------------------------------------------------------------------------

  async function list_tabs() {
    if (!session.running) return { running: false, tabs: [] };
    return { running: true, tabs: await session.list() };
  }

  async function switch_tab({ tab_id: id }) {
    const page = await session.byId(id);
    if (!page) throw new BrowserError("not_found", "That tab isn't open any more.");
    await session.focus(page);
    return { ...describe(await readState(page)), steps: ["Switched tab"] };
  }

  async function close_tab({ tab_id: id }) {
    const page = await session.byId(id);
    if (!page) throw new BrowserError("not_found", "That tab isn't open any more.");
    let title = "";
    try {
      title = await page.title();
    } catch {
      /* ignore */
    }
    await page.close();
    if (session.active === page) session.active = null;
    return { closed: title, remaining: (await session.pages()).length, steps: ["Closed the tab"] };
  }

  // ---- reading the page (these never change anything) ---------------------------------------------------------------

  async function page_state() {
    if (!session.running) return { running: false };
    const page = await session.current();
    if (!page) return { running: true, page: null };
    return { running: true, page: describe(await readState(page)) };
  }

  async function find_clickable({ text: wanted, limit = 6 }) {
    if (!session.running) return { running: false };
    const page = await currentPage();
    const found = await page.evaluate(scripts.findClickableInPage, text(wanted, "text"), Math.min(Number(limit) || 6, 10));
    return { running: true, ...found, page: describe(await readState(page)) };
  }

  async function find_field({ label, limit = 6 }) {
    if (!session.running) return { running: false };
    const page = await currentPage();
    const found = await page.evaluate(scripts.findFieldInPage, text(label, "label"), Math.min(Number(limit) || 6, 10));
    return { running: true, ...found, page: describe(await readState(page)) };
  }

  async function get_title() {
    const page = await currentPage();
    return { title: await page.title() };
  }

  async function get_url() {
    const page = await currentPage();
    return { url: page.url() };
  }

  // ---- acting on the page --------------------------------------------------------------------------------------------

  function pick(found, nth) {
    const candidates = found.candidates || [];
    const exact = candidates.filter((c) => c.score >= 1);
    const pool = exact.length ? exact : candidates;
    return pool[Number.isInteger(nth) && nth >= 0 ? nth : 0];
  }

  async function clearMarks(page) {
    await page
      .evaluate(() => document.querySelectorAll("[data-assistant-n]").forEach((el) => el.removeAttribute("data-assistant-n")))
      .catch(() => {});
  }

  // Watches only as long as something is actually happening, then stops waiting.
  async function afterClick(page, before) {
    const started = Date.now();
    let navigated = false;
    while (Date.now() - started < timeouts.afterClickMs) {
      const pages = await session.pages();
      if (pages.length > before.pages) {
        const fresh = pages[pages.length - 1];
        await session.focus(fresh);
        await settle(fresh, timeouts.settleMs);
        return { page: fresh, navigated: true, newTab: true };
      }
      if (page.url() !== before.url) {
        navigated = true;
        break;
      }
      await sleep(60);
      if (Date.now() - started > 300) {
        const loading = await page.evaluate(() => document.readyState === "loading").catch(() => true);
        if (!loading) break;
      }
    }
    if (navigated) await settle(page, timeouts.settleMs);
    return { page, navigated, newTab: false };
  }

  // The Python side judges one specific label. If the page changed so that something else is now there, do nothing.
  const sameLabel = (a, b) => String(a).replace(/\s+/g, " ").trim().toLowerCase() === String(b).replace(/\s+/g, " ").trim().toLowerCase();
  const changed = () => new BrowserError("changed", "The page changed since I looked, so I didn't do that. Please ask again.");

  async function click({ text: label, nth = 0, expect }) {
    const page = await currentPage();
    const before = { url: page.url(), pages: (await session.pages()).length };
    const found = await page.evaluate(scripts.findClickableInPage, text(label, "text"), 10);
    const target = pick(found, nth);
    if (!target) throw new BrowserError("not_found", `I can't find “${label}” on the page any more.`);
    if (expect !== undefined && !sameLabel(expect, target.label)) {
      await clearMarks(page);
      throw changed();
    }
    const handle = await page.$(`[data-assistant-n="${target.n}"]`);
    if (!handle) throw new BrowserError("not_found", `I can't find “${label}” on the page any more.`);
    try {
      await handle.evaluate((el) => el.scrollIntoView({ block: "center", inline: "center" }));
      try {
        await handle.click();
      } catch {
        await handle.evaluate((el) => el.click());      // covered or oddly shaped: click it directly
      }
    } finally {
      await handle.dispose().catch(() => {});
    }
    const after = await afterClick(page, before);
    await clearMarks(page);
    const state = await readState(after.page).catch(() => null);
    return {
      clicked: target.label,
      navigated: after.navigated,
      new_tab: after.newTab,
      ...(state ? describe(state) : {}),
      steps: [`Clicked “${target.label}”`],
    };
  }

  async function fill({ label, nth = 0, value, expect }) {
    text(value, "text to type", MAX_VALUE);
    const page = await currentPage();
    const found = await page.evaluate(scripts.findFieldInPage, text(label, "label"), 10);
    const target = pick(found, nth);
    if (!target) throw new BrowserError("not_found", `I can't find a box called “${label}” on the page.`);
    if (expect !== undefined && !sameLabel(expect, target.label)) {
      await clearMarks(page);
      throw changed();
    }
    if (target.kind !== "text") {
      await clearMarks(page);
      throw new BrowserError("sensitive_field", "That is a password or card field. I never type into those. Please type it yourself.");
    }
    const handle = await page.$(`[data-assistant-n="${target.n}"]`);
    if (!handle) throw new BrowserError("not_found", `I can't find a box called “${label}” on the page.`);
    try {
      await handle.evaluate((el) => el.scrollIntoView({ block: "center" }));
      await handle.focus();
      await handle.evaluate((el) => {
        if ("value" in el) {
          el.value = "";
          el.dispatchEvent(new Event("input", { bubbles: true }));
        } else {
          el.textContent = "";
        }
      });
      await handle.type(value);
    } finally {
      await handle.dispose().catch(() => {});
    }
    await clearMarks(page);
    return { filled: target.label, characters: value.length, steps: [`Typed into “${target.label}”`] };
  }

  async function scroll({ direction }) {
    if (!DIRECTIONS.has(direction)) throw new BrowserError("bad_args", "I can scroll up, down, to the top or to the bottom.");
    const page = await currentPage();
    return { ...(await page.evaluate(scripts.scrollInPage, direction)), steps: [`Scrolled ${direction}`] };
  }

  async function wait_load({ timeout_ms: timeoutMs } = {}) {
    const page = await currentPage();
    const started = Date.now();
    const limit = Math.min(Number(timeoutMs) > 0 ? Number(timeoutMs) : timeouts.loadWaitMs, timeouts.loadWaitMs);
    const loaded = await settle(page, limit);
    return { loaded, waited_ms: Date.now() - started, ...describe(await readState(page).catch(() => ({ url: page.url(), title: "" }))) };
  }

  // ---- YouTube -------------------------------------------------------------------------------------------------------

  const youtubeHost = new URL(youtubeOrigin).host;
  const onYoutube = (href) => {
    try {
      return new URL(href).host === youtubeHost || new URL(href).host.endsWith(".youtube.com");
    } catch {
      return false;
    }
  };

  async function youtube_play({ query } = {}) {
    const steps = [];
    const page = await pageFor(false, steps);
    let title = "";

    if (typeof query === "string" && query.trim()) {
      text(query, "search");
      await navigate(page, `${youtubeOrigin}/results?search_query=${encodeURIComponent(query.trim())}`, steps);
    } else if (!onYoutube(page.url())) {
      await navigate(page, youtubeOrigin, steps);
      return { played: false, opened_only: true, ...describe(await readState(page)), steps };
    }

    if (new URL(page.url()).hostname.startsWith("consent.")) {
      return { played: false, consent: true, ...describe(await readState(page)), steps };
    }

    if (!new URL(page.url()).pathname.startsWith("/watch")) {
      await page.waitForSelector("a[href^='/watch'], a[href*='youtube.com/watch']", { timeout: timeouts.findMs }).catch(() => {});
      const video = await page.evaluate(scripts.firstVideoLinkInPage);
      if (!video) return { played: false, no_video: true, ...describe(await readState(page)), steps };
      title = video.title;
      steps.push(`Opening “${title}”`);
      await navigate(page, video.href, steps);
    }

    await page.waitForSelector("video", { timeout: timeouts.findMs }).catch(() => {});
    let result = await page.evaluate(scripts.playVideoInPage);
    if (result.found && !result.playing) {
      // Chrome may need a real click before it lets a video start
      const button = await page.$(".ytp-play-button");
      if (button) {
        await button.click().catch(() => {});
        await button.dispose().catch(() => {});
        result = { ...result, playing: await page.evaluate(() => { const v = document.querySelector("video"); return !!v && !v.paused; }) };
      }
    }
    const state = await readState(page);
    steps.push(result.playing ? "Playing" : "The video didn't start");
    return { played: !!result.playing, video_found: !!result.found, video_title: title || state.title, ...describe(state), steps };
  }

  // ---- housekeeping --------------------------------------------------------------------------------------------------

  async function status() {
    if (!session.running) return { running: false, tabs: 0 };
    const tabs = await session.list();
    const active = tabs.find((t) => t.active) || null;
    return { running: true, tabs: tabs.length, active: active ? { title: active.title, host: hostOf(active.url) } : null };
  }

  async function quit() {
    await session.shutdown();
    return { closed: true };
  }

  return {
    open_chrome, open_url, search, new_tab, list_tabs, switch_tab, close_tab, page_state,
    find_clickable, find_field, get_title, get_url, click, fill, scroll, wait_load, youtube_play, status, quit,
  };
}

module.exports = { createActions, mapNavigationError, DIRECTIONS };
