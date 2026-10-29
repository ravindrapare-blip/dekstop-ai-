# Local AI Desktop Assistant

A lightweight, local-first desktop assistant for a **2017 MacBook Air running macOS Monterey**.

- Local LLM through **Ollama**. No cloud APIs for core logic.
- Speech in with **whisper.cpp**, speech out with **Piper**.
- Chrome automation with **Puppeteer**, macOS automation with **Hammerspoon**.
- Floating UI in **SwiftUI**. No Electron.
- **SQLite** for reminders and memory.
- A safety gate in front of every action.

**Status: phase 9.** The backend asks a local Ollama model to choose one tool, validates it, and passes it through a safety layer (risk levels, confirmation, dry-run, audit log). A native floating capsule UI (`app/`) shows it, and you can speak to it and hear replies, all locally (`docs/voice.md`). It can open, focus and quit apps and open and search files (`docs/actions.md`). It can drive Chrome: open sites, search, work with tabs, click, type, scroll and play YouTube videos, stopping at sign-in pages (`docs/browser.md`). It keeps reminders (with macOS notifications), tasks, notes and a few preferences in one private local database (`docs/memory.md`). It can sort the loose files on your Desktop or in Downloads into folders, but only after showing a preview and getting your approval (`docs/organizer.md`). Everything is connected and checked: `scripts/status.py` shows what works, `scripts/validate_all.py` proves it on your Mac (`docs/integration.md`). See `docs/phases.md`, `docs/tools.md`, `safety/README.md` and `app/README.md`.

## Layout

```
.
├── config.yaml        all settings
├── pyproject.toml     Python dependencies
├── app/               SwiftUI floating capsule UI (macOS, Swift package)
├── backend/           orchestrator and API
├── browser/           puppeteer-core worker (Node)
├── browserctl/        Python side of browser control
├── memory/            reminders, tasks, notes, preferences (SQLite)
├── organizer/         safe desktop organization (preview, confirm, log, undo)
├── voice/             speech engines: whisper.cpp, Piper, macOS voice
├── automation/        opening apps and files: finder, Hammerspoon client and Lua
├── safety/            risk tiers, policy, approvals, audit
├── data/              SQLite, schema, logs (git-ignored)
├── docs/              architecture, install guide, phases
├── scripts/           install.sh, start.sh, stop.sh
└── reference/         upstream snapshots, guidance only
```

## Quick start

```bash
./scripts/install.sh
./scripts/start.sh
curl http://127.0.0.1:8765/health
./scripts/stop.sh
```

## Documents

- `docs/architecture.md` : components, request flow, safety model, resource budget, compatibility findings
- `docs/tools.md` : tool registry, JSON format, validation
- `docs/actions.md` : apps and files: how names are matched, where it looks, Hammerspoon setup
- `docs/voice.md` : voice setup, microphone permission, privacy
- `docs/install.md` : setup for Monterey
- `docs/phases.md` : roadmap

## Compatibility warning

Several upstream projects in `reference/` no longer support Monterey in their newest versions (Ollama needs macOS 14, Puppeteer `main` needs Node 22, Hammerspoon `main` targets macOS 13). The design pins older compatible versions. Details in `docs/architecture.md` section 7. Verify them on your Mac with `scripts/health_check.py` (see `docs/install.md`).

## Reference code

`reference/` holds snapshots of Open Interpreter, Ollama, Hammerspoon, Puppeteer, whisper.cpp and Piper, plus Coqui TTS, which is not used. They are for reading only. Nothing in the product imports from them, and their licences apply to them.
