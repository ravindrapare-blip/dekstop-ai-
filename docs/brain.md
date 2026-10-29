# The brain: Gemini first, the local model as the safety net

By default the assistant is **fully local**: Ollama chooses the action, whisper.cpp hears you, Piper speaks. You can turn on **Google Gemini** as the main brain. The local model then stays ready behind it and takes over automatically whenever Gemini can't help, and Gemini takes over again by itself when it recovers.

```
 your request ──► Brain ──► Gemini ───────────────► one tool call ──► (same checks as always) ──► safety layer ──► action
                    │          │ quota / rate limit / timeout / no network / server trouble / bad key
                    │          ▼
                    └──────► local Ollama model ──► one tool call ──► (same checks as always) ──► safety layer ──► action
```

**Gemini only ever proposes a tool call.** The answer goes through exactly the same validation, "find the real target" step, safety layer, confirmation and audit log as the local model's answer. A Gemini answer can't run anything by itself, can't waive a confirmation, and can't use a tool that doesn't exist. (`tests/test_brain_integration.py` proves this with Gemini proposing `javascript:` links, shell commands and closing Finder.)

## What leaves your Mac (only when `USE_GEMINI=true`)

| Sent to Google | Never sent |
|---|---|
| the text of each request (typed, or the words whisper.cpp heard) | your voice recordings (speech to text stays on the Mac) |
| the instructions and the list of tools (the prompt) | your reminders, tasks, notes and command history |
| the current date and time | web page contents, file names and contents |
| | your saved preferences, unless you set `brain.send_memory_to_gemini: true` |
| | the safety layer, the audit log, or anything about what the action did |

Check Google's current terms for the Gemini API: on some plans, what you send may be used to improve Google's products. If that matters to you, leave `USE_GEMINI=false` (the default). The API key is only read from the environment or `.env` (git ignores it), is sent in a request header (never in an address), and is removed from anything logged or shown. `GET /status` never includes it.

## Set it up

1. Get an API key from Google AI Studio.
2. Copy the example file and edit it:

```bash
cp .env.example .env
chmod 600 .env        # only you can read it
```

```
USE_GEMINI=true
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-2.0-flash     # check Google's current model names
USE_LOCAL_FALLBACK=true
```

3. Make sure the local fallback is ready: `ollama pull qwen2.5:3b` (or the model named in `LOCAL_MODEL_NAME`).
4. Restart the backend (`scripts/stop.sh`, then `scripts/start.sh`), then check:

```bash
.venv/bin/python scripts/status.py                      # the Gemini line should say "ready"
.venv/bin/python scripts/validate_all.py --only gemini  # makes one tiny real request
```

If a setting is wrong, the assistant still starts and says what it ignored (`data/logs/backend.log`, `scripts/status.py`).

## Settings

