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
| listening | red dot, filled mic | voice input is on |
| thinking | spinner | the local model is working |
| waiting | orange dot, Confirm / Cancel buttons | the safety layer wants your answer |
| working | spinner | an authorized action is running |
| done | green or orange dot, result text | finished, or refused |
| error | red dot | something failed, with a plain explanation |

"Last: ..." under the box names the previous action. Chips: Open App, Open File, Search Chrome, Play Media, Set Reminder fill the box so you can finish the sentence. Organize Desktop sends right away (it will still ask before moving anything).

Keys: **Return** sends. **Esc** undoes the latest thing: cancels a question, stops waiting, stops listening, clears the text, and only then hides the pill. A high-risk question needs the "I understand the risk" box ticked before Confirm works.

## Settings (optional)

Copy `ui.example.json` to `~/Library/Application Support/LocalAssistant/ui.json`:

| Key | Default | Notes |
|---|---|---|
| `backend_url` | `http://127.0.0.1:8765` | must be an `http` address on this Mac; anything else is ignored |
| `hotkey` | `cmd+shift+space` | modifiers (`cmd`, `shift`, `opt`, `ctrl`) plus one letter, digit or `space` |
| `hide_after_seconds` | `20` | `0` turns auto-hide off; otherwise 3 to 600 |

Bad values fall back to the default and the capsule shows a warning at launch. If another app already owns the shortcut, the menu bar item still works.

## How it is organized

```
Sources/CapsuleCore/   all the logic, Foundation only, unit tested
  CapsuleController    typed and spoken requests, confirmation, cancel, fallback to typing
  CapsuleState         the states and the pure function that moves between them
  Planning             turns a backend decision into one step; never trusts an inconsistent answer
  BackendClient        the only network code; loopback addresses only
  Ports                VoiceInput and ActionRunner interfaces, with "unavailable" stand-ins
  CapsuleLayout, IdlePolicy, Hotkey, Suggestions, UIConfig, Models
Sources/AssistantApp/  thin AppKit and SwiftUI layer: window, view, shortcut, menu bar item
Tests/                 unit tests, plus JSON fixtures captured from the real backend
```

The UI contains no rules. It sends text to `/route`, shows what the safety layer decided, and sends your Confirm or Cancel to `/confirm`. It only hands an action to the runner when the backend says `authorized: true`.

## Not connected yet

- **Voice:** the mic button reports "Voice input isn't set up yet" and moves focus to the text box. Phase 4 supplies a real `VoiceInput`.
- **Running actions:** authorized actions go to a runner that says "nothing was done". Phase 5 onward supplies a real `ActionRunner`. The capsule never claims an action happened when it didn't.
- **Play Media** has no matching tool yet, so the assistant will answer that it can't.

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
7. Mic button: shows the "isn't set up yet" message.
8. With `hide_after_seconds` set to 5, the pill hides itself after 5 idle seconds, but not while it is thinking.
9. After hiding with Cmd+Shift+Space, keyboard focus returns to the app you were using.

Send me the first thing that doesn't match and I'll fix it.
