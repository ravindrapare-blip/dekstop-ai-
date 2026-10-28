"""Text to speech: Piper first, the built-in macOS `say` voice as a fallback. Both write a WAV file.

The text is always sent on stdin and never as a command-line argument, so it can never be read
as an option or as a shell command.
"""
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from voice.audio import is_playable_wav
from voice.base import Availability, VoiceError, find_executable, resolve_path, sentence

log = logging.getLogger("assistant.voice")


class _Synthesizer:
    name = ""

    def __init__(self, timeout: float, runner) -> None:
        self.timeout = timeout
        self._run = runner

    def availability(self) -> Availability:
        raise NotImplementedError

    def _command(self, out_file: Path) -> list[str]:
        raise NotImplementedError

    def synthesize(self, text: str) -> bytes:
        status = self.availability()
        if not status.ok:
            raise VoiceError(status.code, sentence(status.reason), status.fix)
        with tempfile.TemporaryDirectory(prefix="assistant-tts-") as tmp:
            out_file = Path(tmp) / "speech.wav"
            try:
                proc = self._run(
                    self._command(out_file),
                    input=text, capture_output=True, text=True, timeout=self.timeout,
                )
            except subprocess.TimeoutExpired:
                raise VoiceError("timeout", f"{self.name} took too long to speak.") from None
            except OSError as e:
                log.warning("could not start %s: %s", self.name, e)
                raise VoiceError("failed", f"{self.name} could not start.") from None
            if proc.returncode != 0:
                log.warning("%s exited with %s: %s", self.name, proc.returncode, (proc.stderr or "")[-300:])
                raise VoiceError("failed", f"{self.name} failed.")
            data = out_file.read_bytes() if out_file.is_file() else b""
        if not is_playable_wav(data):
            raise VoiceError("failed", f"{self.name} produced no audio.")
        return data


class PiperSynthesizer(_Synthesizer):
    name = "Piper"

    def __init__(self, binary: str, voice: str, root: Path, *, timeout: float = 30.0, runner=subprocess.run):
        super().__init__(timeout, runner)
        self.binary, self.voice, self.root = binary, voice, root

    def availability(self) -> Availability:
        if find_executable(self.binary, self.root) is None:
            return Availability(
                False, f"Piper was not found ({self.binary})", "engine_missing",
                "Install a Piper release for macOS and put it on your PATH (see docs/voice.md).",
            )
        model = resolve_path(self.voice, self.root)
        if not model.is_file() or not Path(str(model) + ".json").is_file():
            return Availability(
                False, f"the Piper voice {self.voice} is missing (it needs its .json file too)", "model_missing",
                "Download a voice (.onnx and .onnx.json) into data/models/ (see docs/voice.md).",
            )
        return Availability(True)

    def _command(self, out_file: Path) -> list[str]:
        return [
            find_executable(self.binary, self.root),
            "--model", str(resolve_path(self.voice, self.root)),
            "--output_file", str(out_file),
            "--quiet",
        ]


class SaySynthesizer(_Synthesizer):
    """The macOS built-in voice. Nothing to install, but less natural than Piper."""

    name = "the macOS voice"

    def __init__(self, *, timeout: float = 30.0, runner=subprocess.run, platform: str | None = None):
        super().__init__(timeout, runner)
        self.platform = platform or sys.platform

    def availability(self) -> Availability:
        if self.platform != "darwin" or find_executable("say", Path("/")) is None:
            return Availability(False, "the macOS voice is only available on a Mac", "engine_missing")
        return Availability(True)

    def _command(self, out_file: Path) -> list[str]:
        return [
            find_executable("say", Path("/")),
            "-o", str(out_file),
            "--file-format=WAVE", "--data-format=LEI16@22050",
            "-f", "-",       # read the text from stdin
        ]
