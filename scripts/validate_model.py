"""Check that the configured model picks the right tool, and measure its speed.

Usage: .venv/bin/python scripts/validate_model.py [--model NAME] [--min-accuracy 0.7]
Needs Ollama running with the model pulled. Prints speed so you can compare models on your Mac.
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import load_config  # noqa: E402
from backend.llm import OllamaError, client_from_config  # noqa: E402
from backend.router import Router  # noqa: E402
from backend.startup import all_ok, run_checks  # noqa: E402
from backend.tool_defs import build_registry  # noqa: E402

CASES = [
    ("open chrome", "open_app"),
    ("open my downloads folder", "open_folder"),
    ("find the pdf called budget", "search_files"),
    ("search google for ai tools", "search_chrome"),
    ("remind me at 6 pm to call mom", "create_reminder"),
    ("save a note called ideas: build a robot", "save_note"),
    ("what time is it", "get_time"),
    ("show my reminders", "list_reminders"),
    ("go to youtube.com", "open_url"),
    ("set the volume to 30", "set_volume"),
    ("delete everything in my documents folder", "reply"),
    ("tell me a joke", "reply"),
    ("open my gmail", "open_url"),
    ("open a new tab and go to calendar", "new_tab"),
    ("go to the tab with youtube", "switch_tab"),
    ("click the sign up button", "click_text"),
    ("type hello into the search box", "fill_field"),
    ("scroll down", "scroll_page"),
    ("open youtube and play lofi music", "play_youtube_video"),
    ("what is the title of this page", "get_page_title"),
    ("type my password into the login box", "reply"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="override ollama.model from config.yaml")
    ap.add_argument("--min-accuracy", type=float, default=0.7)
    args = ap.parse_args()

    cfg = load_config()
    if args.model:
        cfg["ollama"]["model"] = args.model
    llm = client_from_config(cfg)
    try:
        checks = run_checks(llm)
        if not all_ok(checks):
            for r in checks:
                if not r.ok:
                    print(f"[FAIL] {r.name}: {r.detail}\n       fix: {r.fix}")
            return 2

        router = Router(llm, build_registry(), max_retries=cfg.get("router", {}).get("max_retries", 1))
        print(f"model: {llm.model}  (the first request includes model load time)\n")
        correct = first_try = 0
        speeds, times = [], []
        for text, expected in CASES:
            start = time.monotonic()
            try:
                res = router.route(text)
            except OllamaError as e:
                print(f"[ERROR] {text!r}: {e}")
                return 2
            secs = time.monotonic() - start
            times.append(secs)
            good = res.call.tool == expected and not res.fallback
            correct += good
            first_try += (res.attempts == 1 and not res.fallback)
            if res.stats and res.stats.tokens_per_second:
                speeds.append(res.stats.tokens_per_second)
            mark = "ok  " if good else "MISS"
            print(f"[{mark}] {secs:5.1f}s  {text!r} -> {res.call.tool} (expected {expected})")

        n = len(CASES)
        accuracy = correct / n
        print(f"\ncorrect tool:        {correct}/{n} ({accuracy:.0%})")
        print(f"valid JSON 1st try:  {first_try}/{n}")
        print(f"average time:        {sum(times) / n:.1f}s per request")
        if speeds:
            print(f"generation speed:    {sum(speeds) / len(speeds):.1f} tokens/second")
        return 0 if accuracy >= args.min_accuracy else 1
    finally:
        llm.close()


if __name__ == "__main__":
    sys.exit(main())
