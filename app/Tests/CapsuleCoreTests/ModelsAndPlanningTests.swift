import XCTest
@testable import CapsuleCore

final class ModelsAndPlanningTests: XCTestCase {
    private let routeFixtures = [
        "route_allow_open_app", "route_reply", "route_confirm_close_app", "route_confirm_high_risk", "route_block_url",
        "route_dry_run", "route_fallback", "route_not_found", "route_not_available", "route_search",
        "route_open_file", "route_choose_file", "choose_done", "choose_expired",
        "route_browser_search", "route_needs_login", "route_browser_confirm_click", "route_create_reminder", "route_list_tasks", "route_organize_preview",
    ]

    func testEveryBackendFixtureDecodes() throws {
        for name in routeFixtures {
            XCTAssertNoThrow(try Fixtures.route(name), name)
        }
        for name in ["confirm_done", "confirm_expired", "confirm_organize_done"] {
            XCTAssertNoThrow(try Fixtures.confirm(name), name)
        }
    }

    func testRouteResponseFields() throws {
        let r = try Fixtures.route("route_allow_open_app")
        XCTAssertEqual(r.tool, "open_app")
        XCTAssertEqual(r.args["app_name"], .string("Google Chrome"))
        XCTAssertFalse(r.requiresConfirmation)
        XCTAssertFalse(r.fallback)
        XCTAssertEqual(r.decision?.outcome, .allow)
        XCTAssertEqual(r.decision?.authorized, true)
        XCTAssertNil(r.decision?.confirmationId)
        XCTAssertEqual(r.result?.status, .done)
        XCTAssertEqual(r.result?.message, "Opened Google Chrome.")
    }

    func testAQuestionHasNoDecisionYet() throws {
        let r = try Fixtures.route("route_choose_file")
        XCTAssertNil(r.decision)
        XCTAssertEqual(r.result?.status, .choose)
        XCTAssertEqual(r.result?.choice?.choiceId, "c0ffee123456")
        XCTAssertEqual(r.result?.choice?.options.map { $0.label }, ["report.pdf", "report.pdf"])
        XCTAssertEqual(Set(r.result?.choice?.options.map { $0.detail } ?? []), ["~/Documents", "~/Downloads"])
    }

    func testConfirmResponseCarriesTheDecisionAndTheResult() throws {
        let r = try Fixtures.confirm("confirm_done")
        XCTAssertTrue(r.decision.authorized)
        XCTAssertEqual(r.decision.tool, "close_app")
        XCTAssertEqual(r.result?.status, .done)
        XCTAssertNil(try Fixtures.confirm("confirm_expired").result)
    }

