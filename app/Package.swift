// swift-tools-version:5.7
import PackageDescription

var targets: [Target] = [
    // All logic lives here. Foundation only, so it builds and tests without AppKit.
    .target(name: "CapsuleCore"),
    .testTarget(name: "CapsuleCoreTests", dependencies: ["CapsuleCore"]),
]

#if os(macOS)
// The window, hotkey and views. AppKit, SwiftUI and Carbon only; no third-party packages.
targets.append(.executableTarget(name: "AssistantApp", dependencies: ["CapsuleCore"]))
#endif

let package = Package(
    name: "Assistant",
    platforms: [.macOS(.v12)],
    targets: targets
)
