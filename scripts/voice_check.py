"""Check the speech engines, then run a round trip: speak a sentence and transcribe it back.

Usage: .venv/bin/python scripts/voice_check.py [--no-roundtrip]
Exit code 0 only if speech to text and text to speech both work.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import load_config  # noqa: E402
from voice import VoiceError, voice_service_from_config  # noqa: E402
from voice.audio import to_16k_mono  # noqa: E402

SENTENCE = "Testing one two three."


def line(ok: bool, name: str, reason: str, fix: str) -> None:
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + ("" if ok else f": {reason}"))
    if not ok and fix:
        print(f"       fix: {fix}")


def main() -> int:
    service = voice_service_from_config(load_config(), ROOT)
    status = service.status()
    if not status["enabled"]:
        print("[FAIL] voice is turned off. Set voice.enabled: true in config.yaml.")
        return 1

    stt = status["stt"]
    line(stt["available"], f"speech to text ({stt['engine']})", stt["reason"], stt["fix"])
    tts_ok = status["tts"]["available"]
    for engine in status["tts"]["engines"]:
        line(engine["available"], f"text to speech ({engine['engine']})", engine["reason"], engine["fix"])
    if not tts_ok:
        print("[FAIL] no text to speech engine is available")
    if not (stt["available"] and tts_ok):
        return 1
    if "--no-roundtrip" in sys.argv:
        return 0

    print(f"\nRound trip: speaking {SENTENCE!r}, then transcribing it back...")
    try:
        started = time.monotonic()
        audio = service.speak(SENTENCE)
        spoke = time.monotonic() - started
        started = time.monotonic()
        heard = service.transcribe(to_16k_mono(audio))
        listened = time.monotonic() - started
    except VoiceError as e:
        print(f"[FAIL] {e.detail}")
        return 1
    print(f"       speaking took {spoke:.1f}s, transcribing took {listened:.1f}s")
    print(f"       heard: {heard!r}")
    if "testing" in heard.lower():
        print("[ok] round trip works")
        return 0
    print("[FAIL] the transcript does not match; check the model file and your speakers or voice")
    return 1


if __name__ == "__main__":
    sys.exit(main())
