# Install guide (macOS 12 Monterey, Intel)

This is the lightweight path. Phase 0 only needs the backend skeleton. The other tools are listed so you can prepare them, but the assistant does not use them yet.

Read `docs/architecture.md` section 7 first. Several upstream projects have dropped Monterey in their newest versions, so pick versions as described below.

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Command Line Tools | any | `xcode-select --install` |
| Homebrew | current | Works on Monterey, but some formulae may build from source and take a while |
| Python | 3.10 or newer | `brew install python@3.11` |
| Node.js | **20 LTS** | `brew install node@20`. Not Node 22, which needs a newer macOS |
| Google Chrome | any recent | Used as-is by `puppeteer-core`. No Chromium download |
| Ollama | a release that supports macOS 12 | The newest docs say macOS 14+. Check older releases at https://github.com/ollama/ollama/releases |
| Hammerspoon | a release that supports macOS 12 | https://github.com/Hammerspoon/hammerspoon/releases |
| whisper.cpp | build from source | Needs `cmake`: `brew install cmake` |
| Piper | prebuilt macOS x64 release if available | Archived upstream, so verify |
| Xcode | 14.2 | Only needed from phase 5, for the SwiftUI app |

## 2. Set up the project

```bash
git clone https://github.com/ravindrapare-blip/dekstop-ai-.git
cd dekstop-ai-
./scripts/install.sh
```

`install.sh` checks your machine, creates `.venv`, installs the Python dependencies, installs the browser worker's Node dependency, and creates the `data/` folders. It does not download models and does not change system settings. Run `./scripts/install.sh --check` to only report what is missing.

## 3. Start and stop

```bash
./scripts/start.sh
curl http://127.0.0.1:8765/health
./scripts/stop.sh
```

Expected reply: `{"status":"ok","phase":0}`.

`start.sh` waits for the health check and reports failure (for example, port already in use) instead of pretending it started.

Run the tests with `.venv/bin/pip install -e ".[dev]"` once, then `.venv/bin/pytest`.

## 4. Ollama and the model (phase 1)

With Ollama installed and running (`ollama serve` or the app), pull the default model, then check everything:

```bash
ollama pull qwen2.5:3b
.venv/bin/python scripts/health_check.py
.venv/bin/python scripts/validate_model.py
```

`health_check.py` tells you exactly what is missing and how to fix it. `validate_model.py` reports accuracy and tokens per second on your Mac. To try another model, change `ollama.model` in `config.yaml` or pass `--model`.

## 5. Voice (phase 4)

Build whisper.cpp, download a model and install Piper as described in `docs/voice.md`, then check:

```bash
.venv/bin/python scripts/voice_check.py
```

It reports what is missing and does a real round trip (speaks a sentence and transcribes it back), printing how long each took. Voice is optional: without it the capsule falls back to typing.

## 6. Troubleshooting

- **`python3: command not found`**: install Python with Homebrew and reopen the terminal.
- **Port 8765 in use**: change `backend.port` in `config.yaml`.
- **Mac gets slow**: close Chrome, lower the model size, and keep `ollama.keep_alive` short in `config.yaml`.
- **Logs**: `data/logs/backend.log`.
- **`/route` returns 503**: Ollama is not running or the model is not pulled. Run `scripts/health_check.py`.
- **First request is very slow**: the model is loading from disk. Later requests are faster until `keep_alive` expires.
