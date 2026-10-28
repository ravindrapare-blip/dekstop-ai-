import Foundation

/// Decides when the window may hide itself. There is no polling: the window arms one timer
/// for `hideAfter` seconds after each interaction and asks this policy when it fires.
public struct IdlePolicy: Equatable {
    /// Seconds of inactivity before hiding. 0 turns auto-hide off.
    public let hideAfter: TimeInterval

    public init(hideAfter: TimeInterval) {
        self.hideAfter = max(0, hideAfter)
    }

    public var isEnabled: Bool { hideAfter > 0 }

    public func shouldHide(phase: Phase, lastActivity: Date, now: Date) -> Bool {
        guard isEnabled, !phase.isBusy else { return false }
        return now.timeIntervalSince(lastActivity) >= hideAfter
    }
}
