# organizer/

Safe desktop organization: a preview first, a question before anything moves, nothing overwritten, every move logged. Overview, rules and undo: `docs/organizer.md`.

| File | Job |
|---|---|
| `categories.py` | Which folder a file belongs in (by name), what counts as hidden/system/unfinished, "only PDFs" words |
| `planner.py` | Builds the plan (the preview) from one folder's top level. Read-only. Handles name clashes |
| `mover.py` | Carries out an approved plan: re-checks every file, never overwrites, logs before each move |
| `movelog.py` | `data/logs/moves.jsonl`, one line per folder created and per move |
| `undo.py` | Puts a batch back using that log (`scripts/organize_undo.py`) |
| `service.py` | `prepare()` makes the plan and its preview; `execute()` runs it; trusted rules from `config.yaml` |
| `factory.py` | Builds the service from `config.yaml` |

This package never decides what is allowed. `backend/pipeline.py` asks the safety layer first and shows the preview as the question.
