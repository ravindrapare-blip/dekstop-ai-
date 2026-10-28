import Foundation

public enum Phase: Equatable {
    case idle
    case listening     // the microphone is recording
    case transcribing  // the recording is being turned into text
    case thinking      // the local model is working on the request
    case waiting       // waiting for the user to confirm or cancel
    case executing
    case done
    case speaking      // a finished result is being read aloud
    case error

    /// While busy the window must not auto-hide, and new requests are ignored.
    public var isBusy: Bool {
        switch self {
        case .listening, .transcribing, .thinking, .waiting, .executing, .speaking: return true
        case .idle, .done, .error: return false
        }
    }
}

/// How a message should look. The window colors itself from this.
public enum Tone: Equatable {
    case neutral
    case success
    case warning
    case error
}

/// A request the safety layer wants the user to approve.
public struct PendingConfirmation: Equatable {
    public let id: String
    public let message: String
    public let requiresAcknowledgment: Bool
    public let actionLabel: String

    public init(id: String, message: String, requiresAcknowledgment: Bool, actionLabel: String) {
        self.id = id
        self.message = message
        self.requiresAcknowledgment = requiresAcknowledgment
        self.actionLabel = actionLabel
    }
}

public struct CapsuleState: Equatable {
    public var phase: Phase = .idle
    public var tone: Tone = .neutral
    public var message: String = ""
    public var lastAction: String = ""
    public var pending: PendingConfirmation?
    public var choice: ChoicePrompt?       // "which one did you mean?"

    public init() {}

    /// Shown as the input placeholder, so the state is readable without any animation.
    public var placeholder: String {
        switch phase {
        case .idle: return "Ask me to do something…"
        case .listening: return "Listening…"
        case .transcribing: return "Transcribing…"
        case .thinking: return "Thinking…"
        case .waiting: return "Waiting for your answer…"
        case .executing: return "Working on it…"
        case .done: return "Anything else?"
        case .speaking: return "Speaking…"
        case .error: return "Something went wrong"
        }
    }
}

public enum Event: Equatable {
    case started                                       // a request was sent
    case listening                                     // the microphone started
    case transcribing                                  // recording ended, speech is being converted
    case voiceCancelled                                // recording or transcription was abandoned
    case speaking                                      // a result is being read aloud
    case speechEnded
    case speechFailed(String)                          // a short reason, shown beside the last action
    case notice(String, Tone)                          // a message with no request, e.g. voice unavailable
    case waiting(PendingConfirmation)
    case choosing(ChoicePrompt)                        // several matches; waiting for the user to pick
    case executing(String)                             // label of the action being run
    case finished(message: String, tone: Tone, lastAction: String?)
    case failed(String)
    case dismissed                                     // the window was hidden
}

/// Pure state transitions. No I/O, so it is easy to test.
public func reduce(_ state: CapsuleState, _ event: Event) -> CapsuleState {
    var s = state
    switch event {
    case .started:
        s.phase = .thinking
        s.tone = .neutral
        s.message = ""
        s.pending = nil
        s.choice = nil
    case .listening:
        s.phase = .listening
        s.tone = .neutral
        s.message = ""
    case .transcribing:
        if s.phase == .listening { s.phase = .transcribing }
    case .voiceCancelled:
        if s.phase == .listening || s.phase == .transcribing { s.phase = .idle }
    case .speaking:
        // Only a finished result is read aloud; its text, tone and last action stay on screen.
        if s.phase == .done { s.phase = .speaking }
    case .speechEnded:
        if s.phase == .speaking { s.phase = .done }
    case .speechFailed(let reason):
        if s.phase == .speaking {
            s.phase = .done
            s.lastAction = s.lastAction.isEmpty ? reason : "\(s.lastAction) · \(reason)"
        }
    case .notice(let text, let tone):
        s.phase = .idle
        s.tone = tone
        s.message = text
        s.pending = nil
        s.choice = nil
    case .waiting(let pending):
        s.phase = .waiting
        s.tone = .warning
        s.message = pending.message
        s.pending = pending
        s.choice = nil
    case .choosing(let choice):
        s.phase = .waiting
        s.tone = .neutral
        s.message = choice.prompt
        s.pending = nil
        s.choice = choice
    case .executing(let label):
        s.phase = .executing
        s.tone = .neutral
        s.message = ""
        s.pending = nil
        s.choice = nil
        s.lastAction = label
    case .finished(let message, let tone, let lastAction):
        s.phase = .done
        s.tone = tone
        s.message = message
        s.pending = nil
        s.choice = nil
        if let lastAction = lastAction { s.lastAction = lastAction }
    case .failed(let message):
        s.phase = .error
        s.tone = .error
        s.message = message
        s.pending = nil
        s.choice = nil
    case .dismissed:
        // Hiding the window clears a finished result. Work in progress is kept.
        if !s.phase.isBusy {
            s.phase = .idle
            s.tone = .neutral
            s.message = ""
        }
    }
    return s
}
