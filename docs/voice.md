# Voice

Everything runs on this Mac. Nothing is sent anywhere else.

```
tap mic ─▶ app records (16 kHz WAV) ─▶ backend ─▶ whisper.cpp ─▶ text
                                                                   │
          speakers ◀─ app plays ◀─ backend ◀─ Piper ◀─ reply ◀─ /route ◀─ safety layer
```

- The **app** records and plays, because macOS ties the microphone permission to the app.
- The **backend** only converts: audio to text with whisper.cpp, text to audio with Piper (or the built-in Mac voice).
- A spoken request becomes ordinary text and goes through `/route` and the safety layer exactly like a typed one. Speaking does not give it extra rights.

## Using it

- Click the mic, speak, then click it again, or just pause for about a second and a half. It also stops by itself after 15 seconds, or after 6 seconds of silence.
- States: **Listening**, **Transcribing**, **Thinking**, **Working**, **Speaking**. Esc cancels whichever is active.
- By default replies are read aloud only when you spoke the request. Change this with `speak_replies` in `ui.json` (`voice`, `always` or `never`).
- Confirmation questions ("I can do this... Please confirm") are not read out. Answer them with the buttons.
- If voice fails for any reason, the capsule says why and moves the cursor to the text box. Typing always works.

## Setup

Run these in the project folder on the Mac. Check what is missing at any time with:

```bash
.venv/bin/python scripts/voice_check.py
```

**1. Speech to text: whisper.cpp**

```bash
cd reference/whisper.cpp
cmake -B build && cmake --build build -j2 --config Release
sh ./models/download-ggml-model.sh tiny.en
cd ../..
```

`tiny.en` is the smallest English model (about 75 MB on disk, 273 MB of RAM while running per whisper.cpp's README) and the right start for a 2017 Air. `base.en` is more accurate but slower. If you switch, change `voice.whisper.model` in `config.yaml`. The CPU build is correct for an Intel Mac; Metal and Core ML are for Apple silicon.

**2. Text to speech: Piper**

Piper is archived upstream (development moved to `OHF-Voice/piper1-gpl`), so verify these steps against what is currently available:

1. Install a Piper release for macOS and put the `piper` command on your PATH (or set `voice.piper.binary` in `config.yaml` to its path).
2. Download a voice: both the `.onnx` file and its `.onnx.json` file, for example `en_US-lessac-low`, into `data/models/`. The voice list is in `reference/piper/VOICES.md`.

**3. Nothing to install: the Mac voice**

If Piper is not installed, the backend falls back to the built-in macOS `say` voice. It sounds more robotic but works immediately. Turn the fallback off with `voice.system_voice_fallback: false`.

## Microphone permission

The first time you press the mic, macOS asks "Assistant would like to access the microphone". Click OK.

If you clicked Don't Allow, the capsule says so. Fix it in **System Preferences ▸ Security & Privacy ▸ Privacy ▸ Microphone**, tick **Assistant**, then press the mic again.

If a profile or parental control blocks the microphone, the capsule says that instead and nothing can be changed from the app.

The app is signed ad hoc by `build.sh`, so macOS may treat a rebuilt app as a new app and ask again. If the mic stops working after a rebuild, untick and re-tick Assistant in that list.

## Privacy

- Audio is recorded only while the capsule shows Listening, and only after you pressed the mic. Hiding the window stops recording.
- The recording is a temporary file that is deleted as soon as it has been read. The backend deletes its own copy after transcribing.
- Audio and text travel only between the app and the backend on `127.0.0.1`.
- What you say and what is spoken back are not written to the logs. Only sizes and timings are.

## Limits

- No wake word. You press the mic (or use the hotkey, then the mic).
- Recordings are capped at 15 seconds and replies read aloud at 300 characters.
- Apple's dictation is not used as a second engine. If whisper.cpp is unavailable, voice input reports that and you type instead.
- Speed has not been measured on your Air. `voice_check.py` prints how long speaking and transcribing took; send me the numbers if it feels slow.

## Troubleshooting

| Message | What to do |
|---|---|
| "Voice recognition isn't set up. Whisper.cpp was not found..." | Step 1 above. `voice_check.py` shows the exact path it looked in. |
| "...the speech model ... is missing" | Run the model download in step 1. |
| "Microphone access is off for Assistant" | Microphone permission section above. |
| "I couldn't start the microphone" | Another app may be using it, or no microphone is connected. |
| "I didn't catch that" | Nothing intelligible was heard. Speak closer, or lower the silence threshold (below). |
| "couldn't speak: voice output isn't set up" (next to Last:) | Install Piper (step 2) or leave the Mac voice fallback on. |
| Recording stops too early, or never stops on its own | The loudness threshold is `-35` dB in `SilenceDetector` (`app/Sources/CapsuleCore/VoiceInput.swift`). Lower it for a quiet voice, raise it for a noisy room. |
