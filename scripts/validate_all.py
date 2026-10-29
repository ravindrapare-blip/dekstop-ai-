"""The final validation: does each part of the assistant really work on this computer?

Usage: .venv/bin/python scripts/validate_all.py [options]
  --deep         also ask the model to route "open chrome" (slow on an old Mac, but proves it can choose a tool)
  --notify       also send one real macOS notification
  --live         also open a folder in Finder (to prove the app tool really acts)
  --headless     run Chrome without a window for the browser check
  --only a,b     run only some checks: model, gemini, browser, ui, voice, reminders, safety, files, backend
  --list         list the checks and exit

Reminders and files are tried in a temporary folder, never your real data. The browser check opens Chrome (the
assistant's own profile) on a page from this computer, so it needs no internet. Checks that cannot run here say why.
Exit code 0 if nothing FAILED (skipped checks are listed, not hidden).
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import validate as v  # noqa: E402
from backend.config import load_config  # noqa: E402

NAMES = ["model", "gemini", "browser", "ui", "voice", "reminders", "safety", "files", "backend"]


def fetch(url: str):
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    args = sys.argv[1:]
    if "--list" in args:
        print("\n".join(NAMES))
        return 0
    only = args[args.index("--only") + 1].split(",") if "--only" in args and args.index("--only") + 1 < len(args) else NAMES
    unknown = [n for n in only if n not in NAMES]
    if unknown:
        print(f"Unknown check(s): {', '.join(unknown)}. Choose from: {', '.join(NAMES)}.")
        return 2

    cfg = load_config()
    results: list[v.Result] = []
    cleanup = []

    def run(name, check, *a, **kw):
        if name in only:
            if sys.stdout.isatty():
                print(f"... {name}", end="\r", flush=True)
            results.append(v.run_one(name, check, *a, **kw))
            r = results[-1]
            print(f"[{r.status:>4}] {r.name}: {r.detail}  ({r.seconds:.1f}s)")
            if r.status != v.OK and r.fix:
                print(f"       -> {r.fix}")

    try:
        if "model" in only:
            from backend.llm import client_from_config
            llm = client_from_config(cfg)
            cleanup.append(llm.close)
            run("model", v.check_model, llm, deep="--deep" in args)
        if "gemini" in only:
            from backend.brain import build_brain
            from backend.llm import client_from_config
            from backend.settings import apply_to_config, load_settings
            settings = load_settings(cfg)
            effective = apply_to_config(cfg, settings)
            local = client_from_config(effective)
            brain = build_brain(effective, settings, local)
            cleanup += [brain.close, local.close]
            for warning in settings.warnings:
                print(f"[warn] {warning}")
            run("gemini", v.check_gemini, brain)
        if "browser" in only:
            from automation.token import ensure_token
            from browserctl.factory import build_browser
            ensure_token(ROOT / cfg.get("automation", {}).get("token_file", "data/.token"))
            browser = build_browser(cfg, ROOT)
            extra = (["--headless"] if "--headless" in args else []) + (["--no-sandbox"] if hasattr(os, "getuid") and os.getuid() == 0 else [])
            if browser.launcher is not None:
                browser.launcher.extra_args = tuple(extra)
            cleanup.append(browser.close)                     # closes Chrome again
            run("browser", v.check_browser, browser)
        run("ui", v.check_ui, ROOT)
        if "voice" in only:
            from voice import voice_service_from_config
            run("voice", v.check_voice, voice_service_from_config(cfg, ROOT))
        run("reminders", v.check_reminders, notify="--notify" in args)
        if "safety" in only:
            from safety import Policy
            run("safety", v.check_safety, Policy.load(ROOT / cfg.get("safety", {}).get("policy_file", "safety/policy.yaml")))
        if "files" in only:
            from automation import build_actions
            run("files", v.check_files, build_actions(cfg, ROOT), live="--live" in args)
        run("backend", v.check_backend, f"http://127.0.0.1:{cfg['backend']['port']}", fetch=fetch)
    finally:
        for stop in cleanup:
            try:
                stop()
            except Exception:
                pass

    counts = {s: sum(r.status == s for r in results) for s in (v.OK, v.FAIL, v.SKIP)}
    print(f"\n{len(results)} checks: {counts[v.OK]} ok, {counts[v.SKIP]} skipped, {counts[v.FAIL]} failed")
    if counts[v.SKIP]:
        print("Skipped checks were NOT verified: " + ", ".join(r.name for r in results if r.status == v.SKIP))
    return 1 if counts[v.FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
