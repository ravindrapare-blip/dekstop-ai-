"""Argument checks. They can raise a tool's risk but never lower it. Pure functions, no I/O."""
import os
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlparse

from safety.policy import Policy
from safety.risk import Risk

# Args that name an app, path, address or query must be single-line, with no control characters.
SINGLE_LINE_ARGS = {"app_name", "path_or_name", "url", "query", "tab", "label", "target", "direction", "page_hint", "field_kind",
                    "key", "title", "due_date", "due_datetime"}
SCROLL_DIRECTIONS = ("up", "down", "top", "bottom")
PAGE_HINTS = ("", "login", "payment")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")          # everything but tab and newline
_CONTROL_OR_NEWLINE = re.compile(r"[\x00-\x1f\x7f]")
_SHELL_META = re.compile(r"[;|&$`<>\\]")
# "name:" is a scheme unless a port number (1-5 digits) follows: localhost:3000, example.com:8080/x.
# Addresses without a scheme are checked as https://..., so the executor must add https:// too.
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):(?!\d{1,5}(?:[/?#]|$))")
_PATH_START = ("/", "~", "./", "../")


@dataclass(frozen=True)
class Finding:
    risk: Risk
    reason: str


def _worst(findings: list[Finding]) -> Finding | None:
    return max(findings, key=lambda f: f.risk, default=None)


def is_within(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def resolve_path(value: str) -> str | None:
    """Return the real absolute path if `value` looks like a path, else None (it is just a name)."""
    if not value.startswith(_PATH_START):
        return None
    return os.path.realpath(os.path.expanduser(value))


def check_path(value: str, policy: Policy, *, opening_file: bool) -> list[Finding]:
    findings = []
    path = resolve_path(value)
    if path is not None:
        for base in policy.protected_paths:
            real_base = os.path.realpath(os.path.expanduser(base))
            if is_within(path, real_base) or is_within(path, os.path.expanduser(base)):
                findings.append(Finding(Risk.HIGH, f"{base} is a protected location"))
                break
    if opening_file:
        ext = os.path.splitext(value.rstrip("/"))[1].lower()
        if ext in policy.risky_extensions:
            findings.append(Finding(Risk.HIGH, f"opening a {ext} item can run code"))
    return findings


def check_url(value: str, policy: Policy) -> list[Finding]:
    m = _SCHEME.match(value)
    if m:
        scheme = m.group(1).lower()
        if scheme not in ("http", "https"):
            return [Finding(Risk.CRITICAL, f"only http and https links are allowed, not {scheme}:")]
        parsed = urlparse(value)
    else:
        parsed = urlparse("https://" + value)
    if not parsed.hostname:
        return [Finding(Risk.CRITICAL, "that is not a valid web address")]
    text = (parsed.hostname + parsed.path).lower()
    tokens = set(re.split(r"[^a-z0-9]+", text))
    tokens |= {t.replace("-", "").replace("_", "") for t in re.split(r"[^a-z0-9_-]+", text)}
    if tokens & policy.sensitive_url_words:
        return [Finding(Risk.MEDIUM, "this looks like a login or payment page")]
    return []


def check_app(tool: str, value: str, policy: Policy) -> list[Finding]:
    if _SHELL_META.search(value):
        return [Finding(Risk.CRITICAL, "the app name contains characters that are never allowed")]
    if tool == "close_app" and value.lower().removesuffix(".app") in policy.protected_apps:
        return [Finding(Risk.HIGH, f"closing {value} can disrupt macOS")]
    return []


# Preferences are plain, lightweight facts ("preferred browser: Chrome"). Secrets are never stored.
_SECRET_WORDS = {"password", "passwords", "passwd", "passcode", "pin", "secret", "secrets", "token", "tokens",
                 "cvv", "cvc", "ssn", "otp"}
_SECRET_JOINED = ("apikey", "privatekey", "secretkey", "creditcard", "cardnumber", "socialsecurity", "recoverycode")
_CARD_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_LOOKS_LIKE_A_KEY = re.compile(r"^[A-Za-z0-9_+/=-]{32,}$")


def check_preference(args: dict) -> list[Finding]:
    key, value = args.get("key"), args.get("value")
    findings = []
    if isinstance(key, str):
        tokens = [t for t in re.split(r"[^a-z0-9]+", key.lower()) if t]
        if set(tokens) & _SECRET_WORDS or any(w in "".join(tokens) for w in _SECRET_JOINED):
            findings.append(Finding(Risk.CRITICAL, "I don't store passwords, PINs, card numbers or secret keys"))
    if isinstance(value, str) and (_CARD_NUMBER.search(value) or _LOOKS_LIKE_A_KEY.match(value.strip())
                                   or "-----BEGIN" in value):
        findings.append(Finding(Risk.CRITICAL, "that looks like a card number or secret key, which I don't store"))
    return findings


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _mixed_script(text: str) -> bool:
    """A word mixing Latin letters with look-alike Cyrillic or Greek ones (to dodge the word lists)."""
    for word in text.split():
        scripts = {unicodedata.name(c, "").split(" ")[0] for c in word if c.isalpha()}
        if "LATIN" in scripts and scripts & {"CYRILLIC", "GREEK"}:
            return True
    return False


def check_click_target(label: str, policy: Policy) -> list[Finding]:
    """Judge the visible name of the real button or link that will be clicked."""
    if _mixed_script(label):
        return [Finding(Risk.MEDIUM, "the button's name mixes alphabets, which can hide what it does")]
    words = " " + " ".join("".join(c if c.isalnum() else " " for c in label.lower()).split()) + " "
    hit = next((w for w in sorted(policy.click_high_words) if f" {w} " in words), None)
    if hit:
        return [Finding(Risk.HIGH, f"clicking “{label}” could delete something or spend money")]
    hit = next((w for w in sorted(policy.click_confirm_words) if f" {w} " in words), None)
    if hit:
        return [Finding(Risk.MEDIUM, f"clicking “{label}” sends or changes something")]
    return []


def check_browser(tool: str, args: dict, policy: Policy) -> list[Finding]:
    """Checks for acting inside a web page. The arguments describe the real thing found on the page,
    filled in by the browser service; a call that lacks them was never checked against the page, so it is refused."""
    findings: list[Finding] = []
    hint = args.get("page_hint", "")
    if hint not in PAGE_HINTS:
        findings.append(Finding(Risk.CRITICAL, "the page type is not understood"))
    elif hint == "payment":
        findings.append(Finding(Risk.HIGH, "this is a payment page"))
    elif hint == "login":
        findings.append(Finding(Risk.MEDIUM, "this is a sign-in page"))

    if tool == "click_text":
        text, target = args.get("text"), args.get("target")
        if not isinstance(text, str) or _CONTROL_OR_NEWLINE.search(text):
            findings.append(Finding(Risk.CRITICAL, "text must be one line of text"))
        if not isinstance(target, str) or not target.strip():
            findings.append(Finding(Risk.CRITICAL, "the button was not identified on the page"))
        else:
            findings += check_click_target(target, policy)
    elif tool == "fill_field":
        kind, target, value = args.get("field_kind"), args.get("target"), args.get("value")
        if not isinstance(target, str) or not target.strip():
            findings.append(Finding(Risk.CRITICAL, "the box was not identified on the page"))
        elif kind != "text":
            findings.append(Finding(Risk.CRITICAL, "I never type into password, card or one-time-code boxes"))
        if not isinstance(value, str) or not value:
            findings.append(Finding(Risk.CRITICAL, "value must be text"))
    elif tool == "scroll_page" and args.get("direction") not in SCROLL_DIRECTIONS:
        findings.append(Finding(Risk.CRITICAL, "scroll direction must be up, down, top or bottom"))
    return findings


def check_args(tool: str, args: dict, policy: Policy) -> Finding | None:
    """The most serious problem with these arguments, or None."""
    findings: list[Finding] = []
    for name in SINGLE_LINE_ARGS & set(args):
        if not isinstance(args[name], str):
            findings.append(Finding(Risk.CRITICAL, f"{name} must be text"))
    for name, value in args.items():
        if not isinstance(value, str):
            continue
        pattern = _CONTROL_OR_NEWLINE if name in SINGLE_LINE_ARGS else _CONTROL
        if pattern.search(value):
            findings.append(Finding(Risk.CRITICAL, f"{name} contains control characters"))

    for name in ("nth", "tab_id"):
        if name in args and not _is_count(args[name]):
            findings.append(Finding(Risk.CRITICAL, f"{name} must be a number"))
    if "id" in args and (not _is_count(args["id"]) or args["id"] < 1):
        findings.append(Finding(Risk.CRITICAL, "id must be the number of an item"))

    if tool in ("open_file", "open_folder") and isinstance(args.get("path_or_name"), str):
        findings += check_path(args["path_or_name"], policy, opening_file=tool == "open_file")
    elif tool in ("open_url", "new_tab") and isinstance(args.get("url"), str):
        findings += check_url(args["url"], policy)
    elif tool in ("open_app", "focus_app", "close_app") and isinstance(args.get("app_name"), str):
        findings += check_app(tool, args["app_name"], policy)
    elif tool in ("click_text", "fill_field", "scroll_page"):
        findings += check_browser(tool, args, policy)
    elif tool == "set_preference":
        findings += check_preference(args)
    elif tool == "set_volume":
        level = args.get("level")
        if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 100:
            findings.append(Finding(Risk.CRITICAL, "volume must be a number from 0 to 100"))
    return _worst(findings)
