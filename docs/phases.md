# Phases

| Phase | Scope | Exit check |
|---|---|---|
| 0 | Structure, config, docs, startup scripts (this phase) | `scripts/start.sh` brings up `/health` |
| 1 | Verify Monterey-compatible Ollama, Node, Hammerspoon, Piper builds. Chat loop: text in, model reply out. Measure RAM and latency of candidate models | A written benchmark in `docs/` and a chosen default model |
| 2 | `safety/` gate, tool registry, audit log, SQLite schema applied, reminders | Unit tests: forbidden and unknown actions are refused |
| 3 | Voice: whisper.cpp speech-to-text, piper text-to-speech, push-to-talk | Round trip works with the model unloaded between uses |
| 4 | Hammerspoon actions (small allowlist), then Puppeteer actions (domain allowlist) | Every action goes through the gate and the audit log |
| 5 | SwiftUI floating panel, approval prompts, kill switch hotkey | Approval dialog shows exact action and times out to deny |
| 6 | Hardening: crash recovery, log rotation, packaging as a login item | Runs for a week without manual restarts |

Each phase is shipped on its own. Later phases never change the interfaces defined in `docs/architecture.md` without updating that document first.
