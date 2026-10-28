import Foundation

/// A JSON value. Tool arguments vary by tool, so they are kept generic. Used for display only.
public enum JSONValue: Codable, Equatable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case null
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            self = .null
        } else if let v = try? c.decode(Int.self) {
            self = .int(v)
        } else if let v = try? c.decode(Double.self) {
            self = .double(v)
        } else if let v = try? c.decode(Bool.self) {
            self = .bool(v)
        } else if let v = try? c.decode(String.self) {
            self = .string(v)
        } else if let v = try? c.decode([JSONValue].self) {
            self = .array(v)
        } else if let v = try? c.decode([String: JSONValue].self) {
            self = .object(v)
        } else {
            throw DecodingError.dataCorruptedError(in: c, debugDescription: "Unsupported JSON value")
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let v): try c.encode(v)
        case .int(let v): try c.encode(v)
        case .double(let v): try c.encode(v)
        case .bool(let v): try c.encode(v)
        case .null: try c.encodeNil()
        case .array(let v): try c.encode(v)
        case .object(let v): try c.encode(v)
        }
    }

    public var stringValue: String? {
        if case .string(let v) = self { return v }
        return nil
    }

    /// Numbers only. A JSON true/false is never a number.
    public var numberValue: Double? {
        switch self {
        case .int(let v): return Double(v)
        case .double(let v): return v
        default: return nil
        }
    }
}

/// What the safety layer decided. An unknown value fails to decode, and the UI treats that as an error.
public enum Outcome: String, Codable, Equatable {
    case allow
    case confirm
    case block
    case dryRun = "dry_run"
}

/// The `guard` object returned by POST /route and POST /confirm.
public struct GuardDecision: Codable, Equatable {
    public let outcome: Outcome
    public let authorized: Bool
    public let risk: String
    public let reason: String
    public let message: String
    public let tool: String
    public let args: [String: JSONValue]
    public let confirmationId: String?
    public let requiresAcknowledgment: Bool

    enum CodingKeys: String, CodingKey {
        case outcome, authorized, risk, reason, message, tool, args
        case confirmationId = "confirmation_id"
        case requiresAcknowledgment = "requires_acknowledgment"
    }

    public init(
        outcome: Outcome, authorized: Bool, risk: String = "low", reason: String = "",
        message: String = "", tool: String, args: [String: JSONValue] = [:],
        confirmationId: String? = nil, requiresAcknowledgment: Bool = false
    ) {
        self.outcome = outcome
        self.authorized = authorized
        self.risk = risk
        self.reason = reason
        self.message = message
        self.tool = tool
        self.args = args
        self.confirmationId = confirmationId
        self.requiresAcknowledgment = requiresAcknowledgment
    }
}

/// The response of POST /route.
public struct RouteResponse: Codable, Equatable {
    public let tool: String
    public let args: [String: JSONValue]
    public let requiresConfirmation: Bool
    public let fallback: Bool
    public let note: String
    public let attempts: Int
    public let decision: GuardDecision

    enum CodingKeys: String, CodingKey {
        case tool, args, fallback, note, attempts
        case requiresConfirmation = "requires_confirmation"
        case decision = "guard"
    }

    public init(
        tool: String, args: [String: JSONValue] = [:], requiresConfirmation: Bool = false,
        fallback: Bool = false, note: String = "", attempts: Int = 1, decision: GuardDecision
    ) {
        self.tool = tool
        self.args = args
        self.requiresConfirmation = requiresConfirmation
        self.fallback = fallback
        self.note = note
        self.attempts = attempts
        self.decision = decision
    }
}
