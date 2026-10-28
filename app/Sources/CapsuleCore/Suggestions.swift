import Foundation

public struct Suggestion: Equatable, Identifiable {
    public let title: String
    public let prefill: String
    /// A complete request is sent right away. Others only fill the text field for the user to finish.
    public let sendsImmediately: Bool
    public var id: String { title }
}

public enum Suggestions {
    public static let all: [Suggestion] = [
        Suggestion(title: "Open App", prefill: "open ", sendsImmediately: false),
        Suggestion(title: "Open File", prefill: "open the file called ", sendsImmediately: false),
        Suggestion(title: "Search Chrome", prefill: "search chrome for ", sendsImmediately: false),
        Suggestion(title: "Play Media", prefill: "play ", sendsImmediately: false),
        Suggestion(title: "Set Reminder", prefill: "remind me to ", sendsImmediately: false),
        Suggestion(title: "Organize Desktop", prefill: "organize my desktop", sendsImmediately: true),
    ]

    /// Two rows of three, so every chip is reachable without scrolling.
    public static var rows: [[Suggestion]] {
        [Array(all.prefix(3)), Array(all.dropFirst(3))]
    }
}
