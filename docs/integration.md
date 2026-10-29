# How it all fits together (phase 9)

Everything runs on this Mac. No cloud service is called anywhere; the only network traffic is between programs on `127.0.0.1`.

## The path of one request

```
 mic ──► capsule app ──► /voice/transcribe ──► whisper.cpp          (voice in; typing skips this)
              │
              └──────► /route ──► prompt + local model (Ollama) ──► one tool call, validated
                                      │ (memory adds a line of known preferences to the prompt)
                                      ▼
                          prepare: find the REAL target (app, file, tab, button, reminder, folder...)
                                      ▼
                          safety layer: allow / ask you / refuse / describe only (dry run)  ── audit log
                                      ▼
                          execute ─► apps & files: Hammerspoon, else AppleScript / `open`
                                     browser:      Node worker ─► Chrome (own profile)
                                     reminders, tasks, notes, preferences: SQLite  (+ macOS notifications)
                                     organizer:    preview ─► your approval ─► logged moves
                                      ▼
              capsule shows the result ◄── /voice/speak ◄── Piper (or the Mac voice)
```

The same pattern runs through every tool: **prompt + tool routing**, then **prepare → safety → execute**. Nothing runs unless the safety layer said yes to the exact thing about to happen. The pieces and their folders:

| Piece | Where | Started |
|---|---|---|
| Capsule UI (SwiftUI) | `app/` | by you, from the menu bar or `open app/build/Assistant.app` |
| Backend (FastAPI) | `backend/` | `scripts/start.sh`; the only thing that talks to the others |
| Local model | Ollama | by you (or the Ollama app) |
| Safety layer | `safety/` | in the backend |
| Apps and files | `automation/` (+ Hammerspoon) | in the backend |
| Browser | `browserctl/` + `browser/` (Node) | the Node helper starts on the first browser action and closes itself when idle |
| Reminders, tasks, notes, memory | `memory/` | in the backend; one small thread watches the clock |
| Organizer | `organizer/` | in the backend |
| Voice | `voice/` | engines run only while converting speech |

Only three things ever listen on the network, all on `127.0.0.1`: the backend (8765), the browser helper (8766) and Hammerspoon (8767). The last two require the shared token in `data/.token`.

## Start-up checks and status

`scripts/start.sh` starts the backend and waits until it really answers. On start the backend writes one line per part to `data/logs/backend.log` (and warns about anything not working). At any time:

```bash
.venv/bin/python scripts/status.py          # a traffic-light list; works with or without the backend running
```

The capsule's menu bar item has a **Status** line (refreshed each time you open the menu) and a submenu with one line per part.

| Light | Meaning |
|---|---|
| ✓ ok | works |
| ! warn | works through a fallback, or an optional part is missing (typing instead of talking, AppleScript instead of Hammerspoon, Chrome helper not installed) |
| – off | switched off in `config.yaml` |
| ✕ fail | broken; the line says why and how to fix it |

Overall: **ready** (all ok), **degraded** (something optional needs attention; everything else works) or **down**. Only the safety rules and the language model are *core*: if either fails the assistant is down, because nothing could run safely. Everything else failing only removes that feature.

What each part needs:

| Part | Needs | If missing |
|---|---|---|
| Language model | Ollama running, the model in `ollama.model` pulled (or Gemini working: see `docs/brain.md`) | down |
| Safety rules | `safety/policy.yaml` | down |
| Apps and files | macOS and the standard folders | feature fails |
| Hammerspoon | optional | AppleScript / `open` are used |
| Browser | Node 18+, `cd browser && npm install`, Google Chrome | browser tools answer with the fix; everything else works |
| Memory, reminders | a writable `data/` folder | those tools answer politely |
| Notifications | macOS `osascript` (allow notifications) | reminders are kept, nothing pops up |
| Voice in | whisper.cpp and a model | type instead |
| Voice out | Piper, else the Mac's `say` | replies are shown, not spoken |

## Errors, retries and timeouts

Every layer has its own time limit, so nothing waits forever, and nothing is retried unless trying again is safe and likely to help.

| Layer | Limit | Retry | What you see |
|---|---|---|---|
| Model (Ollama) | 120 s to answer (`ollama.timeout_seconds`), 3 s to connect | once, after a short pause, for "still starting" and server hiccups; **never after a timeout** | "Cannot connect to Ollama" / "timed out" with the fix |
| Model returned nonsense | n/a | once, with a corrective message (`router.max_retries`) | otherwise a polite "could you rephrase" |
| Browser helper | 20 s to start, 75 s per action; pages: 20 s to open, then a few seconds to settle | none (a half-done click must not repeat) | the step list plus a plain reason |
| AppleScript / Hammerspoon | 10 s / 4 s | Hammerspoon failure falls back to AppleScript | "I couldn't ..." |
| Voice | whisper 60 s, Piper 30 s | the next speech engine is tried | the capsule says why; typing still works |
| Notifications | 10 s | up to 3 tries, then marked as gone off | listed under `list_reminders` |
| SQLite | 5 s busy wait | none | "busy, try again" (or "damaged": `scripts/memory_check.py`) |
| Confirmations | 60 s | none | expire; nothing runs |
| Capsule to backend | 135 s | none | "taking too long" |

An unexpected bug in the backend returns a short JSON message ("The assistant hit an unexpected problem. Nothing else was changed.") and is logged with details; the next request works normally. Problems while starting up (broken `config.yaml`, broken policy, a non-local address) stop the backend with one clear line instead of a stack trace.

## Shutting down

`scripts/stop.sh` (or Ctrl-C) sends the backend a normal termination signal. It stops taking requests, waits up to 5 seconds for ones in flight, then stops the reminder watcher and the browser helper (which closes its Chrome window), then the model connection. Each step is attempted even if an earlier one fails. Pending confirmations simply disappear: nothing waiting for your approval ever runs later.

## Final validation

```bash
.venv/bin/python scripts/validate_all.py            # everything that can run
.venv/bin/python scripts/validate_all.py --deep --notify --live      # also: model picks a tool, a real notification, opens a folder
.venv/bin/python scripts/validate_all.py --only safety,reminders
```

| Check | What it proves | What it cannot |
|---|---|---|
| model | Ollama is up, the model is pulled; `--deep`: it chooses `open_app` for "open chrome" (and how fast) | quality on all requests: use `scripts/validate_model.py` |
| browser | Chrome starts with its own profile, loads a page served from this Mac, reads the title, follows a link, opens a tab | real websites |
| ui | on a Mac: the built app starts, and its window count changes when the shortcut is pressed twice | needs Accessibility permission for your terminal; otherwise reported as **skipped** with what to check by hand |
| voice | text to speech and speech to text both work (it speaks a sentence and hears it back); the Mac reports a microphone | microphone *permission* (macOS asks the app the first time) |
| reminders | save, list, go off exactly once at the right time, in a temporary database; `--notify` sends a real notification | that you notice it |
| safety | 20 sample actions are judged correctly; every tool has a rule; blocked capabilities cannot be chosen; dry run, single-use approvals, risk acknowledgment and fail-closed logging work | the model's behaviour |
| files | apps are found, a file is found by name, a tidy-up is previewed without moving anything; `--live` opens a folder | |
| backend | if running: `/health` and `/status` | |

A check that cannot run is **skipped and listed as not verified**; it is never reported as passing. Exit code 0 means nothing failed. The unit tests (`.venv/bin/python -m pytest`, `cd browser && npm test`, `cd app && ./build.sh test`) check the logic; this script checks the real machine.