Real environment variables win over `.env`, which wins over `config.yaml` (`brain:` section), which wins over the defaults. Run `scripts/start.sh` from a terminal where they are exported, or put them in `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `USE_GEMINI` | `false` | Use Gemini as the primary brain |
| `GEMINI_API_KEY` | none | Your key. Only from the environment or `.env`, never `config.yaml` |
| `GEMINI_MODEL` | `gemini-2.0-flash` | The model name. If Google has retired or renamed it, the assistant says "Gemini doesn't know the model name" and uses the local model: change it |
| `USE_LOCAL_FALLBACK` | `true` | Switch to the local model when Gemini fails. If `false`, a Gemini failure is an error message instead (nothing runs on the local model) |
| `LOCAL_MODEL_NAME` | `ollama.model` in config.yaml | The Ollama model for the local path |
| `SAFE_MODE` | `false` | Every browser action asks you first |
| `STT_ENGINE` | `whisper` | `whisper` (whisper.cpp, local) or `macos` (macOS Dictation: see below) |
| `TTS_ENGINE` | `piper` | `piper` (local, default) or `say` (the Mac's voice). `google` is not built yet: it falls back to Piper with a warning |

`config.yaml` `brain:` also has: `timeout_seconds` (10), `cooldown_seconds` (300), `auth_cooldown_seconds` (1800), `send_memory_to_gemini` (false), `speak_switch_notice` (false).

## How the switching works

| Situation | What happens |
|---|---|
| Gemini answers | Used. Nothing to say |
| Quota / rate limit (429), a request that takes longer than `timeout_seconds`, no internet, Google server trouble (5xx) | The local model answers **this** request. You are told once: "Gemini's quota or rate limit was reached, so I switched to the local model. I'll try Gemini again in 5 minutes." |
| Rejected key (401/403, "API key not valid"), unknown model (404) | Same, but it waits 30 minutes before trying again, because waiting a minute won't fix those. The message says what to check |
| During the wait | Requests go straight to the local model: no wasted time, no repeated news, no calls to Google |
| The wait is over | The **next request** tries Gemini again. If it works: "Gemini is available again, so I'm using it." (once). If not, it keeps the local model and waits longer (5, 10, then 20 minutes; Google's own `Retry-After` is obeyed, between 5 seconds and an hour) |
| Gemini declines one request (safety block) or sends an unreadable answer | The local model answers that request only. No wait, no news |
| Gemini fails and the local model fails too | One error saying both: "cannot connect to Ollama ... (Gemini is also unavailable: ...)" |
| `USE_LOCAL_FALLBACK=false` and Gemini fails | An error saying so. Nothing is answered by the local model |
| `USE_GEMINI=true` but no key | The local model is used, with a one-time explanation |

There is **no background polling**: an idle assistant makes no network calls. Only one request at a time tests a recovering Gemini; others use the local model meanwhile.

The worst case for one request is the Gemini timeout (10 s) plus the local model's answer, which fits inside the capsule's 135 s limit. On a 2017 MacBook Air the local model is the slow part: `qwen2.5:3b` is the sensible choice.

## Where you see it

- **In the capsule:** the switch message is added under the answer, once ("... switched to the local model" / "Gemini is available again"). With `brain.speak_switch_notice: true` and voice output on, it is read aloud too (your "never speak replies" setting still wins).
- **Menu bar → Status:** a **Gemini** line (ready / local model answering, retrying in 4 min / off / no key) and which model is in use.
- **`scripts/status.py`**, **`GET /status`** (`brain` section: which engine answered last, the state, seconds until the next try, number of switches), and **`data/logs/backend.log`** (one line per switch, with the reason and wait, never your words or the key).
- **Overall health:** with Gemini usable, a dead local model is only "degraded". With both down, the assistant is "down". With `USE_LOCAL_FALLBACK=false`, Gemini becomes the core part.

## Voice: local by default

| | Default | Alternative |
|---|---|---|
| Speech in | whisper.cpp (`STT_ENGINE=whisper`): the mic button records, the Mac transcribes | `STT_ENGINE=macos`: use **macOS Dictation**. Turn it on in System Settings → Keyboard → Dictation, click in the capsule's text box and press the Dictation key (or Fn twice). The mic button then explains this instead of recording |
| Speech out | Piper (`TTS_ENGINE=piper`), with the Mac's voice as backup if Piper isn't set up | `TTS_ENGINE=say`: only the Mac's voice. `google`: not built yet |

Voice never needs Gemini or the internet.

## Safety is unchanged

Everything that was true before is still true with Gemini: unknown tools can't run, risky actions ask, high-risk actions need acknowledgment, the audit log is written first, dry run describes only. `SAFE_MODE=true` adds one more rule: every browser action asks first (it only ever raises the level).

## Limits

- Not tried against the live Gemini service (this was built and tested against a stand-in server, without internet access to Google's documentation). The request follows the public REST format (`generateContent`, key in the `x-goog-api-key` header, JSON output). If Google changes it, the symptom is "Gemini rejected the API key or access" or "doesn't know the model name": check `GEMINI_MODEL` and run `scripts/validate_all.py --only gemini`.
- A small model name list isn't built in: `GEMINI_MODEL` is whatever you set.
- If Gemini returns well-formed JSON for a tool that doesn't exist, that request gets the polite "could you rephrase" answer; it is not retried on the local model.
- The first local-model answer after a long idle can be slow while Ollama loads the model.
