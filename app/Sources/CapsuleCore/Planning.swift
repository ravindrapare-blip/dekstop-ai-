import Foundation

/// What to do with a backend answer. The UI never decides this itself.
public enum Step: Equatable {
    case reply(String)                                              // show the text
    case report(message: String, tone: Tone, lastAction: String?)   // the backend did (or could not do) the action
    case choose(ChoicePrompt)                                       // several matches: ask the user which
    case ask(PendingConfirmation)                                   // the safety layer wants a yes or no
    case refuse(String)                                             // blocked
    case preview(String)                                            // dry run
    case invalid(String)                                            // the backend sent something unusable
}

private let inconsistent = "The assistant sent an inconsistent answer."

/// Arguments that are bookkeeping, or private (text typed into a page), and so are not shown in a label.
private let hiddenArguments: Set<String> = ["value", "content", "details", "page_hint", "field_kind", "plan_id", "into_path", "rule"]

/// "close_app" with {"app_name": "Safari"} becomes "close app (Safari)".
public func summarize(tool: String, args: [String: JSONValue]) -> String {
    let words = tool.replacingOccurrences(of: "_", with: " ")
    var seen = Set<String>()
    let details = args.keys.sorted().compactMap { key -> String? in
        guard !hiddenArguments.contains(key), let value = args[key]?.stringValue, !value.isEmpty else { return nil }
        return seen.insert(value.lowercased()).inserted ? value : nil      // "click text (Send, send)" would repeat itself
    }
    return details.isEmpty ? words : "\(words) (\(details.joined(separator: ", ")))"
}

/// A decision on its own, without a result.
public func plan(_ decision: GuardDecision) -> Step {
    switch decision.outcome {
    case .allow:
        // "allow" must come with authorized == true. Anything else is not trusted.
        guard decision.authorized else { return .invalid(inconsistent) }
        if decision.tool == "reply" {
            let text = decision.args["text"]?.stringValue ?? ""
            return .reply(text.isEmpty ? "Done." : text)
        }
        // An allowed action always arrives with a result saying what happened.
        return .invalid("The assistant didn't say what happened.")
    case .confirm:
        guard !decision.authorized, let id = decision.confirmationId, !id.isEmpty else {
            return .invalid(inconsistent)
        }
        return .ask(PendingConfirmation(
            id: id,
            message: decision.message,
            requiresAcknowledgment: decision.requiresAcknowledgment,
            actionLabel: summarize(tool: decision.tool, args: decision.args)
        ))
    case .block:
        guard !decision.authorized else { return .invalid(inconsistent) }
        return .refuse(decision.message.isEmpty ? decision.reason : decision.message)
    case .dryRun:
        guard !decision.authorized else { return .invalid(inconsistent) }
        return .preview(decision.message)
    }
}

/// The general case. A result is only believed if it fits the decision beside it:
/// an action can only have been done or attempted if the safety layer authorized it.
func plan(decision: GuardDecision?, result: ExecutionResult?, fallbackLabel: String) -> Step {
    guard let result = result else {
        guard let decision = decision else { return .invalid(inconsistent) }
        return plan(decision)
    }

    let label = decision.map { summarize(tool: $0.tool, args: $0.args) } ?? fallbackLabel
    switch result.status {
    case .choose:
        guard decision == nil, let choice = result.choice, !choice.options.isEmpty else { return .invalid(inconsistent) }
        return .choose(choice)
    case .done:
        guard decision?.authorized == true else { return .invalid(inconsistent) }
        return .report(message: result.message, tone: .success, lastAction: label.isEmpty ? nil : label)
    case .notAvailable:
        guard decision?.authorized == true else { return .invalid(inconsistent) }
        return .report(message: result.message, tone: .warning, lastAction: label.isEmpty ? nil : "\(label) (not run)")
    case .needsLogin:
        // The page wants the user to sign in. The assistant stops and waits; it never fills a password.
        guard decision?.authorized == true else { return .invalid(inconsistent) }
        return .report(message: result.message, tone: .warning, lastAction: label.isEmpty ? nil : "\(label) (waiting for you to sign in)")
    case .failed:
        // Either nothing was judged (a "not found", a cancelled or expired choice) or an authorized action failed.
        if let decision = decision, !decision.authorized { return .invalid(inconsistent) }
        return .report(message: result.message, tone: .warning, lastAction: label.isEmpty ? nil : "\(label) (failed)")
    }
}

public func plan(_ response: RouteResponse) -> Step {
    plan(
        decision: response.decision,
        result: response.result,
        fallbackLabel: summarize(tool: response.tool, args: response.args)
    )
}

public func plan(_ response: ConfirmResponse) -> Step {
    plan(decision: response.decision, result: response.result, fallbackLabel: "")
}
