# automation/

Doing things on the Mac. Overview and setup: `docs/actions.md`.

| File | Job |
|---|---|
| `apps.py` | Lists installed apps from the standard folders and matches names |
| `files.py` | Finds files and folders in a few safe folders, with limits |
| `fuzzy.py` | Forgiving name matching (typos, aliases, type words) and the "one clear winner or ask" rule |
| `service.py` | `prepare()` finds the real target, `execute()` does the action, choices for several matches |
| `controller.py` | Opens, focuses and quits apps: Hammerspoon first, then AppleScript or `open` |
| `hammerspoon.py` | Client for the Hammerspoon listener (token, localhost only) |
| `applescript.py` | osascript with the app passed as an argument |
| `token.py` | The shared secret in `data/.token` |
| `hammerspoon/init.lua` | The Hammerspoon side: four named actions, token required, localhost only |

This package never decides what is allowed. `backend/pipeline.py` runs: find the target, ask the safety layer, and only then call `execute()`.
