import Foundation

// Interfaces to things the capsule does not own. Real implementations plug in without the
// controller or the views changing.

// MARK: - Voice in

public enum VoiceEvent: Equatable {
    case processing              // recording ended on its own (silence or time limit); transcribing now
    case transcript(String)      // may be empty if nothing was heard
    case failed(String)          // a plain explanation; the controller falls back to typing
}

/// Microphone to text. Implementations may call `onEvent` from any thread.
public protocol VoiceInput: AnyObject {
    var isAvailable: Bool { get }
    var unavailableReason: String { get }
    func start(onEvent: @escaping (VoiceEvent) -> Void)
    /// Finish recording and transcribe what was said. A transcript or failure always follows.
    func stop()
    /// Discard the recording, or ignore the transcript if it is already being converted.
    func cancel()
}

/// Used when there is no voice engine. The UI falls back to typing.
public final class UnavailableVoiceInput: VoiceInput {
    public init() {}
    public let isAvailable = false
    public let unavailableReason = "Voice input isn't set up yet. Please type your request."
    public func start(onEvent: @escaping (VoiceEvent) -> Void) {}
    public func stop() {}
    public func cancel() {}
}

public enum MicPermission: Equatable {
    case notDetermined
    case granted
    case denied
    case restricted   // blocked by a profile or parental controls; the user cannot change it
}

/// The microphone itself. Implemented with AVFoundation in the app target.
public protocol AudioRecording: AnyObject {
    var permission: MicPermission { get }
    /// Shows the system prompt. The completion must run on the main thread.
    func requestPermission(_ completion: @escaping (Bool) -> Void)
    /// Starts recording 16 kHz, 16-bit, mono audio. `onLevel` receives the loudness in dB (about -160 to 0)
    /// roughly ten times a second. Throws if the microphone cannot be opened.
    func startRecording(onLevel: @escaping (Float) -> Void) throws
    /// Stops and returns the WAV data, or nil if nothing was recorded. The temporary file is deleted.
    func finishRecording() -> Data?
    func cancelRecording()
}

// MARK: - Voice out

public enum SpeechEvent: Equatable {
    case finished
    case failed(String)   // a short reason, such as "voice output isn't set up"
}

/// Reads a reply aloud. Implementations may call `onEvent` from any thread.
public protocol SpeechOutput: AnyObject {
    var isAvailable: Bool { get }
    func speak(_ text: String, onEvent: @escaping (SpeechEvent) -> Void)
    func stop()
}

public final class UnavailableSpeech: SpeechOutput {
    public init() {}
    public let isAvailable = false
    public func speak(_ text: String, onEvent: @escaping (SpeechEvent) -> Void) {
        onEvent(.failed("voice output isn't set up"))
    }
    public func stop() {}
}

/// Plays WAV data. Implemented with AVFoundation in the app target.
public protocol AudioPlaying: AnyObject {
    /// Starts playing. `completion` runs when playback ends, with an error if it failed.
    /// It does not run after `stop()`.
    func play(wav: Data, completion: @escaping (Error?) -> Void) throws
    func stop()
}

public enum SpeakMode: String, Equatable {
    case voiceOnly = "voice"   // read replies aloud only when the request was spoken
    case always
    case never
}

// MARK: - Actions

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
