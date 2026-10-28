# voice/

Local speech engines. They convert; the app records and plays (macOS ties the microphone permission to the app). Setup and troubleshooting: `docs/voice.md`.

| File | Job |
|---|---|
| `stt.py` | whisper.cpp (`whisper-cli`) as a child process: WAV in, text out |
| `tts.py` | Piper, or the macOS `say` voice, as child processes: text in, WAV out |
| `audio.py` | checks recordings (16 kHz, 16-bit, mono, length) and converts audio for the self-test |
| `service.py` | what the backend calls: validation, text cleaning and shortening, engine fallback |
| `base.py` | errors with codes and fixes, and path lookup |

Rules the code follows:

- Text for speech goes in on stdin, never on the command line. Commands are lists, never shell strings.
- Audio is written to a private temporary folder that is deleted afterwards, even on errors.
- One speech job at a time, with timeouts, so a dual-core Mac stays responsive.
- Transcripts and spoken text are never logged.
- Every failure becomes a `VoiceError` with a short message and a fix; the HTTP layer and the app turn those into plain explanations.
