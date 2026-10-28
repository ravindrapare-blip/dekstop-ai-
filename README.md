# Local AI Desktop Assistant

A lightweight, local-first desktop assistant for a **2017 MacBook Air running macOS Monterey**.

- Local LLM through **Ollama**. No cloud APIs for core logic.
- Speech in with **whisper.cpp**, speech out with **Piper**.
- Chrome automation with **Puppeteer**, macOS automation with **Hammerspoon**.
- Floating UI in **SwiftUI**. No Electron.
- **SQLite** for reminders and memory.
- A safety gate in front of every action.

**Status: phase 2.** The backend asks a local Ollama model to choose one tool, validates it, and passes it through a safety layer (risk levels, confirmation, dry-run, audit log). Nothing is executed yet. See `docs/phases.md`, `docs/tools.md` and `safety/README.md`.

## Layout

```
.
├── config.yaml        all settings
├── pyproject.toml     Python dependencies
├── app/               SwiftUI floating UI
├── backend/           orchestrator and API
├── browser/           puppeteer-core worker
├── voice/             whisper.cpp and piper wrappers
├── automation/        Hammerspoon config and client
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
- `docs/install.md` : setup for Monterey
- `docs/phases.md` : roadmap

## Compatibility warning

Several upstream projects in `reference/` no longer support Monterey in their newest versions (Ollama needs macOS 14, Puppeteer `main` needs Node 22, Hammerspoon `main` targets macOS 13). The design pins older compatible versions. Details in `docs/architecture.md` section 7. Verify them on your Mac with `scripts/health_check.py` (see `docs/install.md`).

## Reference code

`reference/` holds snapshots of Open Interpreter, Ollama, Hammerspoon, Puppeteer, whisper.cpp and Piper, plus Coqui TTS, which is not used. They are for reading only. Nothing in the product imports from them, and their licences apply to them.
