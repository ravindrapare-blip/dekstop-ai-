# Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Project structure, config, docs, startup scripts | done |
| 1 | Ollama runtime, tool registry, JSON tool calling, startup checks, validation scripts | done |
| 2 | Safety layer: guardrail manager, risk levels, confirmation flow, dry-run | |
| 3 | Floating capsule UI (SwiftUI) | |
| 4 | Local voice input and output (whisper.cpp, Piper) | |
| 5 | macOS app and file control (Hammerspoon, AppleScript) | |
| 6 | Browser automation (Puppeteer) | |
| 7 | Reminders, tasks, notes, local memory (SQLite) | |
| 8 | Safe desktop organization | |
| 9 | Full integration and final validation script | |
| 10 | Polish, performance tuning, lightweight mode, safe mode | |

Each phase ends with its own tests and a review pass before the next starts. Interfaces defined in `docs/architecture.md` change only after that document is updated.
