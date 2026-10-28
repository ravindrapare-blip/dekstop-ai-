"use strict";

const { BrowserError } = require("./errors");

const MAX_URL = 2048;
const MAX_QUERY = 500;

function bad(message) {
  return new BrowserError("bad_url", message);
}

/** Returns a safe https/http address, or throws. Used by every way of navigating. */
function normalizeUrl(input) {
  if (typeof input !== "string") throw bad("That is not a web address.");
  const text = input.trim();
  if (!text || text.length > MAX_URL || /[\u0000-\u001f\u007f\s]/.test(text)) throw bad("That is not a web address.");

  // "localhost:3000" has a port, it does not have a scheme called "localhost"
  const looksLikeHostAndPort = /^[a-zA-Z0-9.-]+:\d{1,5}(?:[/?#]|$)/.test(text);
  const hasScheme = /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(text) && !looksLikeHostAndPort;
  if (hasScheme && !/^https?:\/\//i.test(text)) throw bad("Only http and https addresses can be opened.");

  let url;
  try {
    url = new URL(hasScheme ? text : `https://${text}`);
  } catch {
    throw bad("That is not a web address.");
  }
  if (url.username || url.password) throw bad("Addresses with a name and password in them are not allowed.");
  if (!url.hostname.includes(".") && url.hostname !== "localhost" && !url.hostname.includes(":")) {
    throw bad("That is not a web address.");
  }
  return url.href;
}

function searchUrl(query) {
  if (typeof query !== "string") throw new BrowserError("bad_args", "There is nothing to search for.");
  const text = query.trim();
  if (!text || text.length > MAX_QUERY || /[\u0000-\u001f\u007f]/.test(text)) {
    throw new BrowserError("bad_args", "There is nothing to search for.");
  }
  return `https://www.google.com/search?q=${encodeURIComponent(text)}`;
}

function hostOf(href) {
  try {
    return new URL(href).hostname.replace(/^www\./, "");
  } catch {
    return "that page";
  }
}

module.exports = { normalizeUrl, searchUrl, hostOf };
