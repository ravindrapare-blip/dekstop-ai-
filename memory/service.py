"""Reminders, tasks, notes, preferences and the day plan, in the same two steps as the other services:

    prepare()  read-only: check and tidy the request, find the real reminder, task or note it means
               (ask the user if unsure)
    -- the safety layer then judges the real target --
    execute()  only called for a call the safety layer authorized

Everything is stored in one local SQLite file and nothing is sent anywhere.
"""
import logging
import re
import sqlite3
from datetime import datetime, timedelta

from automation.choices import ChoiceStore
from automation.fuzzy import normalize, pick, rank, strip_words
from automation.results import ActionResult, Prepared
from memory.db import MemoryError_
from memory.scheduler import ReminderScheduler
from memory.store import (MAX_DETAILS_CHARS, MAX_NOTE_CHARS, MAX_TEXT_CHARS, LimitReached, MemoryStore, Reminder,
                          Task, normalize_key)
from memory.timeparse import clock_text, day_text, parse_date, parse_when, when_text

log = logging.getLogger("assistant.memory")

MEMORY_TOOLS = (
    "create_reminder", "list_reminders", "delete_reminder",
    "create_task", "list_tasks", "complete_task",
    "save_note", "list_notes", "get_note",
    "set_preference", "get_preference", "plan_my_day",
)
FILLER = {"the", "my", "a", "an", "to", "about", "called", "named", "titled", "that", "this", "one", "please"}
MAX_TITLE_CHARS = 120
MAX_VALUE_CHARS = 200
MAX_YEARS_AHEAD = 5
GRACE = timedelta(minutes=1)            # "now" from a model that rounds down is not "in the past"
NO_HISTORY_TEXT = {"fill_field"}        # what you asked to type into a page is never kept
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_CONTROL_LINE = re.compile(r"[\x00-\x1f\x7f]")


