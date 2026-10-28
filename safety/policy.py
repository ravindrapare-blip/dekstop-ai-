"""Load and validate policy.yaml."""
from dataclasses import dataclass
from pathlib import Path

import yaml

from safety.risk import Risk


class PolicyError(ValueError):
    """policy.yaml is missing something or contains a mistake."""


def _phrase(text) -> str:
    """Lowercase words separated by single spaces, as the click check compares them."""
    return " ".join("".join(c if c.isalnum() else " " for c in str(text).lower()).split())


@dataclass(frozen=True)
class Blocked:
    risk: Risk
    reason: str


@dataclass(frozen=True)
class Policy:
    rules: dict[str, Risk]
    blocked: dict[str, Blocked]
    protected_paths: tuple[str, ...]
    protected_apps: frozenset[str]
    risky_extensions: frozenset[str]
    sensitive_url_words: frozenset[str]
    click_high_words: frozenset[str] = frozenset()
    click_confirm_words: frozenset[str] = frozenset()

    @classmethod
    def load(cls, path: Path | str) -> "Policy":
        try:
            raw = yaml.safe_load(Path(path).read_text())
        except (OSError, yaml.YAMLError) as e:
            raise PolicyError(f"cannot read policy {path}: {e}") from e
        if not isinstance(raw, dict):
            raise PolicyError("policy must be a mapping")
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "Policy":
        try:
            rules = {str(k): Risk.from_name(v) for k, v in (raw.get("rules") or {}).items()}
            blocked = {
                str(k): Blocked(Risk.from_name(v["risk"]), str(v["reason"]))
                for k, v in (raw.get("blocked") or {}).items()
            }
        except (ValueError, KeyError, TypeError) as e:
            raise PolicyError(f"invalid rule: {e}") from e
        if not rules:
            raise PolicyError("policy has no rules")
        overlap = sorted(set(rules) & set(blocked))
        if overlap:
            raise PolicyError(f"{overlap[0]!r} is both allowed and blocked")
        return cls(
            rules=rules,
            blocked=blocked,
            protected_paths=tuple(str(p) for p in raw.get("protected_paths") or ()),
            protected_apps=frozenset(str(a).lower() for a in raw.get("protected_apps") or ()),
            risky_extensions=frozenset(str(e).lower() for e in raw.get("risky_extensions") or ()),
            sensitive_url_words=frozenset(str(w).lower() for w in raw.get("sensitive_url_words") or ()),
            click_high_words=frozenset(_phrase(w) for w in raw.get("click_high_words") or ()),
            click_confirm_words=frozenset(_phrase(w) for w in raw.get("click_confirm_words") or ()),
        )
