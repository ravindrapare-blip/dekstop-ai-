"""Check what the assistant can reach on this Mac, and preview how a name would be matched.

Usage: .venv/bin/python scripts/automation_check.py [--find "budget pdf"]
Nothing is opened or changed. Exit code 0 if apps and folders can be found.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from automation import build_actions  # noqa: E402
from backend.config import load_config  # noqa: E402


def show_matches(service, text: str) -> None:
    print(f"\nHow {text!r} would be matched (nothing is opened):")
    apps = service.apps.find(text)
    print(f"  apps:    {apps.kind}: " + (", ".join(a.name for a in apps.items) or "-"))
    for kind in ("file", "folder"):
        lookup = service.files.locate(text, kind)
        names = ", ".join(service.files.display(m.path) for m in lookup.matches) or "-"
        extra = " (stopped early)" if lookup.truncated else ""
        print(f"  {kind + 's:':8} {lookup.kind}: {names}{extra}")


def main() -> int:
    service = build_actions(load_config(), ROOT)
    status = service.status()
    ok = True

    if status["hammerspoon"]:
        print("[ok]   Hammerspoon is running and answering")
    else:
        print("[info] Hammerspoon is not answering. Apps still open (with `open` and AppleScript).")
        print("       To use Hammerspoon: scripts/setup_hammerspoon.sh, then reload its config.")

    if status["apps_found"]:
        print(f"[ok]   {status['apps_found']} apps found")
    else:
        ok = False
        print("[FAIL] no apps found in /Applications and the other standard folders")

    first = status["searched_first"]
    if first:
        print("[ok]   files are searched first in: " + ", ".join(Path(p).name for p in first))
    else:
        ok = False
        print("[FAIL] none of the folders in files.search_dirs exist inside your home folder")
    if status["searched_if_needed"]:
        home = str(Path.home().resolve())
        later = ["your home folder (top level only)" if p == home else Path(p).name for p in status["searched_if_needed"]]
        print("       then, if nothing is found: " + ", ".join(later))

    if "--find" in sys.argv:
        index = sys.argv.index("--find")
        if index + 1 < len(sys.argv):
            show_matches(service, sys.argv[index + 1])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
