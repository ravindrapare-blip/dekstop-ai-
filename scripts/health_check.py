"""Check Ollama, the configured model and the backend.

Usage: .venv/bin/python scripts/health_check.py
Exit code 0 only if Ollama is running and the model is available.
"""
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import load_config  # noqa: E402
from backend.llm import client_from_config  # noqa: E402
from backend.startup import all_ok, run_checks  # noqa: E402


def backend_status(cfg: dict) -> str:
    url = f"http://127.0.0.1:{cfg['backend']['port']}/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return f"running ({resp.read().decode().strip()})"
    except OSError:
        return "not running (start it with scripts/start.sh)"


def main() -> int:
    cfg = load_config()
    llm = client_from_config(cfg)
    try:
        results = run_checks(llm)
    finally:
        llm.close()
    for r in results:
        print(f"[{'ok' if r.ok else 'FAIL'}] {r.name}: {r.detail}")
        if not r.ok and r.fix:
            print(f"       fix: {r.fix}")
    print(f"[info] backend: {backend_status(cfg)}")
    return 0 if all_ok(results) else 1


if __name__ == "__main__":
    sys.exit(main())
