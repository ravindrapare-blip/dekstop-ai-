import AppKit

@main
struct AssistantMain {
    @MainActor
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()      // `app.delegate` is weak, so keep this alive
        app.delegate = delegate
        app.setActivationPolicy(.accessory)   // no Dock icon, no app menu
        app.run()
    }
}
