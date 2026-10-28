# browser/

Chrome automation worker (phase 4). It exposes a small fixed set of actions over `127.0.0.1:8766` and is started by the backend only when needed.

- Uses `puppeteer-core` pinned to 23.11.1 so it runs on Node 20 (Monterey). The `puppeteer` package is not used because it downloads Chromium.
- Launches the installed Google Chrome with a dedicated profile in `data/chrome-profile`, never the user's own profile.
- Only domains listed in `safety/policy.yaml` can be opened. Downloads are off.
- Page content is returned to the backend as untrusted data.

Phase 0 contains only `package.json`.
