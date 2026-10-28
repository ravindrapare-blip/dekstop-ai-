"use strict";

// A small HTTP server on 127.0.0.1 that the Python backend talks to. Only named actions are accepted,
// every request needs the shared token, and requests are handled one at a time.

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");

const { createActions } = require("./actions");
const { VERSION, MAX_BODY_BYTES, IDLE_MINUTES } = require("./config");
const { BrowserError } = require("./errors");
const { Session } = require("./session");

function readToken(file) {
  try {
    const token = fs.readFileSync(file, "utf8").split("\n")[0].trim();
    return token.length >= 16 ? token : null;
  } catch {
    return null;
  }
}

function sameToken(given, expected) {
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new BrowserError("too_large", "request too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function withTimeout(promise, ms) {
  let timer;
  const limit = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new BrowserError("timeout", "That took too long.")), ms);
  });
  return Promise.race([promise, limit]).finally(() => clearTimeout(timer));
}

function createServer({ actions, tokenFile, onActivity = () => {}, log = () => {}, requestTimeoutMs = 60000 }) {
  let chain = Promise.resolve();
  const oneAtATime = (fn) => {
    const run = chain.then(fn, fn);
    chain = run.catch(() => {});
    return run;
  };

  return http.createServer(async (req, res) => {
    const reply = (status, body) => {
      const data = JSON.stringify(body);
      res.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) });
      res.end(data);
    };
    try {
      const token = readToken(tokenFile);
      if (!token) return reply(503, { ok: false, code: "no_token", error: "token file missing" });
      if (!sameToken(String(req.headers["x-assistant-token"] || ""), token)) {
        return reply(401, { ok: false, code: "bad_token", error: "bad token" });
      }
      onActivity();

      if (req.method === "GET" && req.url === "/ping") return reply(200, { ok: true, version: VERSION });
      if (req.method !== "POST" || req.url !== "/action") return reply(404, { ok: false, code: "not_found", error: "not found" });

      let request;
      try {
        request = JSON.parse(await readBody(req));
      } catch (error) {
        if (error instanceof BrowserError) return reply(413, { ok: false, code: error.code, error: error.message });
        return reply(400, { ok: false, code: "bad_request", error: "unreadable request" });
      }
      const name = request && request.action;
      if (typeof name !== "string" || !Object.prototype.hasOwnProperty.call(actions, name)) {
        return reply(400, { ok: false, code: "unknown_action", error: "unknown action" });
      }
      const args = request.args === undefined || request.args === null ? {} : request.args;
      if (typeof args !== "object" || Array.isArray(args)) {
        return reply(400, { ok: false, code: "bad_request", error: "args must be an object" });
      }

      const result = await oneAtATime(() => withTimeout(actions[name](args), requestTimeoutMs));
      log(`${name}: ok`);
      return reply(200, { ok: true, ...result });
    } catch (error) {
      if (error instanceof BrowserError) {
        log(`action failed: ${error.code}`);
        const status = error.code === "bad_url" || error.code === "bad_args" ? 400 : 200;
        return reply(status, { ok: false, code: error.code, error: error.message });
      }
      log(`unexpected error: ${error && error.message}`);      // details stay in the log, never in the reply
      return reply(500, { ok: false, code: "failed", error: "The browser action failed." });
    }
  });
}

function parseArgs(argv) {
  const out = { port: 8766, idleMinutes: IDLE_MINUTES, headless: false, noSandbox: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--port") out.port = Number(argv[++i]);
    else if (arg === "--token-file") out.tokenFile = argv[++i];
    else if (arg === "--chrome") out.chrome = argv[++i];
    else if (arg === "--profile") out.profile = argv[++i];
    else if (arg === "--idle-minutes") out.idleMinutes = Number(argv[++i]);
    else if (arg === "--headless") out.headless = true;
    else if (arg === "--no-sandbox") out.noSandbox = true;
  }
  return out;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (!options.tokenFile || !options.chrome || !options.profile || !Number.isInteger(options.port)) {
    console.error("usage: server.js --port N --token-file F --chrome PATH --profile DIR [--idle-minutes N] [--headless]");
    process.exit(2);
  }
  const puppeteer = require("puppeteer-core");
  const log = (message) => console.log(`${new Date().toISOString()} ${message}`);
  const session = new Session({
    puppeteer,
    chromePath: options.chrome,
    profileDir: path.resolve(options.profile),
    headless: options.headless,
    extraArgs: options.noSandbox ? ["--no-sandbox"] : [],
    log,
  });

  let idleTimer = null;
  let server;
  const shutdown = async () => {
    await session.shutdown();
    if (server) server.close();
    process.exit(0);
  };
  const touch = () => {
    clearTimeout(idleTimer);
    if (options.idleMinutes > 0) idleTimer = setTimeout(shutdown, options.idleMinutes * 60 * 1000);
  };

  // If Chrome dies while it is starting or running, puppeteer's pipe to it can emit an error nobody listens to.
  // That must not take the worker down: the next request simply starts Chrome again.
  process.on("uncaughtException", (error) => {
    log(`unexpected error: ${(error && (error.code || error.message)) || error}`);
    if (!error || (error.code !== "ECONNRESET" && error.code !== "EPIPE")) shutdown();
  });
  process.on("unhandledRejection", (reason) => log(`unhandled rejection: ${(reason && reason.message) || reason}`));

  server = createServer({ actions: createActions(session), tokenFile: options.tokenFile, onActivity: touch, log });
  server.listen(options.port, "127.0.0.1", () => log(`browser worker listening on 127.0.0.1:${options.port}`));
  touch();
  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);

  // If the backend that started this worker disappears, the worker (and its Chrome) go away too.
  const parent = process.ppid;
  setInterval(() => {
    if (process.ppid !== parent) shutdown();
  }, 15000).unref();
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`browser worker failed: ${error && error.message}`);
    process.exit(1);
  });
}

module.exports = { createServer, readToken, sameToken, parseArgs };
