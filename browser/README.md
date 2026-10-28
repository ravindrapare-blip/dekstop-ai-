# browser/

The Chrome worker (phase 6). A small Node program that drives Chrome through `puppeteer-core` (pinned to 23.11.1 so it runs on Node 20; no Chromium download). Setup, safety rules and limits: `docs/browser.md`.

| File | Job |
|---|---|
| `src/server.js` | Local HTTP server on `127.0.0.1`: `GET /ping`, `POST /action`. Token on every request; closes itself when idle or when the backend goes away |
| `src/actions.js` | The fixed list of actions (open, search, tabs, find and click, find and fill, scroll, wait, YouTube) |
| `src/session.js` | Starts Chrome (own profile, private pipe, downloads denied, dialogs dismissed) and tracks tabs |
| `src/page_scripts.js` | Small functions that run inside the page to find buttons and boxes. They only read the page and mark elements |
| `src/classify.js` | Spots sign-in and payment pages |
| `src/urls.js` | Accepts only http(s) addresses; builds search addresses |

The worker never decides what is allowed (the Python safety layer does) and never sends page content to the language model.

```bash
npm install
npm test
```
