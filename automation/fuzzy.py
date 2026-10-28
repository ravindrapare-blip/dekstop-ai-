"""Forgiving name matching for apps and files. Pure functions, no I/O.

"chrome" finds Google Chrome, "vs code" finds Visual Studio Code, "chorme" still finds Chrome.
"""
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

MIN_SCORE = 0.6          # below this a candidate is not offered at all
CLEAR_MARGIN = 0.12      # the best match must beat the runner-up by this much to be chosen without asking
CHOICE_WINDOW = 0.25     # candidates this close to the best are offered to the user
MAX_CHOICES = 5

# What people actually say, mapped to the names macOS apps use.
APP_ALIASES = {
    "vscode": "visual studio code",
    "vs code": "visual studio code",
    "code": "visual studio code",
    "word": "microsoft word",
    "excel": "microsoft excel",
    "powerpoint": "microsoft powerpoint",
    "outlook": "microsoft outlook",
    "teams": "microsoft teams",
    "settings": "system preferences",
    "system settings": "system preferences",
    "preferences": "system preferences",
    "text edit": "textedit",
}

APP_FILLER = {"the", "my", "app", "application", "please", "program"}
# Verbs the model sometimes leaves in the name ("Open VS Code"). Only removed at the start, so an app
# called "Microsoft To Do" keeps its "to".
LEADING_VERBS = {"open", "launch", "start", "run", "switch", "go", "quit", "close", "focus", "exit"}


def strip_leading_verbs(text: str) -> str:
    tokens = normalize(text).split()
    removed = False
    while tokens and tokens[0] in LEADING_VERBS:
        tokens.pop(0)
        removed = True
    if removed and tokens and tokens[0] == "to":
        tokens.pop(0)
    return " ".join(tokens)


def normalize(text: str) -> str:
    """Lowercase, no accents, letters and digits only, single spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.lower()).split())


def strip_words(text: str, words: set[str]) -> str:
    return " ".join(t for t in normalize(text).split() if t not in words)


def score(query: str, candidate: str) -> float:
    """How well `candidate` matches `query`, from 0 to 1. Both must already be normalized."""
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    q_tokens, c_tokens = query.split(), candidate.split()
    best = 0.0

    if candidate.startswith(query):
        best = 0.95
    # every word the user said appears (as a word or word start) in the name
    if all(any(c == q or c.startswith(q) for c in c_tokens) for q in q_tokens):
        extra = max(0, len(c_tokens) - len(q_tokens))
        best = max(best, max(0.8, 0.9 - 0.02 * extra))
    if len(query) >= 3 and query in candidate:
        best = max(best, 0.75)

    # typos: compare the whole text, and each word when the user said a single word
    pool = [candidate] + (c_tokens if len(q_tokens) == 1 else [])
    ratio = max(SequenceMatcher(None, query, p).ratio() for p in pool)
    if ratio >= 0.75:
        best = max(best, 0.6 + (ratio - 0.75) / 0.25 * 0.24)
    return best


@dataclass(frozen=True)
class Ranked(Generic[T]):
    score: float
    item: T


def rank(query: str, items: list[T], name_of: Callable[[T], str], *, aliases: dict[str, str] | None = None,
         tie_break: Callable[[T], float] = lambda item: 0.0) -> list[Ranked[T]]:
    """Candidates scoring at least MIN_SCORE, best first."""
    forms = [query]
    if aliases and query in aliases:
        forms.append(aliases[query])
    ranked = []
    for item in items:
        name = normalize(name_of(item))
        s = max(score(form, name) for form in forms)
        if s >= MIN_SCORE:
            ranked.append(Ranked(s, item))
    ranked.sort(key=lambda r: (-r.score, -tie_break(r.item), name_of(r.item).lower(), repr(r.item)))   # repr: stable order
    return ranked


@dataclass(frozen=True)
class Pick(Generic[T]):
    kind: str            # "one", "many" or "none"
    items: tuple = ()


def pick(ranked: list[Ranked[T]], max_choices: int = MAX_CHOICES) -> Pick[T]:
    """Choose automatically only when one candidate is clearly the best; otherwise offer a short list."""
    if not ranked:
        return Pick("none")
    top = ranked[0]
    exact = [r for r in ranked if r.score >= 1.0]
    if len(exact) == 1:
        return Pick("one", (top.item,))
    if len(ranked) == 1:
        return Pick("one", (top.item,))
    if top.score >= 0.85 and ranked[1].score < top.score - CLEAR_MARGIN:
        return Pick("one", (top.item,))
    close = [r.item for r in ranked if r.score >= top.score - CHOICE_WINDOW][:max_choices]
    return Pick("many", tuple(close)) if len(close) > 1 else Pick("one", tuple(close))
