import Foundation

/// Which rows the capsule shows, and how tall it is. The view and the window both use this,
/// so they always agree. Sizes are in points.
public struct CapsuleLayout: Equatable {
    public var showChips = false
    public var showMessage = false
    public var showAcknowledge = false
    public var showConfirm = false
    public var showLastAction = false

    public static let width: Double = 440
    public static let verticalPadding: Double = 8
    public static let spacing: Double = 6
    public static let inputRow: Double = 40
    public static let chipRow: Double = 26
    public static let chips: Double = chipRow * 2 + 6
    public static let message: Double = 54
    public static let acknowledge: Double = 22
    public static let confirm: Double = 30
    public static let lastAction: Double = 16

    public init() {}

    public init(state: CapsuleState, inputIsEmpty: Bool) {
        showMessage = !state.message.isEmpty
        showConfirm = state.pending != nil
        showAcknowledge = state.pending?.requiresAcknowledgment ?? false
        showChips = state.phase == .idle && inputIsEmpty && !showMessage
        showLastAction = !state.lastAction.isEmpty && !showConfirm
    }

    public var height: Double {
        var rows = [Self.inputRow]
        if showMessage { rows.append(Self.message) }
        if showAcknowledge { rows.append(Self.acknowledge) }
        if showConfirm { rows.append(Self.confirm) }
        if showChips { rows.append(Self.chips) }
        if showLastAction { rows.append(Self.lastAction) }
        let gaps = Self.spacing * Double(rows.count - 1)
        return Self.verticalPadding * 2 + rows.reduce(0, +) + gaps
    }
}
