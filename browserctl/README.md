# browserctl/

The Python side of browser control. Setup and safety rules: `docs/browser.md`.

| File | Job |
|---|---|
| `service.py` | `prepare()` finds the real tab, button or box (and asks "which one?"), `execute()` calls the worker and writes the message |
| `client.py` | HTTP client for the worker: localhost only, shared token |
| `launcher.py` | Starts the worker on first use, explains what is missing, stops it |
| `sites.py` | Names people say for sites ("my gmail") and address checking |
| `factory.py` | Builds the service from `config.yaml` |

This package never decides what is allowed. `backend/pipeline.py` asks the safety layer first, and `backend/actions.py` sends each tool to this package or to `automation/`.
