import Foundation

/// Settings for the capsule. Optional file: ~/Library/Application Support/LocalAssistant/ui.json
///
///     {"backend_url": "http://127.0.0.1:8765", "hotkey": "cmd+shift+space", "hide_after_seconds": 20,
///      "speak_replies": "voice"}
///
/// Anything missing or invalid falls back to the default, with a warning shown at launch.
public struct UIConfig: Equatable {
    public var backendURL: URL
    public var hotkey: HotkeySpec
    /// 0 turns auto-hide off.
    public var hideAfterSeconds: Double
    /// When replies are read aloud.
    public var speakReplies: SpeakMode

    public static let defaultBackendURL = URL(string: "http://127.0.0.1:8765")!
    public static let defaults = UIConfig(
        backendURL: defaultBackendURL, hotkey: .default, hideAfterSeconds: 20, speakReplies: .voiceOnly
    )
    public static let minHideSeconds: Double = 3
    public static let maxHideSeconds: Double = 600

    public static func parse(_ data: Data) -> (config: UIConfig, warnings: [String]) {
        var config = UIConfig.defaults
        var warnings: [String] = []
        guard let object = try? JSONDecoder().decode([String: JSONValue].self, from: data) else {
            return (config, ["ui.json is not a valid JSON object, so the defaults are used."])
        }

        if let value = object["backend_url"] {
            if let text = value.stringValue, let url = URL(string: text),
               url.scheme == "http", isLoopbackHost(url.host) {
                config.backendURL = url
            } else {
                warnings.append("backend_url must be an http address on this Mac (127.0.0.1). Using the default.")
            }
        }

        if let value = object["hotkey"] {
            if let text = value.stringValue, let spec = HotkeySpec.parse(text) {
                config.hotkey = spec
            } else {
                warnings.append("hotkey must be modifiers plus one key, like cmd+shift+space. Using the default.")
            }
        }

        if let value = object["hide_after_seconds"] {
            if let seconds = value.numberValue, seconds >= 0 {
                if seconds == 0 {
                    config.hideAfterSeconds = 0
                } else if seconds < minHideSeconds || seconds > maxHideSeconds {
                    config.hideAfterSeconds = min(max(seconds, minHideSeconds), maxHideSeconds)
                    warnings.append("hide_after_seconds must be 0 or between 3 and 600. It was adjusted.")
                } else {
                    config.hideAfterSeconds = seconds
                }
            } else {
                warnings.append("hide_after_seconds must be a number, 0 or more. Using the default.")
            }
        }

        if let value = object["speak_replies"] {
            if let text = value.stringValue, let mode = SpeakMode(rawValue: text) {
                config.speakReplies = mode
            } else {
                warnings.append("speak_replies must be \"voice\", \"always\" or \"never\". Using the default.")
            }
        }
        return (config, warnings)
    }

    public static func userConfigURL(fileManager: FileManager = .default) -> URL {
        let base = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory())
        return base.appendingPathComponent("LocalAssistant").appendingPathComponent("ui.json")
    }

    /// A missing file is normal and gives the defaults without a warning.
    public static func load(from url: URL = userConfigURL()) -> (config: UIConfig, warnings: [String]) {
        guard let data = try? Data(contentsOf: url) else { return (defaults, []) }
        return parse(data)
    }
}
