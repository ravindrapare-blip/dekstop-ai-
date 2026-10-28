import Foundation

/// Decides when a recording should end by itself, from the loudness readings.
/// Pure and clock-free, so it is easy to test.
public struct SilenceDetector {
    public enum Reason: Equatable {
        case silenceAfterSpeech   // the user finished talking
        case noSpeechHeard        // nothing but quiet for too long
        case tooLong              // hard time limit
    }

    public enum Decision: Equatable {
        case keepListening
        case stop(Reason)
    }

    /// Readings louder than this (dB) count as speech. Room noise on a laptop microphone is far below it.
    public let speechThreshold: Float
    public let silenceToStop: TimeInterval
    public let giveUpIfNoSpeech: TimeInterval
    public let maxDuration: TimeInterval

    private var startedAt: Date?
    private var lastSpeechAt: Date?

    public init(
        speechThreshold: Float = -35,
        silenceToStop: TimeInterval = 1.2,
        giveUpIfNoSpeech: TimeInterval = 6,
        maxDuration: TimeInterval = 15
    ) {
        self.speechThreshold = speechThreshold
        self.silenceToStop = silenceToStop
        self.giveUpIfNoSpeech = giveUpIfNoSpeech
        self.maxDuration = maxDuration
    }

    public mutating func feed(level: Float, at now: Date) -> Decision {
        let start = startedAt ?? now
        startedAt = start
        let elapsed = now.timeIntervalSince(start)

        if elapsed >= maxDuration { return .stop(.tooLong) }
        if level > speechThreshold { lastSpeechAt = now }
        if let last = lastSpeechAt {
            if now.timeIntervalSince(last) >= silenceToStop { return .stop(.silenceAfterSpeech) }
        } else if elapsed >= giveUpIfNoSpeech {
            return .stop(.noSpeechHeard)
        }
        return .keepListening
    }
}

/// Plain-language messages for voice problems. Short enough for the capsule.
enum VoiceMessages {
    static let micDenied = "Microphone access is off for Assistant. Turn it on in System Preferences ▸ Security & Privacy ▸ Privacy ▸ Microphone, then try again."
    static let micRestricted = "The microphone is blocked on this Mac by a profile or parental control, so voice input can't be used."
    static let cannotStartMic = "I couldn't start the microphone. Check that one is connected and not in use by another app."

    static func transcription(_ error: Error) -> String {
        guard let backend = error as? BackendError else { return "Voice recognition failed." }
        switch backend {
        case .unreachable: return "I can't reach the assistant backend."
        case .timedOut: return "Transcribing took too long."
        case .notLocal, .badResponse: return "Voice recognition failed."
        case .server(let status, let detail):
            switch status {
            case 503: return "Voice recognition isn't set up." + (detail.isEmpty ? "" : " " + String(detail.prefix(140)))
            case 400, 413: return "That recording couldn't be used."
            default: return "Voice recognition failed."
            }
        }
    }

    /// Short and lowercase: it is shown beside the "last action" label.
    static func speech(_ error: Error) -> String {
        guard let backend = error as? BackendError else { return "couldn't speak" }
        switch backend {
        case .unreachable: return "couldn't speak: backend unreachable"
        case .timedOut: return "couldn't speak: took too long"
        case .server(503, _): return "couldn't speak: voice output isn't set up"
        default: return "couldn't speak"
        }
    }
}

/// Records the microphone with `AudioRecording`, sends the audio to the backend for whisper.cpp
/// and reports the words. It never records without the user's permission, and never keeps audio.
public final class LocalVoiceInput: VoiceInput {
    private let client: BackendClient
    private let recorder: AudioRecording
    private let clock: () -> Date
    private let lock = NSLock()
    private var session = 0                 // changes on every start and cancel; late results are dropped
    private var recording = false
    private var detector = SilenceDetector()
    private var onEvent: ((VoiceEvent) -> Void)?
    private var transcription: Task<Void, Never>?

    public init(client: BackendClient, recorder: AudioRecording, clock: @escaping () -> Date = Date.init) {
        self.client = client
        self.recorder = recorder
        self.clock = clock
    }

    /// Permission problems are reported when recording starts, with instructions.
    public var isAvailable: Bool { true }
    public var unavailableReason: String { "" }

    public func start(onEvent: @escaping (VoiceEvent) -> Void) {
        let id = locked { () -> Int in
            session += 1
            recording = false
            self.onEvent = onEvent
            transcription?.cancel()
            return session
        }
        recorder.cancelRecording()

        switch recorder.permission {
        case .granted:
            begin(id)
        case .notDetermined:
            recorder.requestPermission { [weak self] granted in
                guard let self = self, self.isCurrent(id) else { return }
                if granted {
                    self.begin(id)
                } else {
                    self.fail(id, VoiceMessages.micDenied)
                }
            }
        case .denied:
            fail(id, VoiceMessages.micDenied)
        case .restricted:
            fail(id, VoiceMessages.micRestricted)
        }
    }

    public func stop() {
        finish(locked { session })
    }

    public func cancel() {
        locked { () -> Void in
            session += 1
            recording = false
            transcription?.cancel()
            transcription = nil
        }
        recorder.cancelRecording()
    }

    // MARK: - Internals

    private func begin(_ id: Int) {
        locked { () -> Void in
            detector = SilenceDetector()
            recording = true
        }
        do {
            try recorder.startRecording { [weak self] level in self?.handle(level: level, session: id) }
        } catch {
            locked { recording = false }
            fail(id, VoiceMessages.cannotStartMic)
        }
    }

    private func handle(level: Float, session id: Int) {
        let decision: SilenceDetector.Decision? = locked {
            guard id == session, recording else { return nil }
            return detector.feed(level: level, at: clock())
        }
        if let decision = decision, case .stop = decision { finish(id) }
    }

    private func finish(_ id: Int) {
        let callback: ((VoiceEvent) -> Void)? = locked {
            guard id == session, recording else { return nil }
            recording = false
            return onEvent
        }
        guard let callback = callback else { return }

        let wav = recorder.finishRecording()
        callback(.processing)
        guard let audio = wav, !audio.isEmpty else {
            callback(.transcript(""))
            return
        }
        let task = Task { [client] in
            do {
                let text = try await client.transcribe(wav: audio)
                guard self.isCurrent(id) else { return }
                callback(.transcript(text))
            } catch is CancellationError {
                // cancelled by the user
            } catch {
                guard self.isCurrent(id) else { return }
                callback(.failed(VoiceMessages.transcription(error)))
            }
        }
        locked { transcription = task }
    }

    private func fail(_ id: Int, _ message: String) {
        let callback: ((VoiceEvent) -> Void)? = locked { id == session ? onEvent : nil }
        callback?(.failed(message))
    }

    private func isCurrent(_ id: Int) -> Bool {
        locked { id == session }
    }

    private func locked<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }
}
