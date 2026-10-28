import AppKit
import Combine
import SwiftUI
import CapsuleCore

/// A borderless panel that can take keyboard input without making the app active,
/// so showing it does not pull focus away from the window the user was using.
final class CapsulePanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

/// Owns the floating window: show, hide, position, resize and the idle timer.
@MainActor
final class PanelController {
    private let panel: CapsulePanel
    private let viewModel: CapsuleViewModel
    private let idlePolicy: IdlePolicy
    private var idleTimer: Timer?
    private var lastActivity = Date()
    private var cancellables = Set<AnyCancellable>()

    var isVisible: Bool { panel.isVisible }

    init(viewModel: CapsuleViewModel, idlePolicy: IdlePolicy) {
        self.viewModel = viewModel
        self.idlePolicy = idlePolicy

        let size = NSSize(width: CapsuleLayout.width, height: viewModel.layout.height)
        panel = CapsulePanel(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = true
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.appearance = NSAppearance(named: .darkAqua)
        panel.contentView = NSHostingView(rootView: CapsuleView(vm: viewModel))

        viewModel.onHeightChange = { [weak self] height in self?.resize(to: height) }
        viewModel.onActivity = { [weak self] in self?.noteActivity() }
        viewModel.onHideRequest = { [weak self] in self?.hide() }
        viewModel.onNeedsAttention = { [weak self] in
            guard let self = self, !self.isVisible else { return }
            self.show()
        }
        // Programmatic focus selects the whole field, so move the cursor to the end afterwards.
        viewModel.$focusToken
            .dropFirst()
            .sink { [weak self] _ in
                DispatchQueue.main.async { self?.moveCursorToEnd() }
            }
            .store(in: &cancellables)
    }

    func toggle() {
        if isVisible { hide() } else { show() }
    }

    func show() {
        position()
        NSApp.unhide(nil)                         // hide() hides the whole app
        NSApp.activate(ignoringOtherApps: true)   // makes sure typing reaches the text field
        panel.makeKeyAndOrderFront(nil)
        noteActivity()
        DispatchQueue.main.async { [weak self] in self?.viewModel.windowShown() }
    }

    func hide() {
        idleTimer?.invalidate()
        idleTimer = nil
        panel.orderOut(nil)
        NSApp.hide(nil)   // hands keyboard focus back to the app the user was in
        viewModel.windowHidden()
    }

    // MARK: - Layout

    private func position() {
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) } ?? NSScreen.main
        guard let visible = screen?.visibleFrame else { return }
        let size = panel.frame.size
        let origin = NSPoint(x: visible.midX - size.width / 2, y: visible.maxY - size.height - 12)
        panel.setFrameOrigin(origin)
    }

    /// Resizes while keeping the top edge where it is, so the pill grows downward.
    private func resize(to height: Double) {
        var frame = panel.frame
        let top = frame.maxY
        frame.size = NSSize(width: CapsuleLayout.width, height: CGFloat(height))
        frame.origin.y = top - frame.size.height
        panel.setFrame(frame, display: true, animate: false)
        panel.invalidateShadow()
    }

    private func moveCursorToEnd() {
        guard let editor = panel.firstResponder as? NSTextView else { return }
        editor.setSelectedRange(NSRange(location: editor.string.utf16.count, length: 0))
    }

    // MARK: - Idle auto-hide (one timer, re-armed on activity; nothing polls)

    func noteActivity() {
        lastActivity = Date()
        armIdleTimer()
    }

    private func armIdleTimer() {
        idleTimer?.invalidate()
        idleTimer = nil
        guard isVisible, idlePolicy.isEnabled else { return }
        let timer = Timer(timeInterval: idlePolicy.hideAfter, repeats: false) { [weak self] _ in
            Task { @MainActor in self?.idleTimerFired() }
        }
        timer.tolerance = 1
        RunLoop.main.add(timer, forMode: .common)
        idleTimer = timer
    }

    private func idleTimerFired() {
        guard isVisible else { return }
        if idlePolicy.shouldHide(phase: viewModel.state.phase, lastActivity: lastActivity, now: Date()) {
            hide()
        } else {
            armIdleTimer()   // busy, or there was activity: check again later
        }
    }
}
