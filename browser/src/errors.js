"use strict";

/** An expected problem with a plain message the user can read. `code` lets the caller react to it. */
class BrowserError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "BrowserError";
    this.code = code;
  }
}

module.exports = { BrowserError };
