# Browser control (phase 6)

The assistant can drive Google Chrome: open sites, search, work with tabs, click buttons and links, type into text boxes, scroll, wait for a page and play a YouTube video.

```
capsule app ─► backend ─► safety layer ─► browserctl/ (Python) ─► browser/ (Node worker) ─► Chrome
```

- **`browser/`** is a small Node program (`puppeteer-core`, no Chromium download). It listens on `127.0.0.1:8766`, needs the shared token (`data/.token`) on every request, and only runs a fixed list of named actions. It starts when the first browser action needs it and closes itself (and Chrome) after `browser.idle_minutes` without one.
- **`browserctl/`** finds the real target (which tab, which button, which box), starts the worker, and turns answers into short messages and step lists.
- The **safety layer** judges that real target before anything runs, exactly as for apps and files (`prepare` → `evaluate` → `execute` → audit).

## Set up

```bash
# Node 18 or newer (Node 20 LTS is the target on macOS 12), then once:
cd browser && npm install
cd .. && .venv/bin/python scripts/browser_check.py            # checks Node, packages, Chrome
.venv/bin/python scripts/browser_check.py --launch            # also opens Chrome and a page, then closes it
```

`scripts/install.sh` already runs `npm install`. Settings are in the `browser:` section of `config.yaml` (`enabled`, `port`, `node`, `chrome_path`, `profile_dir`, `idle_minutes`).

**Chrome and macOS 12.** Chrome 150 is the last release that supports macOS 12. Keep it updated to that, and know that it will no longer get security fixes after that. The assistant works with whatever Chrome is installed; it does not update it.

**Its own Chrome profile.** The assistant uses `data/chrome-profile`, never your everyday profile. The first time a site asks you to sign in, sign in yourself in that window; the session is kept for next time. Chrome is started with a private pipe to the worker, not a debugging port, so no other program can attach to it.

## What it does

| You say | Tool |
|---|---|
| "Open Chrome" | `open_chrome` |
| "Open Chrome and search for AI tools" | `search_chrome` (starts Chrome if needed) |
| "Open my Gmail", "open example.com" | `open_url` (well-known names such as gmail, calendar, youtube, drive, maps, github are in `browserctl/sites.py`) |
| "Open a new tab and go to Calendar" | `new_tab` with an address |
| "Go to the tab with YouTube", "switch to tab 2" | `switch_tab` (number, "second", "last", part of the title or the site) |
| "Close this tab" | `close_tab` (asks first) |
| "Click Sign up" | `click_text` |
| "Type hello into the search box" | `fill_field` |
| "Scroll down", "go to the bottom" | `scroll_page` |
| "Wait for the page" | `wait_for_page_load` |
| "What page is this?" | `get_page_title`, `get_current_url` (the address is shown without its `?query` part) |
| "Open YouTube and play lofi music" / "play that video" | `play_youtube_video` |

Every action reports the steps it took (for example `Started Chrome`, `Opening google.com`, `Page loaded`). It waits for the page to say it has finished loading, with a ceiling (a few seconds after navigation, 10 s for `wait_for_page_load`); a slow page is reported rather than waited on forever.

## Safety rules

| Situation | What happens |
|---|---|
| Address is not http or https (`javascript:`, `file:`, `data:`, `chrome:` ...) | blocked, never reaches Chrome |
| Address looks like a sign-in or payment page (`/login`, `/checkout`, ...) | asks first |
| A page needs you to sign in (visible password box, or a known sign-in address) | the assistant **stops** and says so (`needs_login`). Sign in yourself in the Chrome window, then ask it to continue |
| Typing into a password, card number or one-time-code box | **blocked**, always. Type those yourself |
| Typing or clicking on a sign-in page | asks first |
| Typing or clicking on a payment page, or a button inside a card form | asks, and you must acknowledge the risk |
| Button names like Delete, Remove, Pay, Buy, Purchase, Place order, Donate, Transfer | asks, with risk acknowledgment |
| Button names like Send, Submit, Post, Confirm, Accept, Allow, Subscribe, Download, Sign in/out | asks |
| A button name that mixes alphabets (e.g. a Cyrillic "е" inside "Delete") | asks |
| Several buttons or tabs match | the capsule asks which one |
| Closing a tab | asks |
| Downloads, pop-up dialogs | refused / dismissed |

The word lists are `click_high_words` and `click_confirm_words` in `safety/policy.yaml` (English only: add the words for your language there).

How the judgement works: `click_text` and `fill_field` are first resolved against the **actual page**. The backend asks the worker what the words match, then gives the safety layer the label of the real button (`target`), the kind of box (`field_kind`) and whether the page is a sign-in or payment page (`page_hint`). The model cannot supply these; they are replaced from the page. At click time the worker checks the label again (`expect`) and refuses if the page has changed since it was judged.

Text typed into pages is never written to the audit log (only its length) and never repeated in messages. Page text such as titles and button names is shown to you but is **never given back to the language model**, so a web page cannot give the assistant instructions.

## Limits

- Buttons and boxes are found in the main page only: not inside iframes or shadow DOM. Sites that draw their own interface (canvas apps, some Google apps) may not work with `click_text`.
- Matching uses the visible text, aria labels and similar; it will not find an icon with no name.
- YouTube's page layout changes now and then. `play_youtube_video` picks the first result that is not an ad or a Short, and falls back to pressing play. Cookie-consent pages are left for you to answer.
- English-only risky-word lists (see above).
- One Chrome window and profile are controlled; the assistant does not touch tabs in your own Chrome.

## Troubleshooting

| Message | Fix |
|---|---|
| "The browser helper isn't installed yet" | `cd browser && npm install` |
| "Node.js wasn't found" | install Node 18+ (`brew install node@20`) or set `browser.node` |
| "I can't find Google Chrome" | set `browser.chrome_path` |
| "The browser helper stopped right after starting" | port 8766 may be in use; see `data/logs/browser.log` |
| "I couldn't start Chrome" | the assistant's own Chrome window may already be open: close it |
| Chrome does not start in a container/CI as root | the worker accepts `--no-sandbox` (tests use it); do not use it on your Mac |

## Tests

```bash
cd browser && npm test                       # worker: unit tests, plus real-Chromium tests if a Chrome is found
.venv/bin/python -m pytest tests/test_browser.py tests/test_browser_e2e.py
```

`ASSISTANT_CHROME_PATH` points the real-browser tests at a specific Chrome. The real-browser tests use a local test website, so they need no internet.
