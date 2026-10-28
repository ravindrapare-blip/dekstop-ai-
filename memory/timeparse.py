"""Times and dates, read leniently. The model should send ISO 8601, but a small model sometimes sends
"tomorrow at 9" or "in 10 minutes"; those are understood too. Local time throughout, no time zones."""
import re
from datetime import date, datetime, timedelta, timezone

WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
            "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
            "sunday": 6, "sun": 6}
DEFAULT_HOUR = 9           # "tomorrow" or "on Friday" with no time means 9 AM

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?\s*(Z|[+-]\d{2}:?\d{2})?$", re.I)
_RELATIVE = re.compile(r"\bin\s+(an?|\d{1,4})\s*(min(?:ute)?s?|h(?:ou)?rs?|hours?|days?|weeks?)\b")
_MERIDIEM = re.compile(r"(?<![\d:])(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b\.?")
_AT = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?(?![\d:])")
_COLON = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
_DAY_WORDS = re.compile(r"\b(today|tonight|tomorrow|(?:next\s+|on\s+|this\s+)?(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r"))\b")


def _iso(text: str) -> datetime | None:
    m = _ISO.match(text.strip())
    if not m:
        return None
    y, mo, d, h, mi, sec, zone = m.groups()
    try:
        value = datetime(int(y), int(mo), int(d), int(h or DEFAULT_HOUR), int(mi or 0), int(sec or 0))
    except ValueError:
        return None
    if zone:                                            # a time zone was given: convert to this Mac's local time
        if zone.upper() == "Z":
            offset = timedelta(0)
        else:
            digits = zone[1:].replace(":", "")
            offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:])) * (-1 if zone[0] == "-" else 1)
        value = (value - offset).replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    return value


def _guess_24h(hour: int) -> int:
    """"at 6" with no am/pm: 7-11 mean morning, 12 and 1-6 mean afternoon or evening."""
    if hour == 12:
        return 12
    return hour + 12 if hour <= 6 else hour


def _time_of_day(text: str) -> tuple[int, int, bool] | None:
    """(hour, minute, certain) from the words, or None. `certain` is False when am/pm had to be guessed."""
    if re.search(r"\bnoon\b", text):
        return 12, 0, True
    if re.search(r"\bmidnight\b", text):
        return 0, 0, True
    m = _MERIDIEM.search(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if not (1 <= hour <= 12 and minute < 60):
            return None
        return (hour % 12) + (12 if m.group(3).lower() == "p" else 0), minute, True
    m = _COLON.search(text) or _AT.search(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if hour > 23 or minute > 59:
            return None
        written_24h = m.re is _COLON and m.group(1).startswith("0")      # "08:30" is 24-hour; "6:30" is a guess
        if hour == 0 or hour > 12 or written_24h:
            return hour, minute, True
        return _guess_24h(hour), minute, False
    return None


def parse_when(text: str, now: datetime) -> datetime | None:
    """A moment from ISO 8601 or a few natural phrases; None if it can't be understood."""
    if not isinstance(text, str):
        return None
    if _ISO.match(text.strip()):
        return _iso(text)                               # it was meant as ISO: if it is not a real moment, it is not guessed at
    words = " ".join(text.lower().replace(",", " ").split())
    if not words:
        return None

    rel = _RELATIVE.search(words)
    if rel:
        amount = 1 if rel.group(1) in ("a", "an") else int(rel.group(1))
        unit = rel.group(2)
        delta = (timedelta(minutes=amount) if unit.startswith("min") else timedelta(hours=amount) if unit.startswith("h")
                 else timedelta(days=amount) if unit.startswith("day") else timedelta(weeks=amount))
        return (now + delta).replace(second=0, microsecond=0)

    clock = _time_of_day(words)
    written_date = re.search(r"\d{4}-\d{2}-\d{2}", words)     # "2026-10-29 at 9pm"
    day = written_date or _DAY_WORDS.search(words)
    if day is None and clock is None:
        return None
    hour, minute = (clock[0], clock[1]) if clock else (DEFAULT_HOUR, 0)

    if day is None:                                     # only a time: the next time it happens
        today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if clock and not clock[2]:                      # am/pm was guessed: the other half of the day may come first
            other = today - timedelta(hours=12) if hour >= 12 else today + timedelta(hours=12)
            options = sorted(t for t in (today, other, today + timedelta(days=1)) if t > now)
            return options[0] if options else today + timedelta(days=1)
        return today if today > now else today + timedelta(days=1)

    base = parse_date(written_date.group() if written_date else words, now.date())
    if base is None:
        return None
    moment = datetime(base.year, base.month, base.day, hour, minute)
    guessed = clock is not None and not clock[2]
    if guessed and hour < 12 and (re.search(r"\btonight\b", words) or moment <= now):
        later = moment + timedelta(hours=12)             # "today at 9" said in the evening means 9 PM
        if later.date() == moment.date() and (later > now or re.search(r"\btonight\b", words)):
            moment = later
    return moment


def parse_date(text: str, today: date) -> date | None:
    """A date from YYYY-MM-DD, today, tomorrow, a weekday name, or "in N days"; None otherwise."""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$", raw)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    words = " ".join(raw.lower().replace(",", " ").split())
    if re.search(r"\b(today|tonight)\b", words):
        return today
    if re.search(r"\btomorrow\b", words):
        return today + timedelta(days=1)
    rel = re.search(r"\bin\s+(an?|\d{1,3})\s*(days?|weeks?)\b", words)
    if rel:
        n = 1 if rel.group(1) in ("a", "an") else int(rel.group(1))
        return today + timedelta(days=n * (7 if rel.group(2).startswith("week") else 1))
    day = _DAY_WORDS.search(words)
    if day and day.group(2):
        ahead = (WEEKDAYS[day.group(2)] - today.weekday()) % 7
        if ahead == 0 and words.startswith("next"):
            ahead = 7
        return today + timedelta(days=ahead)
    return None


def clock_text(moment: datetime) -> str:
    return f"{(moment.hour % 12) or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def day_text(day: date, today: date) -> str:
    delta = (day - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    if 1 < delta < 7:
        return day.strftime("%A")
    text = f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"
    return text if day.year == today.year else f"{text} {day.year}"


def when_text(moment: datetime, now: datetime) -> str:
    """"today 6:00 PM", "tomorrow 9:00 AM", "Fri 3 Nov 2:00 PM"."""
    return f"{day_text(moment.date(), now.date())} {clock_text(moment)}"
