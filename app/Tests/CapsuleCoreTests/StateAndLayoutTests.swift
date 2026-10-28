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
        XCTAssertTrue(Phase.transcribing.isBusy)
        XCTAssertTrue(Phase.speaking.isBusy)
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

    func testVoiceInputStates() {
        var s = reduce(CapsuleState(), .listening)
        XCTAssertEqual(s.phase, .listening)
        s = reduce(s, .transcribing)
        XCTAssertEqual(s.phase, .transcribing)
        s = reduce(s, .started)                 // the transcript became a request
        XCTAssertEqual(s.phase, .thinking)
    }

    func testTranscribingOnlyFollowsListening() {
        XCTAssertEqual(reduce(CapsuleState(), .transcribing).phase, .idle)
        XCTAssertEqual(reduce(reduce(CapsuleState(), .started), .transcribing).phase, .thinking)
    }

    func testCancellingVoiceReturnsToIdle() {
        XCTAssertEqual(reduce(reduce(CapsuleState(), .listening), .voiceCancelled).phase, .idle)
        let transcribing = reduce(reduce(CapsuleState(), .listening), .transcribing)
        XCTAssertEqual(reduce(transcribing, .voiceCancelled).phase, .idle)
        // it must not interrupt other work
        let thinking = reduce(CapsuleState(), .started)
        XCTAssertEqual(reduce(thinking, .voiceCancelled).phase, .thinking)
    }

    func testSpeakingFollowsAFinishedResultAndKeepsItOnScreen() {
        var s = reduce(CapsuleState(), .finished(message: "Hello there.", tone: .success, lastAction: "reply"))
        s = reduce(s, .speaking)
        XCTAssertEqual(s.phase, .speaking)
        XCTAssertEqual(s.message, "Hello there.")
        XCTAssertEqual(s.tone, .success)
        s = reduce(s, .speechEnded)
        XCTAssertEqual(s.phase, .done)
        XCTAssertEqual(s.message, "Hello there.")
    }

    func testSpeakingOnlyAppliesToAFinishedResult() {
        XCTAssertEqual(reduce(CapsuleState(), .speaking).phase, .idle)
        XCTAssertEqual(reduce(reduce(CapsuleState(), .started), .speaking).phase, .thinking)
        XCTAssertEqual(reduce(reduce(CapsuleState(), .started), .speechEnded).phase, .thinking)
    }

    func testSpeechFailureKeepsTheResultAndSaysWhy() {
        var s = reduce(CapsuleState(), .finished(message: "Hello.", tone: .neutral, lastAction: "reply"))
        s = reduce(s, .speaking)
        s = reduce(s, .speechFailed("couldn't speak: voice output isn't set up"))
        XCTAssertEqual(s.phase, .done)
        XCTAssertEqual(s.message, "Hello.")
        XCTAssertEqual(s.lastAction, "reply · couldn't speak: voice output isn't set up")
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
        for event in [Event.started, .listening, .waiting(pending), .executing("x")] + [Event.transcribing] {
            let before = reduce(CapsuleState(), event)
            XCTAssertEqual(reduce(before, .dismissed), before)
        }
    }

    func testEveryPhaseHasAPlaceholder() {
        let phases: [Phase] = [.idle, .listening, .transcribing, .thinking, .waiting, .executing, .done, .speaking, .error]
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
        for phase in [Phase.idle, .listening, .transcribing, .thinking, .executing, .done, .speaking, .error] {
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

    // MARK: - choosing between matches

    private let choice = ChoicePrompt(
        choiceId: "c1", prompt: "I found 2 files. Which one?",
        options: [ChoiceOption(id: "1", label: "a.pdf", detail: "~/Documents"), ChoiceOption(id: "2", label: "a.pdf", detail: "~/Downloads")]
    )

    func testAQuestionWaitsForTheUser() {
        let s = reduce(CapsuleState(), .choosing(choice))
        XCTAssertEqual(s.phase, .waiting)
        XCTAssertTrue(s.phase.isBusy)
        XCTAssertEqual(s.message, choice.prompt)
        XCTAssertEqual(s.choice, choice)
        XCTAssertNil(s.pending)
    }

    func testAnythingElseClearsTheQuestion() {
        let asking = reduce(CapsuleState(), .choosing(choice))
        let next: [Event] = [
            .started, .executing("x"), .failed("boom"), .notice("n", .warning),
            .finished(message: "m", tone: .neutral, lastAction: nil), .waiting(pending),
        ]
        for event in next {
            XCTAssertNil(reduce(asking, event).choice, "\(event)")
        }
    }

    func testAQuestionSurvivesHidingTheWindow() {
        let asking = reduce(CapsuleState(), .choosing(choice))
        XCTAssertEqual(reduce(asking, .dismissed), asking)
    }

    func testTheQuestionShowsOneButtonPerOptionAndCancel() {
        let layout = CapsuleLayout(state: reduce(CapsuleState(), .choosing(choice)), inputIsEmpty: true)
        XCTAssertTrue(layout.showChoices)
        XCTAssertEqual(layout.choiceCount, 2)
        XCTAssertFalse(layout.showChips)
        XCTAssertFalse(layout.showConfirm)
        XCTAssertEqual(CapsuleLayout.choices(count: 2), 3 * 26 + 2 * 4)
        XCTAssertEqual(layout.height, 16 + 40 + 6 + 54 + 6 + 86)
    }

    func testTheLastActionLabelMakesRoomForTheQuestion() {
        var s = reduce(CapsuleState(), .executing("earlier"))
        s = reduce(s, .choosing(choice))
        XCTAssertFalse(CapsuleLayout(state: s, inputIsEmpty: true).showLastAction)
    }

    func testFiveOptionsStillFitOnScreen() {
        let many = ChoicePrompt(choiceId: "c", prompt: "Which?", options: (1...5).map { ChoiceOption(id: "\($0)", label: "f\($0)") })
        let height = CapsuleLayout(state: reduce(CapsuleState(), .choosing(many)), inputIsEmpty: true).height
        XCTAssertLessThan(height, 400)
    }
}