class _Stop(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


def one_line(value, what: str, limit: int) -> str:
    if not isinstance(value, str) or not _CONTROL_LINE.sub(" ", value).strip():
        raise _Stop(f"I need {what}.")
    text = " ".join(_CONTROL_LINE.sub(" ", value).split())
    if len(text) > limit:
        raise _Stop(f"That's too long for {what}. Keep it under {limit} characters.")
    return text


def block(value, what: str, limit: int, *, required: bool) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise _Stop(f"I need {what}.")
        return ""
    if not isinstance(value, str):
        raise _Stop(f"I need {what} as text.")
    text = _CONTROL.sub(" ", value).strip()
    if len(text) > limit:
        raise _Stop(f"That's too long for {what}. Keep it under {limit} characters.")
    return text


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _listing(parts: list[str]) -> str:
    shown = ", ".join(parts[:3])
    return shown + (f" (+{len(parts) - 3} more)" if len(parts) > 3 else "")


class MemoryService:
    def __init__(self, store: MemoryStore, notifier=None, scheduler: ReminderScheduler | None = None, *,
                 choices: ChoiceStore | None = None, clock=datetime.now, enabled: bool = True,
                 notifications: bool = True, history: bool = True, history_limit: int = 300,
                 history_days: int = 30, prompt_preferences: int = 8) -> None:
        self.store = store
        self.notifier = notifier
        self.scheduler = scheduler
        self.choices = choices if choices is not None else ChoiceStore()     # not `or`: an empty store is falsy
        self.clock = clock
        self.enabled = enabled
        self.notifications = notifications
        self.history = history
        self.history_limit = history_limit
        self.history_days = history_days
        self.prompt_preferences = prompt_preferences

    # ---- lifecycle and small helpers used by the backend ---------------------------------------------------

    def start(self) -> None:
        if self.enabled and self.scheduler is not None:
            self.scheduler.start()

    def choose(self, choice_id: str, option_id: str | None):
        return self.choices.resolve(choice_id, option_id)

    def close(self) -> None:
        if self.scheduler is not None:
            self.scheduler.stop()

    def status(self) -> dict:
        try:
            counts = self.store.counts()
        except MemoryError_ as e:
            return {"enabled": self.enabled, "ok": False, "problem": str(e)}
        return {"enabled": self.enabled, "ok": True, "notifications": self.notifications, "counts": counts}

    def context_line(self) -> str:
        """A short list of what is known about the user, for the model's prompt. Empty when nothing is saved."""
        if not self.enabled:
            return ""
        try:
            prefs = self.store.preferences(self.prompt_preferences)
        except (MemoryError_, sqlite3.Error):
            return ""
        line = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in prefs)
        return line if len(line) <= 400 else line[:399] + "…"

    def record_command(self, text: str, tool: str, status: str) -> None:
        """Remembers that a request was made (trimmed text, which tool, how it went). Never raises."""
        if not (self.enabled and self.history):
            return
        try:
            self.store.record_command("" if tool in NO_HISTORY_TEXT else " ".join(str(text).split()), tool, status,
                                      self.clock(), keep=self.history_limit)
        except (MemoryError_, sqlite3.Error):
            log.warning("could not record the command")

    # ---- step 1: check the request and find what it means (read-only) ----------------------------------------

    def prepare(self, tool: str, args: dict, requires_confirmation: bool = False,
                dry_run: bool | None = None) -> Prepared:
        if not self.enabled:
            return Prepared("failed", result=ActionResult(
                "not_available", "Memory is turned off. Set memory.enabled: true in config.yaml."))
        try:
            return getattr(self, f"_prepare_{tool}")(args, requires_confirmation, dry_run)
        except _Stop as stop:
            return Prepared("failed", result=ActionResult("failed", stop.message))
        except (MemoryError_, sqlite3.Error) as e:
            return Prepared("failed", result=ActionResult("failed", self._problem(e)))

    @staticmethod
    def _problem(error: Exception) -> str:
        return str(error) if isinstance(error, MemoryError_) else "I couldn't use my memory file just now. Nothing was changed."

    @staticmethod
    def _ready(args: dict) -> Prepared:
        return Prepared("ready", args)

    def _prepare_none(self, args, confirm, dry_run) -> Prepared:
        return self._ready({})                 # tools without arguments: whatever the model added is dropped

    _prepare_list_reminders = _prepare_list_tasks = _prepare_list_notes = _prepare_plan_my_day = _prepare_none

    def _prepare_create_reminder(self, args, confirm, dry_run) -> Prepared:
        text = one_line(args.get("text"), "what to remind you about", MAX_TEXT_CHARS)
        raw = args.get("due_datetime")
        now = self.clock()
        due = parse_when(raw, now) if isinstance(raw, str) else None
        if due is None:
            shown = " ".join(str(raw).split())[:40] if isinstance(raw, str) else ""
            raise _Stop(f"I couldn't understand the time “{shown}”. Try something like “tomorrow at 9 am”.")
        if due < now - GRACE:
            raise _Stop(f"That time has already passed (it's {clock_text(now)} now). Tell me a later time.")
        if due > now + timedelta(days=366 * MAX_YEARS_AHEAD):
            raise _Stop("That's too far ahead for a reminder.")
        return self._ready({"text": text, "due_datetime": due.replace(microsecond=0).isoformat(timespec="seconds")})

    def _prepare_create_task(self, args, confirm, dry_run) -> Prepared:
        ready = {"title": one_line(args.get("title"), "a title for the task", MAX_TITLE_CHARS)}
        details = block(args.get("details"), "the details", MAX_DETAILS_CHARS, required=False)
        if details:
            ready["details"] = details
        raw = args.get("due_date")
        if isinstance(raw, str) and raw.strip():
            day = parse_date(raw, self.clock().date())
            if day is None:
                raise _Stop(f"I couldn't understand the date “{' '.join(raw.split())[:40]}”. Try “friday” or 2026-03-04.")
            ready["due_date"] = day.isoformat()
        return self._ready(ready)

    def _prepare_save_note(self, args, confirm, dry_run) -> Prepared:
        return self._ready({"title": one_line(args.get("title"), "a title for the note", MAX_TITLE_CHARS),
                            "content": block(args.get("content"), "the note's text", MAX_NOTE_CHARS, required=True)})

    def _prepare_set_preference(self, args, confirm, dry_run) -> Prepared:
        raw_key = args.get("key")
        key = normalize_key(raw_key) if isinstance(raw_key, str) else None
        if key is None:
            raise _Stop("I can't use that as a name to remember. Use a few plain words, like “preferred browser”.")
        return self._ready({"key": key, "value": one_line(args.get("value"), "the value to remember", MAX_VALUE_CHARS)})

    def _prepare_get_preference(self, args, confirm, dry_run) -> Prepared:
        raw_key = args.get("key")
        key = normalize_key(raw_key) if isinstance(raw_key, str) else None
        if key is None:
            raise _Stop("Which preference? Tell me a few plain words, like “preferred browser”.")
        return self._ready({"key": key})

    # finding a reminder, task or note by number or by words

    def _find(self, tool, ref, entries: list[tuple[int, str, str]], noun: str, confirm, dry_run, make_args) -> Prepared:
        """entries: (id, label, detail). `ref` is a number from a list, or words from the label."""
        if isinstance(ref, bool) or not isinstance(ref, (int, str)) or (isinstance(ref, str) and not ref.strip()):
            raise _Stop(f"Which {noun}? Give me its number from the list, or some of its words.")
        text = str(ref).strip().lstrip("#")
        if text.isdigit():
            hit = next((e for e in entries if e[0] == int(text)), None)
            if hit is None:
                raise _Stop(f"There's no {noun} number {int(text)}.")
            return self._ready(make_args(hit))
        query = strip_words(text, FILLER | {noun, noun + "s"}) or normalize(text)      # "the mom reminder" -> "mom"
        ranked = rank(query, entries, name_of=lambda e: e[1])
        found = pick(ranked, 5)
        if found.kind == "none":
            raise _Stop(f"I couldn't find a {noun} matching “{' '.join(text.split())[:60]}”.")
        if len(found.items) == 1:
            return self._ready(make_args(found.items[0]))
        targets = {str(i): make_args(e) for i, e in enumerate(found.items, 1)}
        shown = [{"id": str(i), "label": e[1], "detail": e[2]} for i, e in enumerate(found.items, 1)]
        return self.choices.ask(tool, targets, shown, confirm, dry_run,
                                f"Which {noun} did you mean by “{' '.join(text.split())[:60]}”?")

    def _prepare_delete_reminder(self, args, confirm, dry_run) -> Prepared:
        now = self.clock()
        entries = [(r.id, r.text, when_text(r.due_at, now)) for r in self.store.reminders()]
        return self._find("delete_reminder", args.get("id"), entries, "reminder", confirm, dry_run,
                          lambda e: {"id": e[0], "text": e[1]})

    def _prepare_complete_task(self, args, confirm, dry_run) -> Prepared:
        today = self.clock().date()
        ref = args.get("id")
        if isinstance(ref, (int, str)) and not isinstance(ref, bool) and str(ref).strip().lstrip("#").isdigit():
            task = self.store.get_task(int(str(ref).strip().lstrip("#")))
            if task is not None and task.status == "done":
                raise _Stop(f"“{task.title}” is already done.")
        entries = [(t.id, t.title, self._due_text(t, today)) for t in self.store.tasks()]
        return self._find("complete_task", ref, entries, "task", confirm, dry_run, lambda e: {"id": e[0], "title": e[1]})

    def _prepare_get_note(self, args, confirm, dry_run) -> Prepared:
        entries = [(n.id, n.title, f"updated {n.updated_at[:10]}") for n in self.store.notes()]
        return self._find("get_note", args.get("title"), entries, "note", confirm, dry_run,
                          lambda e: {"id": e[0], "title": e[1]})

    @staticmethod
    def _due_text(task: Task, today) -> str:
        if not task.due_date:
            return "no due date"
        return "due " + day_text(datetime.fromisoformat(task.due_date).date(), today)

    # ---- step 2: do it (only for calls the safety layer authorized) -----------------------------------------

    def execute(self, tool: str, args: dict) -> ActionResult | None:
        if tool == "reply":
            return None
        if tool not in MEMORY_TOOLS:
            return ActionResult("not_available", "That isn't something I can do yet, so nothing was done.")
        if not self.enabled:
            return ActionResult("not_available", "Memory is turned off. Set memory.enabled: true in config.yaml.")
        try:
            return getattr(self, f"_do_{tool}")(args)
        except LimitReached as e:
            return ActionResult("failed", str(e))
        except _Stop as e:
            return ActionResult("failed", e.message)
        except (MemoryError_, sqlite3.Error) as e:
            log.warning("memory action %s failed: %s", tool, type(e).__name__)
            return ActionResult("failed", self._problem(e))
        except Exception:
            log.exception("memory action %s failed unexpectedly", tool)
            return ActionResult("failed", "Something went wrong. Nothing was changed.")

    def _wake(self) -> None:
        if self.scheduler is not None:
            self.scheduler.wake()

    def _do_create_reminder(self, args: dict) -> ActionResult:
        now = self.clock()
        due = parse_when(args.get("due_datetime"), now)
        if due is None:
            raise _Stop("I couldn't understand the time.")
        text = one_line(args.get("text"), "what to remind you about", MAX_TEXT_CHARS)
        self.store.add_reminder(text, due, now)
        self._wake()
        message = f"I'll remind you to {text} {when_text(due, now)}."
        if not self.notifications:
            message += " Notifications are turned off in config.yaml, so you won't see it pop up."
        return ActionResult("done", message, via="memory")

    def _do_list_reminders(self, args: dict) -> ActionResult:
        now = self.clock()
        reminders = self.store.reminders()
        if not reminders:
            return ActionResult("done", "You have no reminders.", via="memory")
        waiting = [r for r in reminders if r.status == "pending"]
        items = [self._reminder_line(r, now) for r in reminders]
        head = f"You have {_plural(len(waiting), 'reminder')} waiting" if waiting else "Nothing is waiting"
        parts = [f"{r.text} ({when_text(r.due_at, now)})" for r in waiting]
        return ActionResult("done", head + (": " + _listing(parts) if parts else "") + ".", items, via="memory")

    @staticmethod
    def _reminder_line(r: Reminder, now: datetime) -> str:
        return f"#{r.id}  {r.text} — {'went off ' if r.status == 'fired' else ''}{when_text(r.due_at, now)}"

    def _do_delete_reminder(self, args: dict) -> ActionResult:
        reminder = self.store.get_reminder(self._id(args))
        if reminder is None or not self.store.delete_reminder(reminder.id):
            return ActionResult("failed", "That reminder is already gone.")
        self._wake()
        return ActionResult("done", f"Deleted the reminder “{reminder.text}”.", via="memory")

    @staticmethod
    def _id(args: dict) -> int:
        value = args.get("id")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise _Stop("I need the number of the item.")
        return value

    def _do_create_task(self, args: dict) -> ActionResult:
        now = self.clock()
        title = one_line(args.get("title"), "a title for the task", MAX_TITLE_CHARS)
        details = block(args.get("details"), "the details", MAX_DETAILS_CHARS, required=False)
        due = args.get("due_date") or None
        if due is not None and parse_date(due, now.date()) is None:
            raise _Stop("I couldn't understand the date.")
        self.store.add_task(title, details, due, now)
        suffix = f", due {day_text(datetime.fromisoformat(due).date(), now.date())}" if due else ""
        return ActionResult("done", f"Added the task “{title}”{suffix}.", via="memory")

    def _do_list_tasks(self, args: dict) -> ActionResult:
        today = self.clock().date()
        tasks = self.store.tasks()
        if not tasks:
            return ActionResult("done", "You have no open tasks.", via="memory")
        items = [f"#{t.id}  {t.title} — {self._task_when(t, today)}" for t in tasks]
        overdue = sum(1 for t in tasks if t.due_date and t.due_date < today.isoformat())
        message = f"You have {_plural(len(tasks), 'open task')}" + (f", {overdue} overdue" if overdue else "")
        return ActionResult("done", f"{message}: {_listing([t.title for t in tasks])}.", items, via="memory")

    @staticmethod
    def _task_when(task: Task, today) -> str:
        if not task.due_date:
            return "no due date"
        day = datetime.fromisoformat(task.due_date).date()
        return ("overdue, was due " if day < today else "due ") + day_text(day, today)

    def _do_complete_task(self, args: dict) -> ActionResult:
        task = self.store.get_task(self._id(args))
        if task is None:
            return ActionResult("failed", "I can't find that task any more.")
        if not self.store.complete_task(task.id, self.clock()):
            return ActionResult("failed", f"“{task.title}” is already done.")
        return ActionResult("done", f"Marked “{task.title}” as done.", via="memory")

    def _do_save_note(self, args: dict) -> ActionResult:
        title = one_line(args.get("title"), "a title for the note", MAX_TITLE_CHARS)
        content = block(args.get("content"), "the note's text", MAX_NOTE_CHARS, required=True)
        _, created = self.store.save_note(title, content, self.clock())
        return ActionResult("done", f"Saved the note “{title}”." if created else f"Added to your note “{title}”.", via="memory")

    def _do_list_notes(self, args: dict) -> ActionResult:
        notes = self.store.notes()
        if not notes:
            return ActionResult("done", "You have no notes.", via="memory")
        items = [f"#{n.id}  {n.title} — {self._snippet(n.content, 60)}" for n in notes]
        return ActionResult("done", f"You have {_plural(len(notes), 'note')}: {_listing([n.title for n in notes])}.", items, via="memory")

    @staticmethod
    def _snippet(text: str, limit: int) -> str:
        flat = " ".join(text.split())
        return flat if len(flat) <= limit else flat[: limit - 1] + "…"

    def _do_get_note(self, args: dict) -> ActionResult:
        note = self.store.get_note(self._id(args))
        if note is None:
            return ActionResult("failed", "I can't find that note any more.")
        lines = [line for line in note.content.splitlines() if line.strip()]
        return ActionResult("done", f"{note.title}: {self._snippet(note.content, 600)}", lines[:12], via="memory")

    def _do_set_preference(self, args: dict) -> ActionResult:
        key = normalize_key(args.get("key", "")) or ""
        value = one_line(args.get("value"), "the value to remember", MAX_VALUE_CHARS)
        if not key:
            raise _Stop("I can't use that as a name to remember.")
        previous = self.store.set_preference(key, value, self.clock())
        name = key.replace("_", " ")
        if previous is None:
            return ActionResult("done", f"Got it. I'll remember that your {name} is {value}.", via="memory")
        if previous == value:
            return ActionResult("done", f"I already had that: your {name} is {value}.", via="memory")
        return ActionResult("done", f"Updated: your {name} is now {value} (it was {previous}).", via="memory")

    def _do_get_preference(self, args: dict) -> ActionResult:
        key = normalize_key(args.get("key", "")) or ""
        value = self.store.get_preference(key)
        if value is not None:
            return ActionResult("done", f"Your {key.replace('_', ' ')} is {value}.", via="memory")
        known = self.store.preferences()
        close = pick(rank(normalize(key.replace("_", " ")), known, name_of=lambda kv: kv[0].replace("_", " ")), 5)
        if close.kind == "one":
            k, v = close.items[0]
            return ActionResult("done", f"Your {k.replace('_', ' ')} is {v}.", via="memory")
        if close.kind == "many":
            items = [f"{k.replace('_', ' ')}: {v}" for k, v in close.items]
            return ActionResult("done", f"I have a few things like that: {_listing(items)}.", items, via="memory")
        return ActionResult("done", f"I don't have anything saved for “{key.replace('_', ' ')}”.", via="memory")

    def _do_plan_my_day(self, args: dict) -> ActionResult:
        now = self.clock()
        today = now.date()
        reminders = [r for r in self.store.reminders() if r.status == "pending" and r.due_at.date() == today]
        tasks = self.store.tasks()
        iso = today.isoformat()
        overdue = [t for t in tasks if t.due_date and t.due_date < iso]
        due_today = [t for t in tasks if t.due_date == iso]
        later = [t for t in tasks if not t.due_date or t.due_date > iso][:3]
        if not (reminders or overdue or due_today):
            hint = f" You also have {_plural(len(later), 'open task')} with no date." if later else ""
            return ActionResult("done", "Nothing is scheduled for today." + hint + " You can ask me to add a task or a reminder.",
                                [f"Task: {t.title}" for t in later], via="memory")
        items = [f"Overdue: {t.title} ({self._task_when(t, today)})" for t in overdue]
        items += [f"{clock_text(r.due_at)} — {r.text}" for r in reminders]
        items += [f"Task due today: {t.title}" for t in due_today]
        items += [f"When you have time: {t.title}" for t in later]
        first = (f"“{overdue[0].title}”" if overdue else f"“{reminders[0].text}” at {clock_text(reminders[0].due_at)}" if reminders
                 else f"“{due_today[0].title}”")
        parts = [_plural(len(reminders), "reminder"), _plural(len(overdue) + len(due_today), "task")]
        extra = f" ({len(overdue)} overdue)" if overdue else ""
        return ActionResult("done", f"Today you have {parts[0]} and {parts[1]}{extra}. Start with {first}.", items[:15], via="memory")
