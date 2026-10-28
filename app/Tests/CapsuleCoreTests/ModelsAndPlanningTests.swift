import XCTest
@testable import CapsuleCore

final class ModelsAndPlanningTests: XCTestCase {
    func testEveryBackendFixtureDecodes() throws {
        for name in ["route_allow_open_app", "route_reply", "route_confirm_close_app", "route_confirm_high_risk",
                     "route_block_url", "route_dry_run", "route_fallback"] {
            XCTAssertNoThrow(try Fixtures.route(name), name)
        }
        for name in ["confirm_allow", "confirm_expired"] {
            XCTAssertNoThrow(try Fixtures.decision(name), name)
        }
    }

    func testRouteResponseFields() throws {
        let r = try Fixtures.route("route_allow_open_app")
        XCTAssertEqual(r.tool, "open_app")
        XCTAssertEqual(r.args["app_name"], .string("Google Chrome"))
        XCTAssertFalse(r.requiresConfirmation)
        XCTAssertFalse(r.fallback)
        XCTAssertEqual(r.decision.outcome, .allow)
        XCTAssertTrue(r.decision.authorized)
        XCTAssertNil(r.decision.confirmationId)
    }

    func testUnknownOutcomeDoesNotDecode() {
        let json = #"{"outcome":"maybe","authorized":true,"risk":"low","reason":"","message":"","tool":"x","args":{},"confirmation_id":null,"requires_acknowledgment":false}"#
        XCTAssertThrowsError(try JSONDecoder().decode(GuardDecision.self, from: Data(json.utf8)))
    }

    func testJSONValueDecoding() throws {
        let json = #"{"a":1,"b":2.5,"c":true,"d":null,"e":"x","f":[1,"y"],"g":{"h":false}}"#
        let v = try JSONDecoder().decode([String: JSONValue].self, from: Data(json.utf8))
        XCTAssertEqual(v["a"], .int(1))
        XCTAssertEqual(v["b"], .double(2.5))
        XCTAssertEqual(v["c"], .bool(true))
        XCTAssertEqual(v["d"], .null)
        XCTAssertEqual(v["e"]?.stringValue, "x")
        XCTAssertEqual(v["f"], .array([.int(1), .string("y")]))
        XCTAssertEqual(v["g"], .object(["h": .bool(false)]))
        XCTAssertEqual(v["a"]?.numberValue, 1)
        XCTAssertNil(v["c"]?.numberValue)
    }

    // MARK: - plan

    func testAuthorizedActionIsRun() throws {
        guard case .run(let action) = plan(try Fixtures.route("route_allow_open_app")) else { return XCTFail("expected run") }
        XCTAssertEqual(action.tool, "open_app")
        XCTAssertEqual(action.summary, "open app (Google Chrome)")
    }

    func testReplyShowsItsText() throws {
        XCTAssertEqual(plan(try Fixtures.route("route_reply")), .reply("Hello there."))
    }

    func testReplyWithoutTextStillSaysSomething() {
        let d = GuardDecision(outcome: .allow, authorized: true, tool: "reply", args: [:])
        XCTAssertEqual(plan(d), .reply("Done."))
    }

    func testConfirmationIsAsked() throws {
        guard case .ask(let pending) = plan(try Fixtures.route("route_confirm_close_app")) else { return XCTFail("expected ask") }
        XCTAssertEqual(pending.id, "abc123def456")
        XCTAssertTrue(pending.message.hasSuffix("Please confirm."))
        XCTAssertFalse(pending.requiresAcknowledgment)
        XCTAssertEqual(pending.actionLabel, "close app (Safari)")
    }

    func testHighRiskNeedsAcknowledgment() throws {
        guard case .ask(let pending) = plan(try Fixtures.route("route_confirm_high_risk")) else { return XCTFail("expected ask") }
        XCTAssertTrue(pending.requiresAcknowledgment)
    }

    func testBlockedIsRefused() throws {
        guard case .refuse(let text) = plan(try Fixtures.route("route_block_url")) else { return XCTFail("expected refuse") }
        XCTAssertTrue(text.hasPrefix("I can't do that"))
    }

    func testDryRunIsAPreview() throws {
        guard case .preview(let text) = plan(try Fixtures.route("route_dry_run")) else { return XCTFail("expected preview") }
        XCTAssertTrue(text.hasPrefix("Dry run:"))
    }

    func testFallbackReplyIsJustAReply() throws {
        guard case .reply = plan(try Fixtures.route("route_fallback")) else { return XCTFail("expected reply") }
    }

    func testApprovedConfirmationIsRun() throws {
        guard case .run(let action) = plan(try Fixtures.decision("confirm_allow")) else { return XCTFail("expected run") }
        XCTAssertEqual(action.tool, "close_app")
        XCTAssertEqual(action.args["app_name"], .string("Safari"))
    }

    func testExpiredConfirmationIsRefused() throws {
        guard case .refuse = plan(try Fixtures.decision("confirm_expired")) else { return XCTFail("expected refuse") }
    }

    func testInconsistentDecisionsAreNeverRun() {
        // "allow" that is not authorized, "block" that is authorized, "confirm" with no id.
        let cases = [
            GuardDecision(outcome: .allow, authorized: false, tool: "open_app"),
            GuardDecision(outcome: .block, authorized: true, tool: "open_app"),
            GuardDecision(outcome: .dryRun, authorized: true, tool: "open_app"),
            GuardDecision(outcome: .confirm, authorized: false, tool: "close_app", confirmationId: nil),
            GuardDecision(outcome: .confirm, authorized: false, tool: "close_app", confirmationId: ""),
            GuardDecision(outcome: .confirm, authorized: true, tool: "close_app", confirmationId: "x"),
        ]
        for decision in cases {
            guard case .invalid = plan(decision) else { return XCTFail("expected invalid for \(decision)") }
        }
    }

    func testSummaries() {
        XCTAssertEqual(summarize(tool: "get_time", args: [:]), "get time")
        XCTAssertEqual(summarize(tool: "close_app", args: ["app_name": .string("Safari")]), "close app (Safari)")
        XCTAssertEqual(
            summarize(tool: "create_reminder", args: ["text": .string("call mom"), "due_datetime": .string("2025-01-01T18:00:00")]),
            "create reminder (2025-01-01T18:00:00, call mom)"
        )
        XCTAssertEqual(summarize(tool: "set_volume", args: ["level": .int(30)]), "set volume")
    }
}
