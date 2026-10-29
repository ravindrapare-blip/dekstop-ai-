import Foundation

/// Connects typed or spoken requests to the backend and keeps the capsule's state.
/// It has no UI code. The window observes `onChange` and sends user actions to these methods.
///
/// A spoken request follows the same path as a typed one, so the safety layer sees both the same way:
///   mic -> recording -> transcript -> /route -> safety decision -> (confirm / choose) -> result -> spoken reply
/// The backend runs actions itself, only after the safety layer allowed them; this class just shows the outcome.
@MainActor
public final class CapsuleController {
    public static let maxInputLength = 1000   // the backend rejects longer text

    public private(set) var state = CapsuleState() {
        didSet {
            if state != oldValue { onChange?(state) }
        }
    }
    public var onChange: (@MainActor (CapsuleState) -> Void)?
    /// Asked when the text field should take focus, for example after voice fails.
    public var onFocusRequest: (@MainActor () -> Void)?

    private let client: BackendClient
    private let voice: VoiceInput
    private let speech: SpeechOutput
    private let speakMode: SpeakMode
    private var task: Task<Void, Never>?
    private var requestID = 0              // answers to older requests are ignored
    private var speechID = 0               // so are old speech events
    private var lastRequestWasSpoken = false

    public init(
        client: BackendClient,
        voice: VoiceInput,
        speech: SpeechOutput = UnavailableSpeech(),
        speakMode: SpeakMode = .voiceOnly
    ) {
        self.client = client
        self.voice = voice
        self.speech = speech
        self.speakMode = speakMode
    }

    // MARK: - User actions

    /// Returns true if the request was sent.
    @discardableResult
    public func submit(_ rawText: String) -> Bool {
        submit(rawText, spoken: false)
    }

    /// Answer a confirmation request. A high-risk request needs `acknowledgeRisk` to be approved.
    public func confirm(approved: Bool, acknowledgeRisk: Bool = false) {
        guard state.phase == .waiting, let pending = state.pending else { return }
        if approved && pending.requiresAcknowledgment && !acknowledgeRisk { return }

        requestID += 1
        let id = requestID
        if !approved {
            // Cancelling is always safe, so it does not wait for the backend.
            discard(pending)
            apply(.finished(message: "Cancelled. I didn't do it.", tone: .neutral, lastAction: "cancelled"))
            return
        }
        apply(.executing(pending.actionLabel))      // the backend is carrying out the approved action
        task = Task { [weak self] in
            await self?.finishConfirmation(pending, acknowledgeRisk: acknowledgeRisk, id: id)
        }
    }

    /// The user picked one of several matches.
    public func choose(optionId: String) {
        guard state.phase == .waiting, let choice = state.choice,
              let option = choice.options.first(where: { $0.id == optionId }) else { return }
        requestID += 1
        let id = requestID
        apply(.executing(option.label))
        task = Task { [weak self] in
            await self?.finishChoice(choice, optionId: optionId, id: id)
        }
    }

    /// The user does not want any of the matches. Cancelling does not wait for the backend.
    public func cancelChoice() {
        guard state.phase == .waiting, let choice = state.choice else { return }
        requestID += 1
        discard(choice)
        apply(.finished(message: "Cancelled.", tone: .neutral, lastAction: "cancelled"))
    }

    /// Stops waiting for the model. The backend may still finish, but its answer is ignored.
    public func cancel() {
        guard state.phase == .thinking else { return }
        requestID += 1
        task?.cancel()
        task = nil
        apply(.finished(message: "Cancelled.", tone: .neutral, lastAction: "cancelled"))
    }

    /// The microphone button. Tap to start, tap again to send what you said.
    public func toggleMic() {
        switch state.phase {
        case .listening:
            voice.stop()               // a transcript or a failure always follows
            apply(.transcribing)
            return
        case .speaking:
            stopSpeaking()             // the mic interrupts a spoken reply, then listens
        case .transcribing, .thinking, .waiting, .executing:
            return
        case .idle, .done, .error:
            break
        }

        guard voice.isAvailable else {
            apply(.notice(voice.unavailableReason, .warning))
            onFocusRequest?()
            return
        }
        apply(.listening)
        voice.start { [weak self] event in
            Task { @MainActor in self?.handleVoice(event) }
        }
    }

    /// Escape while listening or transcribing: throw the recording away.
    public func cancelVoice() {
        guard state.phase == .listening || state.phase == .transcribing else { return }
        voice.cancel()
        apply(.voiceCancelled)
    }

    /// Stops a reply that is being read aloud.
    public func stopSpeaking() {
        guard state.phase == .speaking else { return }
        speechID += 1
        speech.stop()
        apply(.speechEnded)
    }

    /// Call when the window is hidden. Nothing keeps recording or talking behind a hidden window.
    public func dismissed() {
        cancelVoice()
        stopSpeaking()
        apply(.dismissed)
    }

    public func notice(_ text: String, tone: Tone = .warning) {
        guard !state.phase.isBusy else { return }
        apply(.notice(text, tone))
    }

    /// One cheap request when the window opens, so a stopped backend is explained instead of
    /// failing silently later.
    public func checkBackend() {
        guard !state.phase.isBusy else { return }
        let client = self.client
        Task { [weak self] in
            let healthy = await client.health()
            guard let self = self, !healthy, !self.state.phase.isBusy else { return }
            self.apply(.notice("I can't reach the assistant backend. Start it with scripts/start.sh.", .warning))
        }
    }

    // MARK: - Requests

