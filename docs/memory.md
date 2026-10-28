# Reminders, tasks, notes and memory (phase 7)

What the assistant remembers for you, and how it stays private and small.

## What you can say

| You say | What happens |
|---|---|
| "Remind me at 6 PM to call mom" | `create_reminder`. A macOS notification appears at that time |
| "What are my reminders?" | `list_reminders` (with numbers) |
| "Delete the reminder about mom" | `delete_reminder`: finds it by number or words, **asks first** |
| "Add a task: write the report, due Friday" | `create_task` (title, optional details, optional due date) |
| "What's on my to-do list?" | `list_tasks`, soonest first, overdue ones marked |
| "I finished the report task" | `complete_task` by number or words |
| "Plan my day" | `plan_my_day`: today's reminders and tasks, overdue first, plus what to start with. No AI guessing: it only reads what you saved |
| "Create a note about my project: ..." | `save_note`. Saving under an existing title **adds** to that note, so nothing is overwritten |
| "Show my notes", "read my project note" | `list_notes`, `get_note` |
| "Remember I prefer Chrome" | `set_preference` (key "preferred browser", value "Chrome") |
| "What's my preferred browser?" | `get_preference` (a close name works too) |

If several things match ("call mom" and "call mum's friend"), the capsule asks which one.

## Where it lives

One SQLite file, `data/assistant.db` (path in `config.yaml`, `data.database`). Tables: `reminders`, `tasks`, `notes`, `preferences`, `command_history` (schema in `memory/schema.sql`, versioned with `PRAGMA user_version`).

## Privacy

- **Local only.** The `memory/` code uses no network at all (a test checks this). Notes, reminders and preferences are never sent to a service. The language model runs on this Mac too.
- **Private file.** Created readable by your user only (`0600`, folder `0700`), and corrected if it was ever widened.
- **No secrets.** The safety layer refuses to remember passwords, PINs, card numbers or things that look like secret keys, in preference names or values. Notes are free text, so do not write passwords in them.
- **The log stays clean.** The audit log (`data/logs/audit.jsonl`) records note text, task details and preference values only as a length.
- **Command history is short and optional.** `command_history` keeps what you asked (trimmed to 200 characters, and nothing for text you asked it to type into a web page), the tool and how it went: at most `memory.history_limit` entries and `memory.history_days` days. Turn it off with `memory.history: false`.
- **You can look and delete.**

```bash
.venv/bin/python scripts/memory_check.py                    # where the file is, permissions, how much is stored
.venv/bin/python scripts/memory_check.py --show history     # or preferences, reminders, tasks, notes
.venv/bin/python scripts/memory_check.py --clear history    # or any table, or: all   (asks first)
```

## Staying lightweight

- Limits: 200 waiting reminders, 500 open tasks, 500 notes (5,000 characters each), 100 preferences, 300 history entries. At a limit the assistant says so instead of growing.
- Fired reminders are dropped after 7 days, finished tasks after 90, history after 30 (cleaned every few hours).
- The model sees at most `memory.prompt_preferences` (default 8) of your most recently changed preferences, in one line of at most 400 characters, labelled as facts and not instructions. Notes, tasks and history are never put in the prompt. Set it to 0 to show none.
- No embeddings and no search index: plain SQL and name matching.
- One small thread watches the clock. It sleeps until the next reminder (checking at least every 30 seconds) and does nothing else.

## Reminders and notifications

- Times are your Mac's local time. The model writes ISO 8601 (`2026-03-04T18:00:00`); words such as "in 10 minutes" or "tomorrow at 9" are also understood. A time that has already passed is refused with a clear message, never moved silently. A bare "at 6" is read as the next 6 AM or PM, whichever comes first.
- Notifications use `osascript` (`display notification`), with the text passed as an argument, never inside the script. The first time, macOS may ask permission, or you may need to allow notifications for **Script Editor** in System Settings > Notifications. Test with `scripts/memory_check.py --notify`.
- The backend must be running for reminders to appear. If the Mac was asleep or the backend off, a reminder that was due shows up as "Missed reminder" when it starts again. If macOS refuses a notification, it is tried three times, then marked as gone off (it stays in `list_reminders`).
- A time zone change or daylight saving shift is not tracked: times are stored as local clock times.

## Limits

- Reminders appear only as macOS notifications (nothing in the capsule yet, and nothing spoken).
- No repeating reminders, snoozing, or editing: delete and create again.
- Task details and notes are stored as typed, with no encryption on disk. Use FileVault for that.
