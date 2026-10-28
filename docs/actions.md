# Apps and files

What the assistant can do on the Mac in phase 5: open, focus and quit apps; open files and folders; search files; list recent files.

| Tool | What it does |
|---|---|
| `open_app` | Starts an app, or brings it to the front if it is already running |
| `focus_app` | Brings a running app to the front (does not start it) |
| `close_app` | Asks an app to quit, like Cmd+Q (asks you first) |
| `open_file` | Opens a file in its usual app |
| `open_folder` | Opens a folder in Finder |
| `search_files` | Lists matching files in your usual folders |
| `get_recent_files` | Lists the most recently changed files in Desktop, Documents and Downloads |

## The order things happen in

```
what the model said  ->  find the real app or file  ->  safety layer judges THAT  ->  do it  ->  log it
   "Open VS Code"         Visual Studio Code.app          allow / ask / refuse        Hammerspoon     audit.jsonl
```

1. **Find** is read-only. "VS Code" becomes `Visual Studio Code`, "the PDF called budget" becomes `/Users/you/Documents/Budget 2024.pdf`.
2. The safety layer then judges the **real** target. So `close_app "fin"` is judged as closing **Finder** (needs acknowledged approval), and a name that turns out to be in `~/.ssh` is stopped, however innocent the words were.
3. Only a call the safety layer authorized is executed, in one place (`backend/pipeline.py`), and the result is logged. A test makes sure nothing runs for a question, a refusal, a dry run, or a "not found".

## Matching

- App names are matched forgivingly: `chrome`, `Google Chrome please`, `chorme`, `VS Code`, `vscode`, `settings` (Monterey calls it System Preferences). Leading words like "open" and "switch to" are ignored.
- File names ignore case, accents and punctuation, and understand type words: "the PDF called budget", "budget.pdf" and "budget pdf" all mean a PDF named budget.
- An exact path (`/Users/you/Documents/plan.txt`, `~/Documents/plan.txt`, `Documents/plan.txt`) is opened as it is. Nothing is guessed when you give a full path; if it does not exist you are told so.
- Well-known folders are understood directly: Desktop, Documents, Downloads, Pictures, Movies, Music, Home, Applications.
- **One clear best match** is used. **Several close matches** show a short list (at most five) and ask you to pick one: "I found 2 files that could match “report.pdf”. Which one?" with each location shown. Your pick is judged by the safety layer like any other request. Choices expire after two minutes.

## Where it looks (and where it never does)

Apps: `/Applications`, `/Applications/Utilities`, `/System/Applications` (and its Utilities), `~/Applications`, plus Finder.

Files, in two steps. The second only happens if the first found nothing:

1. `~/Desktop`, `~/Documents`, `~/Downloads`, up to three folders deep
2. `~/Pictures`, `~/Movies`, `~/Music`, and the top level of your home folder (listed, never entered)

Safeguards, all tested:

- Only folders inside your home folder can be searched. Anything else in `config.yaml` (`/`, `/etc`, ...) is ignored.
- Hidden files and folders, symbolic links, app and library packages (`.app`, `.photoslibrary`, ...) and noisy folders (`Library`, `node_modules`, `venv`, ...) are skipped.
- A search stops after 20,000 items or 2 seconds, and says so ("I stopped early to keep things fast").

Change the folders under `files:` in `config.yaml`.

## Hammerspoon

Hammerspoon starts, focuses and quits apps. Without it the assistant still works: it falls back to `open -a` and AppleScript.

```bash
scripts/setup_hammerspoon.sh        # adds a short block to ~/.hammerspoon/init.lua (your own lines are untouched)
# then: Hammerspoon menu bar icon > Reload Config
.venv/bin/python scripts/automation_check.py
```

- Install a Hammerspoon release that supports macOS 12 (see `docs/install.md`).
- macOS may ask Hammerspoon for **Accessibility** access. Allow it.
- The Lua side is a small server that listens on localhost only. Every request needs the secret in `data/.token` (created automatically, readable only by you). It accepts four named actions (`ping`, `open_app`, `focus_app`, `quit_app`) and never runs received text as code. The safety layer has already decided before anything reaches it.
- Keyboard shortcuts to open apps: copy `automation/hammerspoon/hotkeys.example.lua` to `hotkeys.lua` in the same folder and edit it.
- Remove it all again with `scripts/setup_hammerspoon.sh --remove`.

AppleScript is used only as the fallback, and the app is handed to it as an argument, never pasted into the script.

## Things to know

- Quitting an app is a polite request. An app with unsaved work may ask you what to do, and the assistant says "Asked Safari to quit", not "Safari quit".
- "Recent files" means most recently **changed** files in your first-tier folders, not Finder's "Recents".
- Automation permission: the first time AppleScript controls an app, macOS asks whether the program running the backend (for example Terminal) may control it. Allow it, or use Hammerspoon, which does not need this.
- Not built yet: deleting, moving or renaming files, and anything with settings. Those tools do not exist.

## Checking it

```bash
.venv/bin/python scripts/automation_check.py                      # what is reachable
.venv/bin/python scripts/automation_check.py --find "budget"      # how a name would be matched (opens nothing)
```
