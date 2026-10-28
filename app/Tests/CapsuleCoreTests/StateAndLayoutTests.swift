import XCTest
@testable import CapsuleCore

final class StateAndLayoutTests: XCTestCase {
    private let pending = PendingConfirmation(
        id: "abc", message: "I can do this: close app (Safari). Please confirm.",
        requiresAcknowledgment: false, actionLabel: "close app (Safari)"
    )

    // MARK: - state machine

    func testStartsIdleAndNotBusy() {
        let s = CapsuleState()
        XCTAssertEqual(s.phase, .idle)
        XCTAssertFalse(s.phase.isBusy)
    }

    func testBusyPhases() {
        XCTAssertTrue(Phase.listening.isBusy)
        XCTAssertTrue(Phase.thinking.isBusy)
        XCTAssertTrue(Phase.waiting.isBusy)
        XCTAssertTrue(Phase.executing.isBusy)
        XCTAssertFalse(Phase.idle.isBusy)
        XCTAssertFalse(Phase.done.isBusy)
        XCTAssertFalse(Phase.error.isBusy)
    }

    func testRequestLifecycle() {
        var s = CapsuleState()
        s = reduce(s, .started)
        XCTAssertEqual(s.phase, .thinking)
        s = reduce(s, .executing("open app (Safari)"))
        XCTAssertEqual(s.phase, .executing)
        XCTAssertEqual(s.lastAction, "open app (Safari)")
        s = reduce(s, .finished(message: "Opened.", tone: .success, lastAction: nil))
        XCTAssertEqual(s.phase, .done)
        XCTAssertEqual(s.tone, .success)
        XCTAssertEqual(s.message, "Opened.")
        XCTAssertEqual(s.lastAction, "open app (Safari)")   // kept when no new label is given
    }

    func testStartingClearsPreviousMessageAndPending() {
        var s = reduce(CapsuleState(), .waiting(pending))
        s = reduce(s, .started)
        XCTAssertEqual(s.phase, .thinking)
        XCTAssertEqual(s.message, "")
        XCTAssertNil(s.pending)
    }

    func testWaitingKeepsThePendingRequest() {
        let s = reduce(CapsuleState(), .waiting(pending))
        XCTAssertEqual(s.phase, .waiting)
        XCTAssertEqual(s.pending, pending)
        XCTAssertEqual(s.message, pending.message)
        XCTAssertEqual(s.tone, .warning)
    }

    func testFinishingClearsPending() {
        var s = reduce(CapsuleState(), .waiting(pending))
        s = reduce(s, .finished(message: "Cancelled.", tone: .neutral, lastAction: "cancelled"))
        XCTAssertNil(s.pending)
    }

    func testFailureIsAnErrorState() {
        let s = reduce(CapsuleState(), .failed("boom"))
        XCTAssertEqual(s.phase, .error)
        XCTAssertEqual(s.tone, .error)
        XCTAssertEqual(s.message, "boom")
    }

    func testListeningTransitions() {
        var s = reduce(CapsuleState(), .listening)
        XCTAssertEqual(s.phase, .listening)
        s = reduce(s, .listeningEnded)
        XCTAssertEqual(s.phase, .idle)
        // listeningEnded must not interrupt other work
        s = reduce(CapsuleState(), .started)
        s = reduce(s, .listeningEnded)
        XCTAssertEqual(s.phase, .thinking)
    }

    func testNoticeReturnsToIdleWithAMessage() {
        let s = reduce(CapsuleState(), .notice("Voice isn't set up.", .warning))
        XCTAssertEqual(s.phase, .idle)
        XCTAssertEqual(s.tone, .warning)
        XCTAssertEqual(s.message, "Voice isn't set up.")
    }

    func testDismissingClearsAFinishedResultButKeepsLastAction() {
        var s = reduce(CapsuleState(), .finished(message: "Done.", tone: .success, lastAction: "reply"))
        s = reduce(s, .dismissed)
        XCTAssertEqual(s.phase, .idle)
        XCTAssertEqual(s.message, "")
        XCTAssertEqual(s.lastAction, "reply")
    }

