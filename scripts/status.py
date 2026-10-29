"""Show what works right now, as a traffic-light list (the same information as the menu bar item).

Usage: .venv/bin/python scripts/status.py
Asks the running backend (GET /status). If it isn't running, checks the same things directly.
Exit code 0 if the assistant is ready or degraded (usable), 1 if it is down.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import load_config  # noqa: E402

MARKS = {"ok": "✓", "warn": "!", "off": "–", "fail": "✕"}


def from_backend(cfg: dict) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cfg['backend']['port']}/status", timeout=15) as resp:
            return json.loads(resp.read().decode())
    except (OSError, ValueError):
        return None


def locally(cfg: dict) -> dict:
    """The same report without a running backend. Reminders can't be 'watching' yet, so that line is left out."""
    from automation.token import ensure_token  # noqa: F401
    from backend.actions import build_router
    from backend.llm import client_from_config
    from backend.main import build_guard
    from backend.startup import run_checks
    from backend.status import collect, overall
    from voice import voice_service_from_config

    llm = client_from_config(cfg)
    try:
        results = run_checks(llm)
        components = [c for c in collect(model_checks=results, guard=build_guard(cfg), actions=build_router(cfg, ROOT),
                                         voice=voice_service_from_config(cfg, ROOT)) if c.name != "reminders"]
    finally:
        llm.close()
    state = overall(components)
    return {"ready": state != "down", "state": state, "model": llm.model, "components": [c.to_dict() for c in components]}


def main() -> int:
    cfg = load_config()
    report = from_backend(cfg)
    source = "the running backend"
    if report is None:
        report, source = locally(cfg), "a direct check (the backend is not running; start it with scripts/start.sh)"
    verdict = {"ready": "READY", "degraded": "READY, with parts that need attention", "down": "NOT READY"}.get(report["state"], report["state"])
    print(f"Assistant: {verdict}   [{source}]\n")
    width = max(len(c["name"]) for c in report["components"])
    for c in report["components"]:
        print(f" {MARKS.get(c['state'], '?')} {c['name']:<{width}}  {c['detail']}")
        if c["state"] in ("warn", "fail") and c["fix"]:
            print(f"   {'':<{width}}  fix: {c['fix']}")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
