import Foundation

/// What to do with a backend decision. The UI never decides this itself.
public enum Step: Equatable {
    case reply(String)                     // show the text
    case run(AuthorizedAction)             // the safety layer authorized it; hand it to the runner
    case ask(PendingConfirmation)          // ask the user first
    case refuse(String)                    // blocked
    case preview(String)                   // dry run
    case invalid(String)                   // the backend sent something unusable
}

/// An action the backend explicitly authorized. Only this module can create one.
public struct AuthorizedAction: Equatable {
    public let tool: String
    public let args: [String: JSONValue]
    public let summary: String

    init(tool: String, args: [String: JSONValue], summary: String) {
        self.tool = tool
        self.args = args
        self.summary = summary
    }
}

/// "close_app" with {"app_name": "Safari"} becomes "close app (Safari)".
public func summarize(tool: String, args: [String: JSONValue]) -> String {
    let words = tool.replacingOccurrences(of: "_", with: " ")
    let details = args.keys.sorted().compactMap { key -> String? in
        guard let value = args[key]?.stringValue, !value.isEmpty else { return nil }
        return value
    }
    return details.isEmpty ? words : "\(words) (\(details.joined(separator: ", ")))"
}

public func plan(_ decision: GuardDecision) -> Step {
    switch decision.outcome {
    case .allow:
        // "allow" must come with authorized == true. Anything else is not trusted.
        guard decision.authorized else { return .invalid("The assistant sent an inconsistent answer.") }
        if decision.tool == "reply" {
            let text = decision.args["text"]?.stringValue ?? ""
            return .reply(text.isEmpty ? "Done." : text)
        }
        return .run(AuthorizedAction(
            tool: decision.tool,
            args: decision.args,
            summary: summarize(tool: decision.tool, args: decision.args)
        ))
    case .confirm:
        guard !decision.authorized, let id = decision.confirmationId, !id.isEmpty else {
            return .invalid("The assistant sent an inconsistent answer.")
        }
        return .ask(PendingConfirmation(
            id: id,
            message: decision.message,
            requiresAcknowledgment: decision.requiresAcknowledgment,
            actionLabel: summarize(tool: decision.tool, args: decision.args)
        ))
    case .block:
        guard !decision.authorized else { return .invalid("The assistant sent an inconsistent answer.") }
        return .refuse(decision.message.isEmpty ? decision.reason : decision.message)
    case .dryRun:
        guard !decision.authorized else { return .invalid("The assistant sent an inconsistent answer.") }
        return .preview(decision.message)
    }
}

public func plan(_ response: RouteResponse) -> Step {
    plan(response.decision)
}