    func testDismissingKeepsWorkInProgress() {
        for event in [Event.started, .listening, .waiting(pending), .executing("x")] {
            let before = reduce(CapsuleState(), event)
            XCTAssertEqual(reduce(before, .dismissed), before)
        }
    }

    func testEveryPhaseHasAPlaceholder() {
        let phases: [Phase] = [.idle, .listening, .thinking, .waiting, .executing, .done, .error]
        var seen = Set<String>()
        for phase in phases {
            var s = CapsuleState()
            s.phase = phase
            XCTAssertFalse(s.placeholder.isEmpty)
            seen.insert(s.placeholder)
        }
        XCTAssertEqual(seen.count, phases.count)   // each state reads differently
    }

    // MARK: - layout

    func testIdleShowsChips() {
        let layout = CapsuleLayout(state: CapsuleState(), inputIsEmpty: true)
        XCTAssertTrue(layout.showChips)
        XCTAssertFalse(layout.showMessage)
        XCTAssertEqual(layout.height, 16 + 40 + 6 + 58)
    }

    func testTypingHidesChipsAndShrinksToThePill() {
        let layout = CapsuleLayout(state: CapsuleState(), inputIsEmpty: false)
        XCTAssertFalse(layout.showChips)
        XCTAssertEqual(layout.height, 56)
    }

    func testResultShowsMessageAndLastAction() {
        var s = CapsuleState()
        s = reduce(s, .finished(message: "Done.", tone: .success, lastAction: "reply"))
        let layout = CapsuleLayout(state: s, inputIsEmpty: true)
        XCTAssertTrue(layout.showMessage)
        XCTAssertFalse(layout.showChips)
        XCTAssertTrue(layout.showLastAction)
        XCTAssertEqual(layout.height, 16 + 40 + 6 + 54 + 6 + 16)
    }

    func testConfirmationShowsButtonsAndHidesLastAction() {
        var s = reduce(CapsuleState(), .executing("earlier action"))
        s = reduce(s, .waiting(pending))
        let layout = CapsuleLayout(state: s, inputIsEmpty: true)
        XCTAssertTrue(layout.showConfirm)
        XCTAssertFalse(layout.showAcknowledge)
        XCTAssertFalse(layout.showLastAction)
        XCTAssertEqual(layout.height, 16 + 40 + 6 + 54 + 6 + 30)
    }

    func testHighRiskConfirmationAddsTheAcknowledgmentRow() {
        let risky = PendingConfirmation(id: "x", message: "risky", requiresAcknowledgment: true, actionLabel: "open folder (~/.ssh)")
        let layout = CapsuleLayout(state: reduce(CapsuleState(), .waiting(risky)), inputIsEmpty: true)
        XCTAssertTrue(layout.showAcknowledge)
        XCTAssertEqual(layout.height, 16 + 40 + 6 + 54 + 6 + 22 + 6 + 30)
    }

    func testHeightNeverBelowThePill() {
        for phase in [Phase.idle, .listening, .thinking, .executing, .done, .error] {
            var s = CapsuleState()
            s.phase = phase
            XCTAssertGreaterThanOrEqual(CapsuleLayout(state: s, inputIsEmpty: false).height, 56)
        }
    }

    // MARK: - suggestions

    func testSuggestionChips() {
        XCTAssertEqual(
            Suggestions.all.map { $0.title },
            ["Open App", "Open File", "Search Chrome", "Play Media", "Set Reminder", "Organize Desktop"]
        )
        XCTAssertEqual(Suggestions.rows.map { $0.count }, [3, 3])
        XCTAssertEqual(Set(Suggestions.all.map { $0.id }).count, 6)
        XCTAssertEqual(Suggestions.all.filter { $0.sendsImmediately }.map { $0.title }, ["Organize Desktop"])
        for s in Suggestions.all where !s.sendsImmediately {
            XCTAssertTrue(s.prefill.hasSuffix(" "), "\(s.title) should leave room to keep typing")
        }
    }
}
