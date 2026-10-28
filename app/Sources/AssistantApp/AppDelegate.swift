import AppKit
import CapsuleCore

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var controller: CapsuleController!
    private var viewModel: CapsuleViewModel!
    private var panelController: PanelController!
    private var statusItem: NSStatusItem?
    private var hotkey: GlobalHotkey?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let loaded = UIConfig.load()
        var notices = loaded.warnings
        let config = loaded.config

        let client: BackendClient
        do {
            client = try BackendClient(baseURL: config.backendURL)
        } catch {
            // Cannot happen with a validated config; fall back to the default address anyway.
            client = try! BackendClient(baseURL: UIConfig.defaultBackendURL)
        }

        controller = CapsuleController(
            client: client,
            voice: LocalVoiceInput(client: client, recorder: MicRecorder()),
            runner: UnavailableRunner(),      // replaced in phase 5
            speech: LocalSpeech(client: client, player: AudioPlayer()),
            speakMode: config.speakReplies
        )
        viewModel = CapsuleViewModel(controller: controller)
        panelController = PanelController(
            viewModel: viewModel,
            idlePolicy: IdlePolicy(hideAfter: config.hideAfterSeconds)
        )

        hotkey = GlobalHotkey(spec: config.hotkey) { [weak self] in
            Task { @MainActor in self?.panelController.toggle() }
        }
        if hotkey == nil {
            notices.append(
                "The shortcut \(config.hotkey.text) is already used by something else. "
                + "Use the menu bar icon, or change \"hotkey\" in ui.json."
            )
        }

        makeStatusItem(hotkeyText: hotkey == nil ? nil : config.hotkey.text)
        panelController.show()
        if !notices.isEmpty {
            controller.notice(notices.joined(separator: " "), tone: .warning)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        hotkey = nil   // unregisters the shortcut
    }

    // MARK: - Menu bar item (a reliable way in even if the shortcut is taken)

    private func makeStatusItem(hotkeyText: String?) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            if let image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "Assistant") {
                button.image = image
            } else {
                button.title = "AI"
            }
        }

        let menu = NSMenu()
        let toggleTitle = hotkeyText.map { "Show / Hide Assistant (\($0))" } ?? "Show / Hide Assistant"
        let toggle = NSMenuItem(title: toggleTitle, action: #selector(togglePanel), keyEquivalent: "")
        toggle.target = self
        menu.addItem(toggle)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit Assistant", action: #selector(quitApp), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        item.menu = menu
        statusItem = item
    }

    @objc private func togglePanel() {
        panelController.toggle()
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }
}
