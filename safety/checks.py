"""Argument checks. They can raise a tool's risk but never lower it. Pure functions, no I/O."""
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from safety.policy import Policy
from safety.risk import Risk

# Args that name an app, path, address or query must be single-line, with no control characters.
SINGLE_LINE_ARGS = {"app_name", "path_or_name", "url", "query", "tab"}
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

    if tool in ("open_file", "open_folder") and isinstance(args.get("path_or_name"), str):
        findings += check_path(args["path_or_name"], policy, opening_file=tool == "open_file")
    elif tool == "open_url" and isinstance(args.get("url"), str):
        findings += check_url(args["url"], policy)
    elif tool in ("open_app", "focus_app", "close_app") and isinstance(args.get("app_name"), str):
        findings += check_app(tool, args["app_name"], policy)
    elif tool == "set_volume":
        level = args.get("level")
        if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 100:
            findings.append(Finding(Risk.CRITICAL, "volume must be a number from 0 to 100"))
    return _worst(findings)
