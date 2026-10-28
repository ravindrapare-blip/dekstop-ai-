import SwiftUI
import CapsuleCore

/// The pill. Plain shapes and one solid color: no blur or continuous animation,
/// so it stays cheap on an older GPU.
struct CapsuleView: View {
    @ObservedObject var vm: CapsuleViewModel
    @FocusState private var fieldFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: CGFloat(CapsuleLayout.spacing)) {
            inputRow
            if vm.layout.showMessage { messageRow }
            if vm.layout.showAcknowledge { acknowledgeRow }
            if vm.layout.showConfirm { confirmRow }
            if vm.layout.showChips { chipsRows }
            if vm.layout.showLastAction { lastActionRow }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, CGFloat(CapsuleLayout.verticalPadding))
        .frame(width: CGFloat(CapsuleLayout.width), height: CGFloat(vm.layout.height), alignment: .top)
        .background(
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .fill(Color(red: 0.07, green: 0.07, blue: 0.08).opacity(0.94))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .stroke(Color.white.opacity(0.10), lineWidth: 1)
        )
        .onChange(of: vm.focusToken) { _ in fieldFocused = true }
        .onChange(of: vm.input) { _ in vm.inputChanged() }
        .onHover { inside in
            if inside { vm.noteActivity() }
        }
        .onExitCommand { vm.escape() }
    }

    // MARK: - Rows

    private var inputRow: some View {
        HStack(spacing: 10) {
            StatusIndicator(phase: vm.state.phase, tone: vm.state.tone)
            TextField(vm.state.placeholder, text: $vm.input)
                .textFieldStyle(.plain)
                .font(.system(size: 15))
                .foregroundColor(.white)
                .focused($fieldFocused)
                .onSubmit { vm.submit() }
            Button(action: { vm.toggleMic() }) {
                Image(systemName: vm.state.phase == .listening ? "mic.fill" : "mic")
                    .font(.system(size: 15))
                    .foregroundColor(vm.state.phase == .listening ? Color.red : Color.white.opacity(0.85))
                    .frame(width: 28, height: 28)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Tap to speak, tap again to send")
            .accessibilityLabel("Microphone")
        }
        .frame(height: CGFloat(CapsuleLayout.inputRow))
    }

    private var messageRow: some View {
        Text(vm.state.message)
            .font(.system(size: 13))
            .foregroundColor(color(for: vm.state.tone))
            .lineLimit(3)
            .help(vm.state.message)
            .frame(
                maxWidth: .infinity,
                minHeight: CGFloat(CapsuleLayout.message),
                maxHeight: CGFloat(CapsuleLayout.message),
                alignment: .topLeading
            )
    }

    private var acknowledgeRow: some View {
        Toggle("I understand the risk", isOn: $vm.acknowledged)
            .toggleStyle(.checkbox)
            .font(.system(size: 12))
            .foregroundColor(.white)
            .frame(maxWidth: .infinity, minHeight: CGFloat(CapsuleLayout.acknowledge), alignment: .leading)
    }

    private var confirmRow: some View {
        let needsAcknowledgment = vm.state.pending?.requiresAcknowledgment ?? false
        return HStack(spacing: 8) {
            Button("Confirm") { vm.answer(approved: true) }
                .disabled(needsAcknowledgment && !vm.acknowledged)
            Button("Cancel") { vm.answer(approved: false) }
            Spacer(minLength: 0)
        }
        .controlSize(.small)
        .frame(height: CGFloat(CapsuleLayout.confirm))
    }

    private var chipsRows: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(0..<Suggestions.rows.count, id: \.self) { index in
                HStack(spacing: 6) {
                    ForEach(Suggestions.rows[index]) { suggestion in
                        chip(suggestion)
                    }
                    Spacer(minLength: 0)
                }
            }
        }
        .frame(height: CGFloat(CapsuleLayout.chips), alignment: .top)
    }

    private func chip(_ suggestion: Suggestion) -> some View {
        Button(action: { vm.choose(suggestion) }) {
            Text(suggestion.title)
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(.white)
                .padding(.horizontal, 10)
                .frame(height: CGFloat(CapsuleLayout.chipRow))
                .background(Capsule().fill(Color.white.opacity(0.12)))
        }
        .buttonStyle(.plain)
    }

    private var lastActionRow: some View {
        Text("Last: \(vm.state.lastAction)")
            .font(.system(size: 11))
            .foregroundColor(Color.white.opacity(0.45))
            .lineLimit(1)
            .frame(maxWidth: .infinity, minHeight: CGFloat(CapsuleLayout.lastAction), alignment: .leading)
    }

    private func color(for tone: Tone) -> Color {
        switch tone {
        case .neutral: return Color.white.opacity(0.92)
        case .success: return Color(red: 0.40, green: 0.85, blue: 0.55)
        case .warning: return Color(red: 1.0, green: 0.72, blue: 0.30)
        case .error: return Color(red: 1.0, green: 0.45, blue: 0.45)
        }
    }
}

/// A dot for the state, or a small spinner while the assistant is working.
private struct StatusIndicator: View {
    let phase: Phase
    let tone: Tone

    var body: some View {
        Group {
            if phase == .thinking || phase == .executing || phase == .transcribing {
                ProgressView()
                    .controlSize(.small)
                    .scaleEffect(0.7)
            } else if phase == .speaking {
                Image(systemName: "speaker.wave.2.fill")
                    .font(.system(size: 12))
                    .foregroundColor(Color(red: 0.45, green: 0.70, blue: 1.0))
            } else {
                Circle()
                    .fill(dotColor)
                    .frame(width: 10, height: 10)
            }
        }
        .frame(width: 18, height: 18)
        .accessibilityLabel(label)
    }

    private var dotColor: Color {
        switch phase {
        case .idle: return Color.white.opacity(0.35)
        case .listening: return Color.red
        case .waiting: return Color.orange
        case .done:
            return tone == .success ? Color.green : (tone == .warning ? Color.orange : Color.white.opacity(0.7))
        case .error: return Color.red
        case .thinking, .executing, .transcribing, .speaking: return Color.clear
        }
    }

    private var label: String {
        switch phase {
        case .idle: return "Ready"
        case .listening: return "Listening"
        case .transcribing: return "Transcribing"
        case .speaking: return "Speaking"
        case .thinking: return "Thinking"
        case .waiting: return "Waiting for your answer"
        case .executing: return "Working"
        case .done: return "Done"
        case .error: return "Error"
        }
    }
}
