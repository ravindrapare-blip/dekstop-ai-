# memory/

Everything the assistant remembers, in one local SQLite file. Overview, privacy and limits: `docs/memory.md`.

| File | Job |
|---|---|
| `schema.sql` | The five tables: reminders, tasks, notes, preferences, command_history |
| `db.py` | Opens the file (private permissions, versioned schema, friendly errors) |
| `store.py` | All reads and writes, with size limits and pruning |
| `service.py` | `prepare()` checks the request and finds the real item; `execute()` does it and writes the message |
| `timeparse.py` | ISO 8601 and a few natural phrases ("in 10 minutes", "friday at 3pm") |
| `scheduler.py` | One thread that fires reminders when they are due |
| `notify.py` | macOS notifications through `osascript` |
| `factory.py` | Builds the service from `config.yaml` |

This package never decides what is allowed (`backend/pipeline.py` asks the safety layer first) and never uses the network.
