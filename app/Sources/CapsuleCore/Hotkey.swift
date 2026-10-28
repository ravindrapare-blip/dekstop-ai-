import Foundation

/// A global shortcut such as "cmd+shift+space". The numbers are the Carbon virtual key codes
/// and modifier masks that RegisterEventHotKey expects, so the UI needs no lookup of its own.
public struct HotkeySpec: Equatable {
    public let keyCode: UInt32
    public let modifiers: UInt32
    public let text: String

    public static let `default` = HotkeySpec(keyCode: 49, modifiers: 256 | 512, text: "cmd+shift+space")

    private static let modifierMasks: [String: UInt32] = [
        "cmd": 256, "command": 256,
        "shift": 512,
        "opt": 2048, "option": 2048, "alt": 2048,
        "ctrl": 4096, "control": 4096,
    ]

    private static let keyCodes: [String: UInt32] = [
        "space": 49,
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
        "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "o": 31, "u": 32, "i": 34, "p": 35,
        "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
        "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28, "9": 25, "0": 29,
    ]

    /// Returns nil unless the text is one or more modifiers plus exactly one key.
    /// A shortcut with no modifier is refused, because it would steal that key everywhere.
    public static func parse(_ text: String) -> HotkeySpec? {
        let parts = text.lowercased()
            .split(separator: "+")
            .map { $0.trimmingCharacters(in: .whitespaces) }
        guard parts.count >= 2, !parts.contains("") else { return nil }

        var modifiers: UInt32 = 0
        var keys: [UInt32] = []
        for part in parts {
            if let mask = modifierMasks[part] {
                modifiers |= mask
            } else if let code = keyCodes[part] {
                keys.append(code)
            } else {
                return nil
            }
        }
        guard modifiers != 0, keys.count == 1 else { return nil }
        return HotkeySpec(keyCode: keys[0], modifiers: modifiers, text: parts.joined(separator: "+"))
    }
}
