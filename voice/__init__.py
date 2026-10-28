"""Local speech: whisper.cpp (speech to text) and Piper (text to speech). Recording and playback are
done by the app, because the microphone permission belongs to the app; this package only converts."""
from voice.base import Availability, VoiceError
from voice.service import VoiceService, voice_service_from_config

__all__ = ["Availability", "VoiceError", "VoiceService", "voice_service_from_config"]
