"use strict";

// Every wait in one place. Waits end as soon as the thing they wait for happens; these are only ceilings.
module.exports = {
  VERSION: "1",
  TIMEOUTS: {
    navigationMs: 20000,   // how long a page may take to start loading
    settleMs: 3000,        // after that, how long to wait for it to finish (a slow page is not an error)
    loadWaitMs: 10000,     // wait_for_page_load
    afterClickMs: 1500,    // how long to watch for a click to start a navigation
    findMs: 2000,          // looking for something on the page
  },
  MAX_BODY_BYTES: 16 * 1024,
  MAX_TEXT: 500,           // longest label or value accepted
  IDLE_MINUTES: 30,        // the browser is closed after this long without a request (0 = never)
};