    func testUnknownValuesDoNotDecode() {
        let outcome = #"{"outcome":"maybe","authorized":true,"risk":"low","reason":"","message":"","tool":"x","args":{},"confirmation_id":null,"requires_acknowledgment":false}"#
        XCTAssertThrowsError(try JSONDecoder().decode(GuardDecision.self, from: Data(outcome.utf8)))
        let status = #"{"status":"exploded","message":"","items":[],"choice":null,"via":""}"#
        XCTAssertThrowsError(try JSONDecoder().decode(ExecutionResult.self, from: Data(status.utf8)))
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

    // MARK: - plan: what the backend says becomes one step

    func testAnActionTheBackendDidIsReportedWithItsLabel() throws {
        XCTAssertEqual(
            plan(try Fixtures.route("route_allow_open_app")),
            .report(message: "Opened Google Chrome.", tone: .success, lastAction: "open app (Google Chrome)")
        )
    }

    func testTheLabelNamesTheRealTargetNotTheWords() throws {
        guard case .report(_, _, let label) = plan(try Fixtures.route("choose_done")) else { return XCTFail("expected a report") }
        XCTAssertEqual(label, "open file (/Users/me/Documents/report.pdf)")
    }

    func testSearchResultsAreReported() throws {
        guard case .report(let message, let tone, _) = plan(try Fixtures.route("route_search")) else { return XCTFail("expected a report") }
        XCTAssertEqual(message, "Found 2: budget.pdf, budget old.pdf")
        XCTAssertEqual(tone, .success)
    }

    func testNotFoundIsAWarningWithoutADecision() throws {
        let r = try Fixtures.route("route_not_found")
        XCTAssertNil(r.decision)
        guard case .report(let message, let tone, let label) = plan(r) else { return XCTFail("expected a report") }
        XCTAssertTrue(message.contains("Photoshop"))
        XCTAssertEqual(tone, .warning)
        XCTAssertEqual(label, "open app (Photoshop) (failed)")
    }

    func testAnUnbuiltToolIsNeverPresentedAsDone() throws {
        guard case .report(_, let tone, let label) = plan(try Fixtures.route("route_not_available")) else { return XCTFail("expected a report") }
        XCTAssertEqual(tone, .warning)
        XCTAssertTrue(label?.hasSuffix("(not run)") ?? false)
    }

    func testASignInPageStopsAndWaitsForTheUser() throws {
        let response = try Fixtures.route("route_needs_login")
        XCTAssertEqual(response.result?.status, .needsLogin)
        guard case .report(let message, let tone, let label) = plan(response) else { return XCTFail("expected a report") }
        XCTAssertTrue(message.contains("sign in"))
        XCTAssertEqual(tone, .warning)
        XCTAssertTrue(label?.hasSuffix("(waiting for you to sign in)") ?? false)
    }

    func testABrowserActionIsReportedWithItsSteps() throws {
        let response = try Fixtures.route("route_browser_search")
        XCTAssertEqual(response.result?.status, .done)
        XCTAssertEqual(response.result?.items.first, "Searching Google")
        guard case .report(_, let tone, let label) = plan(response) else { return XCTFail("expected a report") }
        XCTAssertEqual(tone, .success)
        XCTAssertEqual(label, "search chrome (AI tools)")
    }

    func testARiskyClickAsksFirstAndTheLabelHidesBookkeeping() throws {
        guard case .ask(let pending) = plan(try Fixtures.route("route_browser_confirm_click")) else { return XCTFail("expected a question") }
        XCTAssertFalse(pending.requiresAcknowledgment)
        XCTAssertEqual(pending.actionLabel, "click text (Send)")
        XCTAssertEqual(summarize(tool: "fill_field", args: ["label": .string("Message"), "value": .string("private words"),
                                                              "field_kind": .string("text"), "nth": .int(0)]), "fill field (Message)")
    }

    func testMemoryResultsAreReportedWithTheirLines() throws {
        let made = try Fixtures.route("route_create_reminder")
        guard case .report(let message, let tone, let label) = plan(made) else { return XCTFail("expected a report") }
        XCTAssertEqual(message, "I'll remind you to call mom today 6:00 PM.")
        XCTAssertEqual(tone, .success)
        XCTAssertEqual(label, "create reminder (2026-01-05T18:00:00, call mom)")
        let tasks = try Fixtures.route("route_list_tasks")
        XCTAssertEqual(tasks.result?.items.count, 2)
        XCTAssertEqual(tasks.result?.items.first, "#1  Write report — due tomorrow")
    }

    func testOrganizingShowsThePreviewAndAsksBeforeMoving() throws {
        guard case .ask(let pending) = plan(try Fixtures.route("route_organize_preview")) else { return XCTFail("expected a question") }
        XCTAssertTrue(pending.message.hasPrefix("In ~/Desktop I would move 3 files:"))
        XCTAssertTrue(pending.message.contains("• Documents (new folder), 1: report.pdf"))
        XCTAssertTrue(pending.message.hasSuffix("Nothing moves until you confirm."))
        XCTAssertFalse(pending.requiresAcknowledgment)
        XCTAssertEqual(pending.actionLabel, "organize desktop (/Users/me/Desktop)")
        let done = try Fixtures.confirm("confirm_organize_done")
        guard case .report(let message, let tone, _) = plan(done) else { return XCTFail("expected a report") }
        XCTAssertTrue(message.hasPrefix("Moved 3 files in your Desktop folder"))
        XCTAssertEqual(tone, .success)
    }

    func testPrivateTextIsNotShownInAnActionLabel() {
        XCTAssertEqual(summarize(tool: "save_note", args: ["title": .string("Diary"), "content": .string("private thoughts")]), "save note (Diary)")
        XCTAssertEqual(summarize(tool: "create_task", args: ["title": .string("Visit"), "details": .string("12 Elm Street")]), "create task (Visit)")
    }

    func testReplyShowsItsText() throws {
        XCTAssertEqual(plan(try Fixtures.route("route_reply")), .reply("Hello there."))
        guard case .reply = plan(try Fixtures.route("route_fallback")) else { return XCTFail("expected reply") }
    }

    func testReplyWithoutTextStillSaysSomething() {
        let d = GuardDecision(outcome: .allow, authorized: true, tool: "reply", args: [:])
        XCTAssertEqual(plan(d), .reply("Done."))
    }

    func testSeveralMatchesBecomeAQuestion() throws {
        guard case .choose(let choice) = plan(try Fixtures.route("route_choose_file")) else { return XCTFail("expected choose") }
        XCTAssertEqual(choice.options.count, 2)
        XCTAssertTrue(choice.prompt.hasSuffix("Which one?"))
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

    func testApprovedConfirmationIsReported() throws {
        XCTAssertEqual(
            plan(try Fixtures.confirm("confirm_done")),
            .report(message: "Asked Safari to quit.", tone: .success, lastAction: "close app (Safari)")
        )
    }

    func testExpiredConfirmationIsRefused() throws {
        guard case .refuse = plan(try Fixtures.confirm("confirm_expired")) else { return XCTFail("expected refuse") }
    }

    func testAnExpiredChoiceIsReportedWithoutALabelOfItsOwn() throws {
        guard case .report(let message, _, let label) = plan(try Fixtures.route("choose_expired")) else { return XCTFail("expected a report") }
        XCTAssertTrue(message.contains("expired"))
        XCTAssertNil(label)
    }

    // MARK: - plan: answers that contradict themselves are never trusted

    private func result(_ status: ResultStatus, choice: ChoicePrompt? = nil) -> ExecutionResult {
        ExecutionResult(status: status, message: "msg", choice: choice)
    }

    func testAResultNeedsAnAuthorizingDecision() {
        let blocked = GuardDecision(outcome: .block, authorized: false, tool: "close_app")
        let confirm = GuardDecision(outcome: .confirm, authorized: false, tool: "close_app", confirmationId: "x")
        let dry = GuardDecision(outcome: .dryRun, authorized: false, tool: "close_app")
        for decision in [blocked, confirm, dry] {
            for status in [ResultStatus.done, .notAvailable, .needsLogin, .failed] {
                guard case .invalid = plan(decision: decision, result: result(status), fallbackLabel: "") else {
                    return XCTFail("\(status) beside \(decision.outcome) must not be shown")
                }
            }
        }
        for status in [ResultStatus.done, .notAvailable, .needsLogin] {
            guard case .invalid = plan(decision: nil, result: result(status), fallbackLabel: "x") else {
                return XCTFail("\(status) with no decision at all must not be shown")
            }
        }
    }

    func testAQuestionMustHaveOptionsAndNoDecision() {
        let allowed = GuardDecision(outcome: .allow, authorized: true, tool: "open_file")
        let choice = ChoicePrompt(choiceId: "c", prompt: "Which?", options: [ChoiceOption(id: "1", label: "a")])
        let empty = ChoicePrompt(choiceId: "c", prompt: "Which?", options: [])
        guard case .invalid = plan(decision: allowed, result: result(.choose, choice: choice), fallbackLabel: "") else { return XCTFail("a question beside a decision") }
        guard case .invalid = plan(decision: nil, result: result(.choose, choice: empty), fallbackLabel: "") else { return XCTFail("a question with no options") }
        guard case .invalid = plan(decision: nil, result: result(.choose), fallbackLabel: "") else { return XCTFail("a question with no choice") }
        guard case .choose = plan(decision: nil, result: result(.choose, choice: choice), fallbackLabel: "") else { return XCTFail("a good question") }
    }

    func testInconsistentDecisionsAreNeverTrusted() {
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

    func testAnAllowedActionWithoutAResultIsInvalid() {
        let allowed = GuardDecision(outcome: .allow, authorized: true, tool: "open_app", args: ["app_name": .string("Safari")])
        guard case .invalid = plan(allowed) else { return XCTFail("expected invalid") }
        guard case .invalid = plan(decision: nil, result: nil, fallbackLabel: "") else { return XCTFail("nothing at all") }
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
