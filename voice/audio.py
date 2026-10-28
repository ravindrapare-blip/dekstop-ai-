"""Checks on audio coming from the app. whisper.cpp needs 16 kHz, 16-bit, mono WAV."""
import io
import sys
import wave
from array import array

from voice.base import VoiceError

MAX_AUDIO_BYTES = 3_000_000   # about 90 seconds at 16 kHz mono; the app stops recording at 15
MAX_AUDIO_SECONDS = 30
MIN_AUDIO_SECONDS = 0.3       # shorter than this is a tap, not speech
SAMPLE_RATE = 16_000


def wav_duration(data: bytes) -> float:
    """Validate a WAV for whisper.cpp and return its length in seconds."""
    if len(data) > MAX_AUDIO_BYTES:
        raise VoiceError("too_large", "That recording is too large.")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError):
        raise VoiceError("bad_audio", "That is not a valid WAV recording.") from None
    if (channels, width, rate) != (1, 2, SAMPLE_RATE):
        raise VoiceError(
            "bad_audio",
            f"Audio must be 16 kHz, 16-bit, mono (got {rate} Hz, {width * 8}-bit, {channels} channel(s)).",
        )
    duration = frames / rate
    if duration > MAX_AUDIO_SECONDS:
        raise VoiceError("too_large", f"That recording is longer than {MAX_AUDIO_SECONDS} seconds.")
    return duration


def is_playable_wav(data: bytes) -> bool:
    """A synthesized reply must be a readable WAV with some audio in it."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.getnframes() > 0
    except (wave.Error, EOFError):
        return False


def to_16k_mono(wav: bytes) -> bytes:
    """Convert 16-bit WAV audio to the 16 kHz mono format whisper.cpp needs (used by the self-test).

    Linear interpolation is plenty for speech. Uses only the standard library."""
    try:
        with wave.open(io.BytesIO(wav), "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        raise VoiceError("bad_audio", "That is not a valid WAV file.") from None
    if width != 2:
        raise VoiceError("bad_audio", "Only 16-bit audio can be converted.")
    samples = array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % (2 * channels)])
    if sys.byteorder == "big":
        samples.byteswap()
    if channels > 1:
        samples = array("h", (sum(samples[i:i + channels]) // channels for i in range(0, len(samples), channels)))
    if rate != SAMPLE_RATE and samples:
        step = rate / SAMPLE_RATE
        last = len(samples) - 1
        converted = array("h")
        for i in range(int(len(samples) / step)):
            pos = i * step
            j = int(pos)
            a, b = samples[j], samples[min(j + 1, last)]
            converted.append(int(a + (b - a) * (pos - j)))
        samples = converted
    if sys.byteorder == "big":
        samples.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    return out.getvalue()
