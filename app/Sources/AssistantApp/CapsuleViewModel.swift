import SwiftUI
import CapsuleCore

/// Glue between the controller (logic) and the SwiftUI view. It holds no rules of its own.
@MainActor
final class CapsuleViewModel: ObservableObject {
    @Published private(set) var state = CapsuleState()
    @Published private(set) var layout = CapsuleLayout()
    @Published private(set) var focusToken = 0
    @Published var input = ""
    @Published var acknowledged = false   // the "I understand the risk" box

    var onHeightChange: (@MainActor (Double) -> Void)?
    var onActivity: (@MainActor () -> Void)?
    var onNeedsAttention: (@MainActor () -> Void)?   // a question or result appeared while hidden
    var onHideRequest: (@MainActor () -> Void)?

    private let controller: CapsuleController

    init(controller: CapsuleController) {
        self.controller = controller
        controller.onChange = { [weak self] state in self?.stateChanged(state) }
        controller.onFocusRequest = { [weak self] in self?.requestFocus() }
        layout = CapsuleLayout(state: state, inputIsEmpty: true)
    }

    // MARK: - From the view

    func submit() {
        if controller.submit(input) {
            input = ""
            relayout()
        }
    }

    func choose(_ suggestion: Suggestion) {
        if suggestion.sendsImmediately {
            if controller.submit(suggestion.prefill) { input = "" }
        } else {
            input = suggestion.prefill
            requestFocus()
        }
        relayout()
    }

    func toggleMic() {
        controller.toggleMic()
    }

    func answer(approved: Bool) {
        controller.confirm(approved: approved, acknowledgeRisk: acknowledged)
    }

    /// Escape: undo the most recent thing, and hide only when there is nothing left to undo.
    func escape() {
        if state.pending != nil {
            controller.confirm(approved: false)
        } else if state.phase == .thinking {
            controller.cancel()
        } else if state.phase == .listening || state.phase == .transcribing {
            controller.cancelVoice()
        } else if state.phase == .speaking {
            controller.stopSpeaking()
        } else if !input.isEmpty {
            input = ""
            relayout()
        } else {
            onHideRequest?()
        }
    }

    func inputChanged() {
        relayout()
        onActivity?()
    }

    func noteActivity() {
        onActivity?()
    }

    // MARK: - From the window

    func windowShown() {
        controller.checkBackend()
        requestFocus()
    }

    func windowHidden() {
        controller.dismissed()
    }

    func requestFocus() {
        focusToken += 1
    }

    // MARK: - Internals

    private func stateChanged(_ new: CapsuleState) {
        let old = state
        state = new
        if new.pending?.id != old.pending?.id { acknowledged = false }
        relayout()
        onActivity?()
        if new.phase != old.phase, [Phase.waiting, .done, .error].contains(new.phase) {
            onNeedsAttention?()
        }
    }

    private func relayout() {
        let next = CapsuleLayout(state: state, inputIsEmpty: input.isEmpty)
        guard next != layout else { return }
        layout = next
        onHeightChange?(next.height)
    }
}
