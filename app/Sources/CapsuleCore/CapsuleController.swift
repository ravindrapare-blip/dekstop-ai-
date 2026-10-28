import Foundation

/// Connects typed or spoken requests to the backend and keeps the capsule's state.
/// It has no UI code. The window observes `onChange` and sends user actions to these methods.
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
    private let runner: ActionRunner
    private var task: Task<Void, Never>?
    private var requestID = 0   // answers to older requests are ignored

    public init(client: BackendClient, voice: VoiceInput, runner: ActionRunner) {
        self.client = client
        self.voice = voice
        self.runner = runner
    }

    // MARK: - User actions

    /// Returns true if the request was sent.
    @discardableResult
    public func submit(_ rawText: String) -> Bool {
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return false }
        guard text.unicodeScalars.count <= Self.maxInputLength else {
            apply(.notice("That's too long. Please keep it under \(Self.maxInputLength) characters.", .warning))
            return false
        }
        guard state.phase != .thinking, state.phase != .executing else { return false }

        if state.phase == .listening { voice.stop() }
        if let pending = state.pending { discard(pending) }
        apply(.started)
        requestID += 1
        let id = requestID
        task = Task { [weak self] in await self?.route(text, id: id) }
        return true
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
        apply(.started)
        task = Task { [weak self] in
            await self?.finishConfirmation(pending, acknowledgeRisk: acknowledgeRisk, id: id)
        }
    }

    /// Stops waiting for the model. The backend may still finish, but its answer is ignored.
    public func cancel() {
        guard state.phase == .thinking else { return }
        requestID += 1
        task?.cancel()
        task = nil
        apply(.finished(message: "Cancelled.", tone: .neutral, lastAction: "cancelled"))
    }

    public func toggleMic() {
        if state.phase == .listening {
            voice.stop()
            apply(.listeningEnded)
            return
        }
        guard !state.phase.isBusy else { return }
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

    /// Call when the window is hidden.
    public func dismissed() {
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

    // MARK: - Internals

    private func apply(_ event: Event) {
        state = reduce(state, event)
    }

    private func handleVoice(_ event: VoiceEvent) {
        guard state.phase == .listening else { return }   // late events are ignored
        switch event {
        case .transcript(let text):
            if !submit(text) && state.phase == .listening {
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
            await perform(plan(response), id: id)
        } catch is CancellationError {
            // cancel() already updated the state
        } catch {
            guard id == requestID else { return }
            apply(.failed(Self.describe(error)))
        }
    }

    private func finishConfirmation(_ pending: PendingConfirmation, acknowledgeRisk: Bool, id: Int) async {
        do {
            let decision = try await client.confirm(id: pending.id, approved: true, acknowledgeRisk: acknowledgeRisk)
            guard id == requestID else { return }
            await perform(plan(decision), id: id)
        } catch is CancellationError {
            // cancel() already updated the state
        } catch {
            guard id == requestID else { return }
            apply(.failed(Self.describe(error)))
        }
    }

    private func perform(_ step: Step, id: Int) async {
        switch step {
        case .reply(let text):
            apply(.finished(message: text, tone: .neutral, lastAction: "reply"))
        case .run(let action):
            apply(.executing(action.summary))
            let result = await runner.run(action)
            guard id == requestID else { return }
            switch result {
            case .completed(let text):
                apply(.finished(message: text, tone: .success, lastAction: action.summary))
            case .notAvailable(let text):
                apply(.finished(message: text, tone: .warning, lastAction: "\(action.summary) (not run)"))
            case .failed(let text):
                apply(.finished(message: text, tone: .error, lastAction: "\(action.summary) (failed)"))
            }
        case .ask(let pending):
            apply(.waiting(pending))
        case .refuse(let text):
            apply(.finished(message: text, tone: .warning, lastAction: "blocked"))
        case .preview(let text):
            apply(.finished(message: text, tone: .neutral, lastAction: nil))
        case .invalid(let text):
            apply(.failed(text))
        }
    }

    private func discard(_ pending: PendingConfirmation) {
        let client = self.client
        Task { _ = try? await client.confirm(id: pending.id, approved: false, acknowledgeRisk: false) }
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
