# app/ — floating capsule UI

A small native macOS app: a dark pill at the top of the screen with a text box, a microphone button and suggestion chips. It shows what the assistant is doing and asks you before anything risky. It talks only to the Python backend at `127.0.0.1:8765`.

- Swift Package, **no third-party code, no Xcode project**. SwiftUI for the views, AppKit for the window, Carbon for the global shortcut.
- Targets macOS 12 (Monterey). Swift 5.7, which ships with Xcode 14.2 and the matching command line tools.
- Menu bar app: no Dock icon. Open it with **Cmd+Shift+Space** or the waveform icon in the menu bar.

## Build and run (on the Mac)

```bash
cd app
./build.sh            # builds build/Assistant.app
open build/Assistant.app
./build.sh test       # runs the unit tests
```

The backend must be running (`scripts/start.sh` from the project root). If it isn't, the capsule says so.

## What you see

| State | Look | Meaning |
|---|---|---|
| ready | grey dot, chips under the box | idle |
| listening | red dot, filled mic | recording; tap the mic again to send |
| transcribing | spinner | the recording is being turned into text |
| thinking | spinner | the local model is working |
| waiting | orange dot, Confirm / Cancel buttons | the safety layer wants your answer |
| working | spinner | an authorized action is running |
| done | green or orange dot, result text | finished, or refused |
| speaking | speaker icon | the result is being read aloud; Esc stops it |
| error | red dot | something failed, with a plain explanation |

"Last: ..." under the box names the previous action. Chips: Open App, Open File, Search Chrome, Play Media, Set Reminder fill the box so you can finish the sentence. Organize Desktop sends right away (it will still ask before moving anything).

The menu bar icon's menu has a **Status** line (and a submenu with one line per part) that shows what works right now: it is refreshed each time you open the menu. If the assistant isn't running it says so.

Keys: **Return** sends. **Esc** undoes the latest thing: cancels a question, stops waiting, stops listening, clears the text, and only then hides the pill. A high-risk question needs the "I understand the risk" box ticked before Confirm works.

## Settings (optional)

Copy `ui.example.json` to `~/Library/Application Support/LocalAssistant/ui.json`:

| Key | Default | Notes |
|---|---|---|
| `backend_url` | `http://127.0.0.1:8765` | must be an `http` address on this Mac; anything else is ignored |
| `hotkey` | `cmd+shift+space` | modifiers (`cmd`, `shift`, `opt`, `ctrl`) plus one letter, digit or `space` |
| `hide_after_seconds` | `20` | `0` turns auto-hide off; otherwise 3 to 600 |
| `speak_replies` | `voice` | `voice` reads replies aloud only for spoken requests, `always`, or `never` |

Bad values fall back to the default and the capsule shows a warning at launch. If another app already owns the shortcut, the menu bar item still works.

## How it is organized

```
Sources/CapsuleCore/   all the logic, Foundation only, unit tested
  CapsuleController    typed and spoken requests, confirmation, cancel, fallback to typing
  CapsuleState         the states and the pure function that moves between them
  Planning             turns a backend answer into one step; never trusts an inconsistent answer
  BackendClient        the only network code; loopback addresses only
  VoiceInput, Speech   microphone and speaker logic: silence detection, permissions, backend calls
  Ports                the interfaces for microphone, speaker, voice and actions, with stand-ins
  CapsuleLayout, IdlePolicy, Hotkey, Suggestions, UIConfig, Models
Sources/AssistantApp/  thin AppKit and SwiftUI layer: window, view, shortcut, menu bar item,
                       MicRecorder and AudioPlayer (AVFoundation)
Tests/                 unit tests, plus JSON fixtures captured from the real backend
```

The UI contains no rules. It sends text to `/route`, shows what the safety layer decided, and sends your Confirm or Cancel to `/confirm`. It only hands an action to the runner when the backend says `authorized: true`.

## Voice

The mic button records, transcribes with whisper.cpp through the backend, and sends the words like a typed request. Replies to spoken requests are read aloud (Piper, or the Mac voice). Setup, permission, privacy and troubleshooting are in `../docs/voice.md`. If voice fails for any reason the capsule says why and you type instead.

Voice needs the engines installed on the Mac (`scripts/voice_check.py` tells you what is missing).

## Actions and choices

The app does not run anything. The backend finds the real app or file, asks the safety layer, and only then does it, and the app shows what happened ("Opened Google Chrome.", "Asked Safari to quit."). Details: `../docs/actions.md`.

- If several apps or files could match ("report.pdf" in Documents and in Downloads), the capsule lists them with their locations. Click one, or "None of these". Esc cancels.
- A name that cannot be found is explained, not guessed.
- The app only believes a result that fits the safety decision beside it: "done" without an authorizing decision is shown as an error.

## Not connected yet

- **A sign-in page:** when a page needs you to sign in, the capsule says so and waits (it shows "waiting for you to sign in"). Sign in yourself in Chrome, then ask it to continue. The assistant never types passwords.
- **Reminders, tasks, notes and preferences** are answered in the capsule (the lines of a list are in the result); reminders also appear as macOS notifications.
- **Play Media** has no matching tool yet, so the assistant will answer that it can't.
- **Answering confirmations by voice:** you still click Confirm or Cancel.

## Low resource use

One window, a solid fill (no blur), no continuous animation, no polling. The idle timer is a single one-shot timer. The only background request is one health check each time the pill opens. Expect a few tens of MB.

## First run on the Mac: checklist

These need a real Mac and could not be tested where this was written:

1. `./build.sh test` passes.
2. `./build.sh`, then `open build/Assistant.app`: the pill appears at the top of the screen.
3. Cmd+Shift+Space hides and shows it. Menu bar icon works too.
4. Type, press Return: "Thinking…", then an answer. With the backend stopped you get the "start it with scripts/start.sh" message.
5. Ask it to quit Safari: Confirm / Cancel appear. Cancel says "Cancelled".
6. Click a chip: text appears in the box with the cursor at the end, ready to type.
7. Mic button, before voice is set up: shows a clear message and moves to the text box. After setup (`../docs/voice.md`): macOS asks for the microphone, you speak, tap again, and it answers; the reply is read aloud.
8. With `hide_after_seconds` set to 5, the pill hides itself after 5 idle seconds, but not while it is thinking.
9. After hiding with Cmd+Shift+Space, keyboard focus returns to the app you were using.
10. Voice: deny the microphone prompt once and check the message; hide the pill while listening and confirm the recording stops; press Esc while it speaks and confirm it stops.

Send me the first thing that doesn't match and I'll fix it.
