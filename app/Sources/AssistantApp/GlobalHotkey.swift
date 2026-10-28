import Carbon.HIToolbox
import CapsuleCore

/// One system-wide shortcut, registered with Carbon's RegisterEventHotKey.
/// Unlike an NSEvent global monitor, this needs no Accessibility permission.
/// Registration fails (and `init` returns nil) if another app already owns the shortcut.
final class GlobalHotkey {
    private var hotKeyRef: EventHotKeyRef?
    private var handlerRef: EventHandlerRef?
    private let action: () -> Void

    init?(spec: HotkeySpec, action: @escaping () -> Void) {
        self.action = action

        var eventType = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed)
        )
        let userData = Unmanaged.passUnretained(self).toOpaque()
        let installed = InstallEventHandler(
            GetApplicationEventTarget(),
            { _, _, userData in
                guard let userData = userData else { return noErr }
                let hotkey = Unmanaged<GlobalHotkey>.fromOpaque(userData).takeUnretainedValue()
                hotkey.action()
                return noErr
            },
            1,
            &eventType,
            userData,
            &handlerRef
        )
        guard installed == noErr else { return nil }

        let hotKeyID = EventHotKeyID(signature: OSType(0x4153_5354), id: 1)  // 'ASST'
        let registered = RegisterEventHotKey(
            spec.keyCode,
            spec.modifiers,
            hotKeyID,
            GetApplicationEventTarget(),
            0,
            &hotKeyRef
        )
        guard registered == noErr else {
            if let handler = handlerRef { RemoveEventHandler(handler) }
            return nil
        }
    }

    deinit {
        if let ref = hotKeyRef { UnregisterEventHotKey(ref) }
        if let handler = handlerRef { RemoveEventHandler(handler) }
    }
}