    private func submit(_ rawText: String, spoken: Bool) -> Bool {
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return false }
        guard text.unicodeScalars.count <= Self.maxInputLength else {
            apply(.notice("That's too long. Please keep it under \(Self.maxInputLength) characters.", .warning))
            return false
        }
        // A transcript arrives while the phase is listening or transcribing. A typed request must wait
        // for those, and for work already in progress.
        let phase = state.phase
        if phase == .thinking || phase == .executing { return false }
        if !spoken && phase == .transcribing { return false }

        if !spoken && phase == .listening { voice.cancel() }
        stopSpeaking()
        if let pending = state.pending { discard(pending) }
        if let choice = state.choice { discard(choice) }
        lastRequestWasSpoken = spoken
        apply(.started)
        requestID += 1
        let id = requestID
        task = Task { [weak self] in await self?.route(text, id: id) }
        return true
    }

    private func handleVoice(_ event: VoiceEvent) {
        guard state.phase == .listening || state.phase == .transcribing else { return }   // late events are ignored
        switch event {
        case .processing:
            apply(.transcribing)
        case .transcript(let text):
            if !submit(text, spoken: true) && (state.phase == .listening || state.phase == .transcribing) {
                apply(.notice("I didn't catch that. Please try again or type your request.", .warning))
                onFocusRequest?()
            }
        case .failed(let message):
            apply(.notice("\(message) Please type your request.", .warning))
            onFocusRequest?()
        }
    }

    private func route(_ text: String, id: Int) async {
        do {
            let response = try await client.route(text: text)
            guard id == requestID else { return }
            var step = plan(response)
            if let brain = response.brain, !brain.notice.isEmpty {
                step = adding(notice: brain.notice, to: step)      // for example: "Gemini's quota was reached, so I switched to the local model."
                if brain.speak { lastRequestWasSpoken = true }     // the user asked to hear switches; "never speak" still wins
            }
            perform(step)
        } catch is CancellationError {
            // cancel() already updated the state
        } catch {
            guard id == requestID else { return }
            apply(.failed(Self.describe(error)))
        }
    }

    private func finishChoice(_ choice: ChoicePrompt, optionId: String, id: Int) async {
        do {
            let response = try await client.choose(choiceId: choice.choiceId, optionId: optionId)
            guard id == requestID else { return }
            perform(plan(response))
        } catch is CancellationError {
            // cancel() already updated the state
        } catch {
            guard id == requestID else { return }
            apply(.failed(Self.describe(error)))
        }
    }

    private func finishConfirmation(_ pending: PendingConfirmation, acknowledgeRisk: Bool, id: Int) async {
        do {
            let response = try await client.confirm(id: pending.id, approved: true, acknowledgeRisk: acknowledgeRisk)
            guard id == requestID else { return }
            perform(plan(response))
        } catch is CancellationError {
            // cancel() already updated the state
        } catch {
            guard id == requestID else { return }
            apply(.failed(Self.describe(error)))
        }
    }

    private func perform(_ step: Step) {
        switch step {
        case .reply(let text):
            finish(text, tone: .neutral, lastAction: "reply")
        case .report(let message, let tone, let lastAction):
            finish(message, tone: tone, lastAction: lastAction)
        case .choose(let choice):
            apply(.choosing(choice))    // answered with the buttons, so it is not read aloud
        case .ask(let pending):
            apply(.waiting(pending))    // answered with the buttons, so it is not read aloud
        case .refuse(let text):
            finish(text, tone: .warning, lastAction: "blocked")
        case .preview(let text):
            finish(text, tone: .neutral, lastAction: nil)
        case .invalid(let text):
            apply(.failed(text))
        }
    }

    private func discard(_ pending: PendingConfirmation) {
        let client = self.client
        Task { _ = try? await client.confirm(id: pending.id, approved: false, acknowledgeRisk: false) }
    }

    private func discard(_ choice: ChoicePrompt) {
        let client = self.client
        Task { _ = try? await client.choose(choiceId: choice.choiceId, optionId: nil) }
    }

    // MARK: - State and speech

    private func apply(_ event: Event) {
        state = reduce(state, event)
    }

    /// Shows a result and, if wanted, reads it aloud.
    private func finish(_ message: String, tone: Tone, lastAction: String?) {
        apply(.finished(message: message, tone: tone, lastAction: lastAction))
        speakIfWanted(message)
    }

    private func speakIfWanted(_ message: String) {
        guard speech.isAvailable, speakMode != .never else { return }
        guard speakMode == .always || lastRequestWasSpoken else { return }
        let text = SpokenText.make(from: message)
        guard !text.isEmpty else { return }

        speechID += 1
        let id = speechID
        apply(.speaking)
        speech.speak(text) { [weak self] event in
            Task { @MainActor in self?.handleSpeech(event, id: id) }
        }
    }

    private func handleSpeech(_ event: SpeechEvent, id: Int) {
        guard id == speechID, state.phase == .speaking else { return }
        switch event {
        case .finished: apply(.speechEnded)
        case .failed(let reason): apply(.speechFailed(reason))
        }
    }

    static func describe(_ error: Error) -> String {
        guard let backend = error as? BackendError else { return "Something went wrong." }
        switch backend {
        case .unreachable:
            return "I can't reach the assistant backend. Start it with scripts/start.sh."
        case .timedOut:
            return "The model took too long to answer."
        case .notLocal:
            return "The backend address must be on this Mac (127.0.0.1)."
        case .badResponse:
            return "I got an unexpected reply from the backend."
        case .server(let status, let detail):
            let extra = detail.isEmpty ? "" : " " + String(detail.prefix(120))
            switch status {
            case 503: return "The local model isn't ready." + extra
            case 502: return "The local model returned an error." + extra
            case 422: return "I couldn't send that request."
            default: return "The backend returned an error (\(status))." + extra
            }
        }
    }
}
