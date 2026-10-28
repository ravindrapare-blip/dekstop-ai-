"""Check that Chrome control is ready, and optionally try it.

Usage: .venv/bin/python scripts/browser_check.py            # checks only; nothing is started
       .venv/bin/python scripts/browser_check.py --launch   # also starts the helper and opens Chrome, then closes it
Add --headless to --launch to try it without showing a window. Exit code 0 if everything needed is in place.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from automation.token import ensure_token  # noqa: E402
from backend.config import load_config  # noqa: E402
from browserctl.factory import build_browser  # noqa: E402

PINNED = "23.11.1"


def node_version(node: str) -> tuple[int, ...] | None:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)", out.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def main() -> int:
    cfg = load_config()
    b = cfg.get("browser", {})
    ok = True

    if not b.get("enabled", True):
        print("[info] browser.enabled is false in config.yaml, so browser actions are switched off")

    node = b.get("node", "node")
    version = node_version(node) if shutil.which(node) or Path(node).exists() else None
    if version is None:
        ok = False
        print("[FAIL] Node.js not found. Install Node 18 or newer from nodejs.org (or set browser.node in config.yaml).")
    elif version[0] < 18:
        ok = False
        print(f"[FAIL] Node {'.'.join(map(str, version))} is too old; the browser helper needs 18 or newer.")
    else:
        print(f"[ok]   Node {'.'.join(map(str, version))}")

    package = ROOT / "browser" / "node_modules" / "puppeteer-core" / "package.json"
    if package.exists():
        found = json.loads(package.read_text()).get("version")
        print(f"[ok]   puppeteer-core {found}" + ("" if found == PINNED else f" (expected {PINNED})"))
    else:
        ok = False
        print("[FAIL] the browser helper's packages are not installed. Run:  cd browser && npm install")

    chrome = Path(b.get("chrome_path", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    if chrome.exists():
        print(f"[ok]   Chrome found at {chrome}")
    else:
        ok = False
        print(f"[FAIL] Chrome not found at {chrome}. Install Google Chrome or set browser.chrome_path in config.yaml.")

    print(f"[info] the assistant's own Chrome profile is {ROOT / b.get('profile_dir', 'data/chrome-profile')} (sign in to your sites there once)")

    if "--launch" not in sys.argv:
        return 0 if ok else 1
    if not ok:
        print("[skip] not trying to launch until the problems above are fixed")
        return 1

    ensure_token(ROOT / cfg.get("automation", {}).get("token_file", "data/.token"))
    service = build_browser(cfg, ROOT)
    if "--headless" in sys.argv:
        service.launcher.extra_args = (*service.launcher.extra_args, "--headless")
    try:
        for tool, args in (("open_chrome", {}), ("open_url", {"url": "example.com"}), ("get_page_title", {}), ("get_current_url", {})):
            result = service.execute(tool, args)
            print(f"[{'ok' if result.status == 'done' else 'FAIL'}]   {tool}: {result.message}")
            ok = ok and result.status == "done"
    finally:
        service.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
