import Foundation

/// Turns a result message into something comfortable to hear: one line, and short.
public enum SpokenText {
    public static let maxLength = 300

    public static func make(from message: String, maxLength: Int = SpokenText.maxLength) -> String {
        let collapsed = message.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard collapsed.count > maxLength else { return collapsed }

        let cut = String(collapsed.prefix(maxLength))
        let enders: [Character] = [".", "!", "?"]
        if let end = cut.lastIndex(where: { enders.contains($0) }),
           cut.distance(from: cut.startIndex, to: end) > maxLength / 2 {
            return String(cut[...end])           // stop at the last whole sentence
        }
        if let space = cut.lastIndex(of: " ") {
            return String(cut[..<space])         // otherwise at a word boundary
        }
        return cut
    }
}

/// Reads replies aloud: the backend makes the audio (Piper, or the Mac voice), `AudioPlaying` plays it.
public final class LocalSpeech: SpeechOutput {
    private let client: BackendClient
    private let player: AudioPlaying
    private let lock = NSLock()
    private var generation = 0       // changes on every speak and stop; stale results are dropped
    private var task: Task<Void, Never>?

    public init(client: BackendClient, player: AudioPlaying) {
        self.client = client
        self.player = player
    }

    public var isAvailable: Bool { true }

    public func speak(_ text: String, onEvent: @escaping (SpeechEvent) -> Void) {
        let id = locked { () -> Int in
            generation += 1
            task?.cancel()
            return generation
        }
        player.stop()

        let newTask = Task { [client, player] in
            do {
                let wav = try await client.speak(text: text)
                guard self.isCurrent(id) else { return }
                // Playback starts on the main thread.
                try await MainActor.run {
                    try player.play(wav: wav) { [weak self] error in
                        guard let self = self, self.isCurrent(id) else { return }
                        onEvent(error == nil ? .finished : .failed("couldn't play the audio"))
                    }
                }
            } catch is CancellationError {
                // stopped by the user
            } catch {
                guard self.isCurrent(id) else { return }
                onEvent(.failed(VoiceMessages.speech(error)))
            }
        }
        locked { task = newTask }
    }

    public func stop() {
        locked { () -> Void in
            generation += 1
            task?.cancel()
            task = nil
        }
        player.stop()
    }

    private func isCurrent(_ id: Int) -> Bool {
        locked { id == generation }
    }

    private func locked<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }
}
