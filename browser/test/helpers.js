"use strict";

const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

/** A Chrome or Chromium to test with, or null (then the real-browser tests are skipped). */
function findChrome() {
  const candidates = [process.env.ASSISTANT_CHROME_PATH, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"];
  for (const root of ["/opt/pw-browsers"]) {
    try {
      for (const dir of fs.readdirSync(root)) {
        if (!dir.startsWith("chromium-")) continue;
        for (const sub of fs.readdirSync(path.join(root, dir))) candidates.push(path.join(root, dir, sub, "chrome"));
      }
    } catch {
      /* not there */
    }
  }
  return candidates.find((c) => c && fs.existsSync(c)) || null;
}

function tempDir(prefix = "assistant-browser-test-") {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

const PAGES = {
  "/": `<title>Home</title><body style="margin:0">
    <nav><a href="/about">About us</a></nav>
    <main>
      <a href="/a">Read more</a> <a href="/b">Read more</a> <a href="/c">Read more articles</a>
      <button id="show" onclick="document.getElementById('out').textContent='Hello!'">Show message</button>
      <span id="out"></span>
      <button style="display:none">Secret</button>
      <button disabled>Disabled one</button>
      <div><span>Plain text item</span></div>
      <a href="/docs" target="_blank">Open docs</a>
      <a href="/file.zip">Download report</a>
      <a href="/login">Sign in</a>
      <form action="/checkout"><input name="cardnumber" placeholder="Card number"><button type="button">Pay now</button></form>
      <div style="height:3000px">tall</div>
    </main></body>`,
  "/about": "<title>About</title><h1>About us</h1>",
  "/a": "<title>Page A</title><h1>A</h1>",
  "/c": "<title>Page C</title><h1>C</h1>",
  "/b": "<title>Page B</title><h1>B</h1>",
  "/docs": "<title>Docs</title><h1>Docs</h1>",
  "/form": `<title>Form</title>
    <label for="name">Full name</label><input id="name">
    <input placeholder="Email address">
    <input aria-label="Search the site">
    <label>Message <textarea></textarea></label>
    <label for="pw">Password</label><input id="pw" type="password">
    <input name="cardnumber" placeholder="Card number" autocomplete="cc-number">
    <input type="text" name="otp" placeholder="One-time code">
    <label for="sms">SMS digits</label><input id="sms" autocomplete="one-time-code">
    <label><input type="checkbox"> Remember me</label>
    <input type="submit" value="Save details" aria-label="Save details">`,
  "/login": `<title>Sign in</title><form><input type="email" placeholder="Email"><input type="password" placeholder="Password"><button>Log in</button></form>`,
  "/checkout": `<title>Checkout</title><form><input autocomplete="cc-number" placeholder="Card number"><button>Place order</button></form>`,
  "/plain": "<title>Plain page</title><p>Nothing special</p>",
  "/alert": "<title>Alerts</title><script>alert('hi'); confirm('sure?')</script><p>done</p>",
  "/slow": `<title>Slow</title><p>wait for it</p><img src="/slow.png?ms=1200">`,
  "/slower": `<title>Slower</title><p>wait for it</p><img src="/slow.png?ms=4000">`,
  "/results": `<title>Results</title>
    <ytd-ad-slot-renderer><a href="/watch?v=ad" title="Buy now ad" style="display:block;width:100px;height:20px">Ad</a></ytd-ad-slot-renderer>
    <a href="/shorts/xyz" title="A short" style="display:block;width:100px;height:20px">short</a>
    <a href="/watch?v=1" title="Lofi beats to relax" style="display:block;width:200px;height:20px">Lofi beats to relax</a>`,
  "/novideo": "<title>No results</title><p>nothing</p>",
  "/watch": `<title>Lofi beats to relax</title><video></video>
    <script>HTMLMediaElement.prototype.play = function () { Object.defineProperty(this, "paused", { value: false }); return Promise.resolve(); };</script>`,
};

/** A tiny website to test against. Listens on a free local port. */
function startSite() {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    if (url.pathname === "/file.zip") {
      res.writeHead(200, { "Content-Type": "application/zip", "Content-Disposition": "attachment; filename=report.zip" });
      return res.end("PK");
    }
    if (url.pathname === "/slow.png") {
      return setTimeout(() => {
        res.writeHead(200, { "Content-Type": "image/gif" });
        res.end(Buffer.from("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7", "base64"));
      }, Number(url.searchParams.get("ms")) || 1000);
    }
    const body = PAGES[url.pathname];
    if (!body) {
      res.writeHead(404, { "Content-Type": "text/html" });
      return res.end("<title>Not found</title>");
    }
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(`<!doctype html><meta charset="utf-8">${body}`);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const origin = `http://127.0.0.1:${server.address().port}`;
      resolve({ origin, url: (p) => origin + p, close: () => new Promise((r) => server.close(r)) });
    });
  });
}

/** Starts the real worker process (src/server.js) and returns a way to call it; kill() stops it. */
async function startWorker({ chrome, extraArgs = [] }) {
  const { spawn } = require("child_process");
  const net = require("net");
  const dir = tempDir();
  const token = "w".repeat(32);
  const tokenFile = path.join(dir, "token");
  fs.writeFileSync(tokenFile, token + "\n");
  const port = await new Promise((resolve) => {
    const probe = net.createServer().listen(0, "127.0.0.1", () => { const p = probe.address().port; probe.close(() => resolve(p)); });
  });
  const child = spawn(process.execPath, [path.join(__dirname, "..", "src", "server.js"), "--port", String(port),
    "--token-file", tokenFile, "--chrome", chrome, "--profile", path.join(dir, "profile"), ...extraArgs], { stdio: "ignore" });
  const request = (method, urlPath, body, host = "127.0.0.1", useToken = token) => new Promise((resolve, reject) => {
    const data = body === undefined ? undefined : JSON.stringify(body);
    const req = http.request({ host, port, method, path: urlPath, timeout: 30000,
      headers: { ...(useToken ? { "X-Assistant-Token": useToken } : {}), ...(data ? { "Content-Type": "application/json" } : {}) } },
    (res) => { let text = ""; res.on("data", (d) => (text += d)); res.on("end", () => resolve({ status: res.statusCode, body: text ? JSON.parse(text) : null })); });
    req.on("error", reject);
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.end(data);
  });
  for (let i = 0; i < 50; i++) {
    const ready = await request("GET", "/ping").then((r) => r.status === 200, () => false);
    if (ready) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  return { port, token, request, child, kill: () => child.kill() };
}

module.exports = { findChrome, startSite, startWorker, tempDir };
