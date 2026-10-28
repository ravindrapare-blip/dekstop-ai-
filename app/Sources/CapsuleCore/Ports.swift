import Foundation

// Interfaces to things the capsule does not own. Later phases plug real implementations in,
// so the UI never changes when voice or action execution arrives.

public enum VoiceEvent: Equatable {
    case transcript(String)
    case failed(String)
}

/// Microphone to text. Implementations may call `onEvent` from any thread.
public protocol VoiceInput: AnyObject {
    var isAvailable: Bool { get }
    var unavailableReason: String { get }
    func start(onEvent: @escaping (VoiceEvent) -> Void)
    func stop()
}

/// Used until the voice engine is built (phase 4). The UI falls back to typing.
public final class UnavailableVoiceInput: VoiceInput {
    public init() {}
    public let isAvailable = false
    public let unavailableReason = "Voice input isn't set up yet. Please type your request."
    public func start(onEvent: @escaping (VoiceEvent) -> Void) {}
    public func stop() {}
}

public enum RunResult: Equatable {
    case completed(String)
    case notAvailable(String)
    case failed(String)
}

/// Runs an action the backend has authorized. Implemented in phases 5 to 8.
public protocol ActionRunner {
    func run(_ action: AuthorizedAction) async -> RunResult
}

/// Used until actions can be executed. It never pretends something happened.
public struct UnavailableRunner: ActionRunner {
    public init() {}
    public func run(_ action: AuthorizedAction) async -> RunResult {
        .notAvailable("I understood: \(action.summary). Running actions isn't built yet, so nothing was done.")
    }
}
