# Safe desktop organization (phase 8)

Sorts the loose files in your Desktop or Downloads into folders, **after showing you exactly what would happen**.

## What you can say

| You say | What happens |
|---|---|
| "Organize my desktop" | A preview of every move; nothing moves until you confirm |
| "Sort my downloads into folders" | The same for `~/Downloads` |
| "Move PDFs into the project folder" | Only PDFs, into a folder called Project (an existing one if there is one, otherwise a new one next to the files) |
| "Organize my desktop" with dry run on (`safety.dry_run: true`) | Describes the same preview and does nothing, ever |

Every request follows the same path: **preview, then your confirmation, then the move**. The confirmation card *is* the preview:

```
In ~/Desktop I would move 9 files:
• Work (new folder), 2: budget.xlsx, script.py
• Documents (new folder), 2: notes.txt, report.pdf
• Media (new folder), 2: movie.mp4, song.mp3
• Images (new folder), 1: photo.jpg
• Personal (new folder), 1: resume.docx
• Other (new folder), 1: app.zip
Name already taken (1), so a timestamp is added: invoice.pdf → invoice 2026-10-29 14-05-33.pdf
Left alone: 3 folders, 5 hidden or system files

Nothing moves until you confirm.
```

Cancel (or ignore it for 60 seconds) and nothing changes.

## How files are sorted

By name only; file contents are never read. The lists are in `organizer/categories.py`.

| Folder | What goes there |
|---|---|
| Work | spreadsheets, presentations, code and data files (xlsx, csv, pptx, py, json, html, ...), and documents named like work (meeting, project, client, proposal, budget, ...) |
| Documents | pdf, doc/docx, pages, txt, md, rtf, epub, ... |
| Media | audio and video (mp3, m4a, wav, mp4, mov, mkv, ...) |
| Images | png, jpg, heic, gif, webp, svg, psd, raw formats, ... |
| Personal | names containing words like resume, passport, tax, insurance, medical, bank, receipt, family, vacation; calendar and contact cards |
| Other | everything else (zip, dmg, pkg, unknown types) |

The folders are made *inside* the folder being organized (`~/Desktop/Documents`, ...). An existing folder with that name (any capitalization) is reused. Someone's `Documents` file in the way only blocks that one category.

## What it will never do

- **Touch hidden or system things.** Hidden files and folders, `.DS_Store`, `~$` lock files, unfinished downloads (`.crdownload`, `.download`, `.part`), files macOS marks hidden or locked, and anything changed in the last minute (it may still be downloading) are left alone and listed as "Left alone".
- **Go into folders.** Only the files directly in the chosen folder are looked at. Folders, apps, shortcuts, aliases and special files are never moved or entered.
- **Organize other places.** Only the folders listed under `organizer.folders` (Desktop and Downloads by default), and only visible folders inside your home folder. Never `~/Library`, never hidden folders, never system folders. A destination for "move PDFs into ..." follows the same rule.
- **Overwrite.** If a name is taken, the file gets a timestamp (`report 2026-10-29 14-05-33.pdf`, then ` 2`, ` 3` if even that is taken), and the preview says so. Names are compared the way the Mac does (ignoring case and accents-as-two-characters). A name that appears after the preview is handled the same way at move time.
- **Copy or delete.** Files are only renamed on the same disk. A destination on another disk is refused.
- **Move something that changed since the preview.** Every file is re-checked (size, modification time, identity) just before it moves; a changed or vanished file stays put and is reported.
- **Run an old preview.** A preview works once, for ten minutes, and the folder is re-checked at the moment of the move.
- **Hide what it did.** Every folder created and every move is written to `data/logs/moves.jsonl` *before* it happens. If the log can't be written, nothing moves.

More than 100 files in one preview raises the risk level: you must acknowledge it explicitly.

## Undo

```bash
.venv/bin/python scripts/organize_undo.py          # shows what would go back and asks
.venv/bin/python scripts/organize_undo.py --list   # the batches that can be undone
```

Puts the last batch back where each file came from. It never overwrites (if something has since taken the old name, that file stays and is reported) and removes only the folders the organizer made, only if they are empty.

## Trusted rules (moving without asking)

By default **every** move waits for your approval. You can grant a standing permission for a routine job by editing `config.yaml` yourself; the assistant cannot add or change one, and the model cannot ask for one:

```yaml
organizer:
  trusted_rules:
    - name: downloads-pictures
      folder: ~/Downloads
      categories: [Images, Media]    # optional
      max_files: 25                  # optional, default 25
```

A rule applies only when the **whole** plan fits it: the same folder, only the listed categories, no more than `max_files` files, a plan that isn't cut short, and no custom destination ("move PDFs into ...") ever. Otherwise the normal preview-and-confirm happens. A rule-covered move still shows what it did, is written to the move log, can be undone, and is still just a description when dry run is on. Rules that are invalid are ignored (and logged), never partly applied.

## Settings (`config.yaml`, `organizer:`)

| Setting | Meaning |
|---|---|
| `enabled` | Turn the feature off completely |
| `folders` | The only folders it may organize |
| `settle_seconds` | Files changed more recently than this are left alone (default 60) |
| `max_files` | Largest preview (default 200); say it again for the rest |
| `trusted_rules` | See above |

## Limits

- Sorting is by name and type, so it will sometimes put a file somewhere you wouldn't (a resume called `final.docx` goes to Personal only if its name says so). The preview is where you catch that; ask for "only PDFs" or similar to narrow it.
- No renaming, no sorting into dated folders, no organizing inside sub-folders.
- Dry run (`safety.dry_run: true`, or `"dry_run": true` on `/route`) never moves anything, even with a trusted rule. The capsule has no switch for it yet.
- Not tried on a real Mac. Mac-only file flags (hidden, locked) are handled but could only be tested with stand-ins.

## Tests

```bash
.venv/bin/python -m pytest tests/test_organizer.py
```

These use a real temporary home folder and real file moves.
