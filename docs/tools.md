# Tool system

The model never acts directly. It picks **one tool** from a fixed registry and returns JSON. The backend validates it. Executing the tool is a separate step handled by later phases.

## Output format

```json
{"tool": "open_app", "args": {"app_name": "Google Chrome"}, "requires_confirmation": false}
```

`POST /route` with `{"text": "open chrome"}` returns that object plus `fallback`, `note` and `attempts`.

## Flow

```
user text -> prompt.py (rules + compact tool list + examples)
          -> Ollama (format=json, temperature 0)
          -> toolcall.py validates (registered tool, exact args, types)
          -> valid:   return the call
             invalid: one corrective retry, then a harmless "reply" fallback
```

## What gets rejected

Anything that is not a JSON object, an unknown tool, a missing or extra argument, a wrong type, an empty or over-long string. Code fences around the JSON are tolerated. Prose is never turned into an action: if the output can't be validated twice, the result is the `reply` tool asking the user to rephrase, with `fallback: true`.

## `requires_confirmation`

Each tool has a default in its metadata. The model can set the flag to `true` but can never set it to `false` for a tool that requires it. A non-boolean value is treated as `true`. Enforcing the confirmation flow is phase 2.

## Categories and tools

| Category | Tools |
|---|---|
| app | `open_app`, `focus_app`, `close_app`* |
| file | `open_file`, `open_folder`, `search_files`, `get_recent_files` |
| browser | `open_url`, `search_chrome`, `new_tab`, `switch_tab`, `close_tab`* |
| reminder | `create_reminder`, `list_reminders`, `delete_reminder`* |
| note | `save_note`, `list_notes` |
| system | `reply`, `get_time`, `get_battery`, `set_volume`* |

\* confirmation required by default. There are deliberately no tools to delete, rename or move files, run shell commands, or change security settings. `reply` is how the model answers, asks a question or declines.

## Adding a tool

Add one `ToolSpec` to `backend/tool_defs.py`, with its parameters and a one-line description. It is then listed in the prompt, returned by `GET /tools` and accepted by the validator. Keep descriptions short: the whole prompt is about 530 tokens, and the context window is 2048.

## Model choice

`ollama.model` in `config.yaml` is the only setting that decides the model. `format: "json"` is used instead of a JSON schema because it works on every Ollama release, including older ones that macOS 12 may require. Run `scripts/validate_model.py` to compare models on your Mac.

## Scripts

```bash
.venv/bin/python scripts/health_check.py     # Ollama installed, running, model pulled, backend up
.venv/bin/python scripts/validate_model.py   # 12 sample requests: accuracy, JSON validity, speed
.venv/bin/python scripts/validate_model.py --model llama3.1:8b
```
