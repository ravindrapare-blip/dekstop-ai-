"use strict";

const fs = require("fs");
const { BrowserError } = require("./errors");

// Arguments for Chrome. Only what is needed: a quiet first start, and no automation banner.
const BASE_ARGS = ["--no-first-run", "--no-default-browser-check"];

/**
 * The one Chrome window the assistant controls, in its own profile folder (never your everyday profile).
 * Chrome is started with a private pipe to this process, not a network port, so no other program can
 * connect to it and see the logins stored in its profile.
 */
class Session {
  constructor({ puppeteer, chromePath, profileDir, headless = false, extraArgs = [], log = () => {} }) {
    this.puppeteer = puppeteer;
    this.chromePath = chromePath;
    this.profileDir = profileDir;
    this.headless = headless;
    this.extraArgs = extraArgs;
    this.log = log;
    this.browser = null;
    this.active = null;
    this.ids = new WeakMap();
    this.nextId = 1;
  }

  get running() {
    return !!this.browser && this.browser.connected !== false;
  }

  /** Starts Chrome if it is not running. Returns true if it had to be started. */
  async ensure() {
    if (this.running) return false;
    if (!this.chromePath || !fs.existsSync(this.chromePath)) {
      throw new BrowserError("no_chrome", "I can't find Google Chrome. Check browser.chrome_path in config.yaml.");
    }
    fs.mkdirSync(this.profileDir, { recursive: true, mode: 0o700 });
    try {
      fs.chmodSync(this.profileDir, 0o700);
    } catch {
      /* not ours to change */
    }
    let browser;
    try {
      browser = await this.puppeteer.launch({
        executablePath: this.chromePath,
        userDataDir: this.profileDir,
        headless: this.headless,
        pipe: true,
        defaultViewport: null,
        ignoreDefaultArgs: ["--enable-automation"],
        args: [...BASE_ARGS, ...this.extraArgs],
      });
    } catch (e) {
      this.log(`launch failed: ${e.message}`);
      throw new BrowserError("no_chrome", "I couldn't start Chrome. If the assistant's Chrome window is already open, close it and try again.");
    }
    this.browser = browser;
    this.active = null;
    browser.on("disconnected", () => {
      if (this.browser === browser) {
        this.browser = null;
        this.active = null;
      }
    });
    try {
      // Downloads are never allowed: a link must not be able to put files on the Mac.
      const cdp = await browser.target().createCDPSession();
      await cdp.send("Browser.setDownloadBehavior", { behavior: "deny" });
    } catch (e) {
      this.log(`could not deny downloads: ${e.message}`);
      await this.shutdown();
      throw new BrowserError("failed", "I couldn't start Chrome safely, so I stopped.");
    }
    browser.on("targetcreated", async (target) => {
      if (target.type() !== "page") return;
      try {
        this.watch(await target.page());
      } catch {
        /* the tab closed already */
      }
    });
    for (const page of await browser.pages()) this.watch(page);
    return true;
  }

  watch(page) {
    if (!page) return;
    // Pop-up questions are answered "no" (or "leave", for a page that asks before closing).
    page.on("dialog", (dialog) => {
      const action = dialog.type() === "beforeunload" ? dialog.accept() : dialog.dismiss();
      Promise.resolve(action).catch(() => {});
    });
  }

  idOf(page) {
    if (!this.ids.has(page)) this.ids.set(page, this.nextId++);
    return this.ids.get(page);
  }

  async pages() {
    if (!this.running) return [];
    return (await this.browser.pages()).filter((p) => !p.isClosed());
  }

  /** The tab the user is looking at. In a visible window only one tab reports itself visible. */
  async current() {
    const pages = await this.pages();
    if (!pages.length) return null;
    const visible = [];
    for (const page of pages) {
      try {
        if ((await page.evaluate(() => document.visibilityState)) === "visible") visible.push(page);
      } catch {
        /* a page that is changing; ignore it */
      }
    }
    if (this.active && visible.includes(this.active)) return this.active;
    if (visible.length === 1) return visible[0];
    return this.active && pages.includes(this.active) ? this.active : pages[pages.length - 1];
  }

  async list() {
    const pages = await this.pages();
    const current = await this.current();
    const tabs = [];
    for (let i = 0; i < pages.length; i++) {
      let title = "";
      try {
        title = await pages[i].title();
      } catch {
        /* ignore */
      }
      tabs.push({
        id: this.idOf(pages[i]),
        index: i + 1,
        title,
        url: pages[i].url(),
        active: pages[i] === current,
      });
    }
    return tabs;
  }

  async byId(id) {
    for (const page of await this.pages()) if (this.idOf(page) === id) return page;
    return null;
  }

  async newTab() {
    const page = await this.browser.newPage();
    this.watch(page);
    this.active = page;
    await page.bringToFront().catch(() => {});
    return page;
  }

  async focus(page) {
    this.active = page;
    await page.bringToFront().catch(() => {});
  }

  async shutdown() {
    const browser = this.browser;
    this.browser = null;
    this.active = null;
    if (browser) await browser.close().catch(() => {});
  }
}

module.exports = { Session, BASE_ARGS };
