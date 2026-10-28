# app/

SwiftUI floating assistant panel (phase 5).

- A small always-on-top panel with a text field, a push-to-talk button, a status line and the approval dialog.
- Talks only to the backend at `127.0.0.1:8765`. It holds no logic of its own.
- Built as an AppKit `NSPanel` / `NSStatusItem` shell hosting SwiftUI views, because `MenuBarExtra` needs macOS 13 and the target is macOS 12.
- Xcode 14.2 is the last version that runs on Monterey.
- Approval dialogs show the exact action and arguments and time out to deny.

Phase 0 contains no Swift code.
