# voice/

Local speech, phase 3.

- **Speech to text:** `whisper.cpp` (`whisper-cli`), CPU build, `tiny.en` or `base.en`, 2 threads. Started per utterance so it uses no memory while idle.
- **Text to speech:** `piper` with a small voice. Started per reply.
- Both run as child processes of the backend. Nothing is imported from `reference/`.
- Speech and LLM generation do not run at the same time.

Paths and settings are in the `voice:` section of `config.yaml`. Phase 0 contains no code here.
