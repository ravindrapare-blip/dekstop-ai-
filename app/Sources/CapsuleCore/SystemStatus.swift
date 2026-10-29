import Foundation

/// One part of the assistant as the backend reports it (`GET /status`): ok, warn, off or fail.
public struct ComponentStatus: Codable, Equatable {
    public let name: String
    public let state: String
    public let detail: String
    public let fix: String
    public let core: Bool

    public init(name: String, state: String, detail: String = "", fix: String = "", core: Bool = false) {
        self.name = name
        self.state = state
        self.detail = detail
        self.fix = fix
        self.core = core
    }
}

/// Everything the backend knows about what works right now. Extra fields in the answer are ignored.
public struct SystemStatus: Codable, Equatable {
    public let ready: Bool
    public let state: String          // "ready", "degraded" or "down"
    public let model: String
    public let components: [ComponentStatus]

    public init(ready: Bool, state: String, model: String = "", components: [ComponentStatus] = []) {
        self.ready = ready
        self.state = state
        self.model = model
        self.components = components
    }
}

public struct StatusSummary: Equatable {
    public let tone: Tone
    public let text: String
}

/// One line for the menu bar: whether the assistant can be used, and what needs attention.
public func summarize(_ status: SystemStatus) -> StatusSummary {
    let broken = status.components.filter { $0.state == "fail" || $0.state == "warn" }
    switch status.state {
    case "ready":
        return StatusSummary(tone: .success, text: "Everything is ready")
    case "degraded":
        let names = broken.prefix(3).map { $0.name }.joined(separator: ", ")
        let more = broken.count > 3 ? " and \(broken.count - 3) more" : ""
        return StatusSummary(tone: .warning, text: broken.isEmpty ? "Ready" : "Ready. Needs attention: \(names)\(more)")
    case "down":
        let core = status.components.filter { $0.core && $0.state == "fail" }
        let names = core.map { $0.name }.joined(separator: ", ")
        let fix = core.first(where: { !$0.fix.isEmpty })?.fix ?? ""
        return StatusSummary(tone: .error, text: "Not ready: \(names.isEmpty ? "see details" : names)" + (fix.isEmpty ? "" : ". \(fix)"))
    default:
        return StatusSummary(tone: .warning, text: "Status unknown")
    }
}

/// A short line per part, for the details list: "✓ memory: 2 reminders", "! Hammerspoon: ...".
public func line(for component: ComponentStatus) -> String {
    let mark: String
    switch component.state {
    case "ok": mark = "✓"
    case "warn": mark = "!"
    case "fail": mark = "✕"
    default: mark = "–"          // off, or anything new
    }
    let detail = component.detail.count > 70 ? String(component.detail.prefix(69)) + "…" : component.detail
    return "\(mark) \(component.name): \(detail)"
}
