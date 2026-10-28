"""Speech to text with whisper.cpp, run as a child process only while a recording is transcribed."""
import logging
import re
import subprocess
import tempfile
from pathlib import Path

from voice.base import Availability, VoiceError, find_executable, resolve_path, sentence

log = logging.getLogger("assistant.voice")

# whisper.cpp writes tags like [BLANK_AUDIO] or (silence) when it hears no speech.
_NOISE_TAG = re.compile(
    r"[\[(*]\s*(?:blank[ _]audio|silence|music|applause|laughter|noise|inaudible)[^\])*]*[\])*]",
    re.IGNORECASE,
)


def clean_transcript(raw: str) -> str:
    return " ".join(_NOISE_TAG.sub(" ", raw).split())


class WhisperTranscriber:
    name = "whisper.cpp"

    def __init__(
        self,
        binary: str,
        model: str,
        root: Path,
        *,
        threads: int = 2,
        language: str = "en",
        timeout: float = 60.0,
        runner=subprocess.run,
    ) -> None:
        self.binary, self.model, self.root = binary, model, root
        self.threads, self.language, self.timeout = threads, language, timeout
        self._run = runner

    def availability(self) -> Availability:
        if find_executable(self.binary, self.root) is None:
            return Availability(
                False, f"whisper.cpp was not found at {self.binary}", "engine_missing",
                "Build it: cmake -B build && cmake --build build -j2 --config Release (see docs/voice.md).",
            )
        if not resolve_path(self.model, self.root).is_file():
            return Availability(
                False, f"the speech model {self.model} is missing", "model_missing",
                "Download one: sh models/download-ggml-model.sh tiny.en (see docs/voice.md).",
            )
        return Availability(True)

    def transcribe(self, wav: bytes) -> str:
        """Returns the spoken text, or "" if nothing intelligible was heard.
        The caller (VoiceService) has already checked that `wav` is 16 kHz, 16-bit, mono."""
        status = self.availability()
        if not status.ok:
            raise VoiceError(status.code, sentence(status.reason), status.fix)

        args = [
            find_executable(self.binary, self.root),
            "-m", str(resolve_path(self.model, self.root)),
            "-t", str(self.threads),
            "-l", self.language,
            "-nt", "-np",       # no timestamps, no progress output: just the words
        ]
        with tempfile.TemporaryDirectory(prefix="assistant-stt-") as tmp:
            audio = Path(tmp) / "audio.wav"
            audio.write_bytes(wav)   # removed with the folder, even on errors
            try:
                proc = self._run(
                    [*args, "-f", str(audio)],
                    capture_output=True, text=True, timeout=self.timeout, stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                raise VoiceError("timeout", "Speech recognition took too long.") from None
            except OSError as e:
                log.warning("could not start whisper.cpp: %s", e)
                raise VoiceError("failed", "Speech recognition could not start.") from None

        if proc.returncode != 0:
            log.warning("whisper.cpp exited with %s: %s", proc.returncode, (proc.stderr or "")[-300:])
            raise VoiceError("failed", "Speech recognition failed.")
        return clean_transcript(proc.stdout or "")
