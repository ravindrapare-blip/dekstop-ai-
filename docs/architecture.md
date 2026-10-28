# Architecture

Status: **Phases 0-5 done** (runtime, tool calling, safety layer, capsule UI, voice, apps and files). See `docs/tools.md`, `safety/README.md`, `app/README.md`, `docs/voice.md` and `docs/actions.md`. Later components are still design only.

## 1. Goals and constraints

A local-first desktop assistant for a **2017 MacBook Air (Intel, dual-core, no GPU, macOS 12 Monterey)**.

- Core logic never calls a cloud API. Everything runs on the machine.
- Lightweight and stable beats feature-rich. Idle cost should be close to zero.
- Small independent processes, not one big app.
- Anything dangerous or unsupported needs explicit user approval, or is not offered at all.

## 2. Components

| Component | Tech | Process | Responsibility |
|---|---|---|---|
| `app/` | SwiftUI (AppKit window shell) | long-running, tiny | Floating UI: text input, mic button, status, approval prompts |
| `backend/` | Python 3.10+, FastAPI | long-running | Orchestrator: request loop, LLM calls, tool dispatch, SQLite |
| `safety/` | Python package, YAML policy | in-process in backend | Classifies every action, gates approval, writes the audit log |
| `voice/` | Python wrappers | child processes, on demand | Runs `whisper.cpp` (STT) and `piper` or `say` (TTS). The app records and plays; this only converts |
| `browser/` | Node.js, `puppeteer-core` | worker, started on demand | Drives the user's existing Chrome through a fixed set of actions |
| `automation/` | Python + Hammerspoon (Lua) | in the backend; Hammerspoon itself | Finds apps and files, runs authorized actions. Hammerspoon runs four named actions |
| `data/` | SQLite, JSON lines | files | Reminders, tasks, notes, memory (SQLite); audit log (`audit.jsonl`) |
| Ollama | external binary | long-running, `127.0.0.1:11434` | Local LLM inference |

```
                      ┌────────────────────────┐
   mic / keyboard ──▶ │  app/ (SwiftUI panel)  │ ◀── approval prompts
                      └──────────┬─────────────┘
                                 │ HTTP, 127.0.0.1 + token
                      ┌──────────▼─────────────┐        ┌───────────────┐
                      │  backend/ (FastAPI)    │───────▶│ Ollama :11434 │
                      │  orchestrator          │        └───────────────┘
                      │  ┌──────────────────┐  │
                      │  │ safety/ gate     │  │        ┌───────────────┐
                      │  └──────────────────┘  │───────▶│ voice/        │
                      │  SQLite (data/)        │        │ whisper, piper│
                      └───┬────────────────┬───┘        └───────────────┘
                          │ HTTP+token     │ HTTP+token
                  ┌───────▼──────┐   ┌─────▼────────────┐
                  │ browser/     │   │ automation/      │
                  │ puppeteer    │   │ Hammerspoon      │
                  │ → Chrome     │   │ → macOS          │
                  └──────────────┘   └──────────────────┘
```

Rules that keep this modular:

- The **backend is the only component that talks to the others.** The UI, browser worker and Hammerspoon never call each other.
- Each box can be stopped or replaced without touching the rest. If the browser worker is down, only browser tools fail.
- Components share nothing but small JSON messages. There are no shared libraries between Python and Node.

## 3. Request flow

1. User types or speaks. For voice, the app records a WAV, the backend runs `whisper.cpp` on it, and the text continues as if it had been typed.
2. Backend sends the text, a short system prompt, recent memory and the **tool list** to Ollama.
3. The model replies with text or a **structured action proposal** (JSON: tool name + arguments).
4. The proposal goes to `safety/`: schema check, tool registry check, risk classification.
5. Depending on the risk tier, the action runs, runs and is logged, waits for approval in the UI, or is refused.
6. The result is shown. For a spoken request the backend turns it into speech with `piper` (or the Mac voice) and the app plays it.

The model never executes anything itself. It only proposes actions from a fixed registry.

## 4. Safety model

Detail lives in `safety/README.md` and `safety/policy.yaml`. Implemented in phase 2. The principles:

- **Deny by default.** An action that is not in the registry does not exist.
- **Four risk levels:** low (runs, logged), medium (asks first), high (blocked by default, needs acknowledged approval), critical (never).
- **No free-form execution.** There is no "run this shell command" or "evaluate this Lua" tool. Open Interpreter is reference material for agent-loop ideas only; its arbitrary code execution is deliberately not adopted.
- **Untrusted content stays data.** Web pages and file contents are not instructions. Text from them can never raise a tier or skip an approval.
- **Approvals show the exact action** and its arguments, time out to "deny", and are never cached across actions.
- **Local services are local.** Every service binds to `127.0.0.1` and requires a shared token from `data/.token`. Note that `hs.httpserver` listens on *all* interfaces by default, so the Hammerspoon side must call `setInterface("localhost")`.
- **Audit log.** Every decision is written to `data/logs/audit.jsonl` before it is returned. If it can't be written, nothing is authorized.
- **Kill switch.** A global hotkey stops the backend's current task and closes the browser worker.

## 5. Data (SQLite, `data/assistant.db`)

Tables for reminders, tasks, notes, preferences and command history arrive in phase 7. A draft is in `data/schema.sql`. Memory is plain facts and preferences with timestamps. Retrieval starts as simple keyword search. No embedding model is run, to save RAM.

## 6. Resource budget (8 GB RAM, 2 cores)

These are targets to **measure in phase 1**, not measured results.

| Item | Target |
|---|---|
| Backend idle | under 100 MB |
| SwiftUI panel | under 80 MB |
| Ollama model | 1-3B parameters, 4-bit quantised, about 1-2 GB while loaded |
| `keep_alive` | 5 minutes, then the model unloads |
| Whisper | `tiny.en` or `base.en` (about 273 MB / 388 MB per its README), run only while listening |
| Piper | a "low" or "medium" voice, run only while speaking |
| Browser worker | not running unless a browser tool is used |

One model is loaded at a time (`OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`). Speech and generation do not run concurrently. Chrome itself is the biggest memory user, so browser tasks should close tabs they open.

## 7. Compatibility findings (checked against the reference snapshots)

The reference repos are current `main` branches. Several no longer target Monterey. **Do not build or install their latest versions blindly.**

| Item | Finding | Decision |
|---|---|---|
| Ollama | Its `docs/macos.mdx` lists macOS 14 (Sonoma) or newer. | Pin an older Ollama release that still supports macOS 12, or build from source. Verify before phase 1. This is the biggest risk. |
| Puppeteer | `puppeteer-core` on `main` needs Node 22.12 or newer. | Use `puppeteer-core@23.11.1` (engines `node >=18`) on **Node 20 LTS**. Use `puppeteer-core`, not `puppeteer`, so no Chromium is downloaded and the user's Chrome is reused. |
| Hammerspoon | `main` sets `MACOSX_DEPLOYMENT_TARGET = 13.0`. | Install a released build that supports Monterey. Do not build `main`. Check the release notes. |
| whisper.cpp | CPU build works on Intel. Metal and Core ML are Apple Silicon focused. | Build with the default CPU backend. Use quantised models. |
| Piper | The repo says development moved to `OHF-Voice/piper1-gpl`. | Use a prebuilt macOS x64 release if one exists, otherwise build the archived version. Verify. |
| SwiftUI | `MenuBarExtra` needs macOS 13. | Use an AppKit `NSPanel` / `NSStatusItem` shell hosting SwiftUI views. The last Xcode that runs on Monterey is 14.2. |
| Python | macOS 12 ships no usable Python 3. | Install 3.10+ through Homebrew or python.org. |

## 8. Repository layout

```
.
├── README.md
├── config.yaml            # all tunables, no secrets
├── pyproject.toml         # Python backend dependencies
├── app/                   # SwiftUI floating capsule UI
├── backend/               # orchestrator, config, API
├── browser/               # puppeteer-core worker
├── voice/                 # whisper.cpp and piper wrappers
├── automation/            # Hammerspoon config and action list
├── safety/                # risk tiers, policy, approval, audit
├── data/                  # SQLite file, schema, logs (git-ignored)
├── docs/                  # architecture, install guide, phases
├── scripts/               # install.sh, start.sh, stop.sh
└── reference/             # upstream snapshots, guidance only, not shipped
```

`reference/` holds read-only snapshots of the upstream projects, including `coqui-tts`, which is not part of the design. Nothing in the product imports from it.

## 9. Out of scope for phase 0

No LLM calls, no audio, no browser or macOS actions, no UI code, no database creation. Those arrive in the phases listed in `docs/phases.md`.
