"""Names people say for websites ("my Gmail", "calendar") and a check for things that look like addresses."""
import re

from automation.fuzzy import normalize

SITES = {
    "gmail": "https://mail.google.com/",
    "google mail": "https://mail.google.com/",
    "calendar": "https://calendar.google.com/",
    "google calendar": "https://calendar.google.com/",
    "youtube": "https://www.youtube.com/",
    "drive": "https://drive.google.com/",
    "google drive": "https://drive.google.com/",
    "docs": "https://docs.google.com/document/",
    "google docs": "https://docs.google.com/document/",
    "sheets": "https://docs.google.com/spreadsheets/",
    "google sheets": "https://docs.google.com/spreadsheets/",
    "maps": "https://maps.google.com/",
    "google maps": "https://maps.google.com/",
    "google": "https://www.google.com/",
    "github": "https://github.com/",
    "wikipedia": "https://www.wikipedia.org/",
    "stack overflow": "https://stackoverflow.com/",
}
_FILLER = {"my", "the", "a", "site", "website", "page", "tab", "app", "please", "homepage", "home"}
_ADDRESS = re.compile(r"^(https?://\S+|localhost(:\d{1,5})?([/?#]\S*)?|[\w-]+(\.[\w-]+)+(:\d{1,5})?([/?#]\S*)?)$", re.I)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def resolve_site(text: str) -> str | None:
    words = [w for w in normalize(text).split() if w not in _FILLER]
    return SITES.get(" ".join(words))


def to_address(text: str) -> str | None:
    """A web address for what the user said, or None if it is neither a known site nor address-like.
    Anything with a scheme (even a bad one like javascript:) is passed on unchanged so the safety layer can refuse it."""
    text = text.strip()
    site = resolve_site(text)
    if site:
        return site
    if _ADDRESS.match(text) or _SCHEME.match(text):
        return text
    return None
