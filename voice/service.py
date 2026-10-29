"""The voice service the backend uses: transcribe a recording, speak a reply."""
import logging
import re
import threading
import time
from pathlib import Path

from voice.audio import MIN_AUDIO_SECONDS, wav_duration
from voice.base import VoiceError
from voice.stt import MacDictation, WhisperTranscriber
from voice.tts import PiperSynthesizer, SaySynthesizer

log = logging.getLogger("assistant.voice")

DEFAULT_MAX_TEXT_CHARS = 500
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def prepare_text(text: str, max_chars: int = DEFAULT_MAX_TEXT_CHARS) -> str:
    """Make a reply safe and short enough to speak: no control characters, one line, capped."""
    one_line = " ".join(_CONTROL.sub(" ", text).split())
    if len(one_line) <= max_chars:
        return one_line
    cut = one_line[:max_chars]
    sentence_end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if sentence_end > max_chars // 2:
        return cut[: sentence_end + 1]
    return cut.rsplit(" ", 1)[0] + "…"


class VoiceService:
    def __init__(self, stt, synthesizers: list, *, enabled: bool = True, max_text_chars: int = DEFAULT_MAX_TEXT_CHARS):
        self.stt = stt
        self.synthesizers = synthesizers
        self.enabled = enabled
        self.max_text_chars = max_text_chars
        # One speech job at a time keeps a dual-core Mac responsive.
        self._lock = threading.Lock()

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise VoiceError("disabled", "Voice is turned off.", "Set voice.enabled: true in config.yaml.")

    def status(self) -> dict:
        stt = self.stt.availability()
        engines = [{"engine": s.name, **s.availability().to_dict()} for s in self.synthesizers]
        return {
            "enabled": self.enabled,
            "stt": {"engine": self.stt.name, "external": bool(getattr(self.stt, "external", False)), **stt.to_dict()},
            "tts": {"available": any(e["available"] for e in engines), "engines": engines},
        }

    def transcribe(self, wav: bytes) -> str:
        self._require_enabled()
        if wav_duration(wav) < MIN_AUDIO_SECONDS:   # also rejects anything that is not usable audio
            return ""
        with self._lock:
            started = time.monotonic()
            text = self.stt.transcribe(wav)
        log.info("transcribed %d bytes in %.1fs (%d characters)", len(wav), time.monotonic() - started, len(text))
        return text

    def speak(self, text: str) -> bytes:
        """WAV bytes for the reply. Tries each engine in order and uses the first that works."""
        self._require_enabled()
        spoken = prepare_text(text, self.max_text_chars)
        if not spoken:
            raise VoiceError("bad_text", "There is nothing to say.")
        last_error: VoiceError | None = None
        with self._lock:
            for engine in self.synthesizers:
                started = time.monotonic()
                try:
                    wav = engine.synthesize(spoken)
                except VoiceError as e:
                    log.warning("%s could not speak (%s)", engine.name, e.code)
                    last_error = last_error or e   # report the preferred engine's problem
                    continue
                log.info("%s spoke %d characters in %.1fs", engine.name, len(spoken), time.monotonic() - started)
                return wav
        raise last_error or VoiceError("engine_missing", "No voice output is available.")


def voice_service_from_config(cfg: dict, root: Path) -> VoiceService:
    v = cfg.get("voice", {})
    w, p = v.get("whisper", {}), v.get("piper", {})
    timeout = p.get("timeout_seconds", 30)
    piper = PiperSynthesizer(p.get("binary", "piper"), p.get("voice", "data/models/en_US-lessac-low.onnx"), root, timeout=timeout)
    say = SaySynthesizer(timeout=timeout)
    if v.get("tts_engine", "piper") == "say":                      # TTS_ENGINE=say: only the Mac's own voice
        synthesizers: list = [say]
    else:                                                          # the default: Piper, then the Mac's voice if Piper isn't set up
        synthesizers = [piper] + ([say] if v.get("system_voice_fallback", True) else [])
    if v.get("stt_engine", "whisper") == "macos":                  # STT_ENGINE=macos: macOS Dictation types into the text box
        return VoiceService(MacDictation(), synthesizers, enabled=v.get("enabled", True),
                            max_text_chars=v.get("max_text_chars", DEFAULT_MAX_TEXT_CHARS))
    stt = WhisperTranscriber(
        w.get("binary", "reference/whisper.cpp/build/bin/whisper-cli"),
        w.get("model", "reference/whisper.cpp/models/ggml-tiny.en.bin"),
        root,
        threads=w.get("threads", 2),
        language=w.get("language", "en"),
        timeout=w.get("timeout_seconds", 60),
    )
    return VoiceService(
        stt, synthesizers,
        enabled=v.get("enabled", True),
        max_text_chars=v.get("max_text_chars", DEFAULT_MAX_TEXT_CHARS),
    )
