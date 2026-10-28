import XCTest
@testable import CapsuleCore
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

final class RecordingRunner: ActionRunner {
    private(set) var actions: [AuthorizedAction] = []
    var result: RunResult
    init(result: RunResult = .completed("Done.")) { self.result = result }
    func run(_ action: AuthorizedAction) async -> RunResult {
        actions.append(action)
        return result
    }
}

final class FakeVoice: VoiceInput {
    var isAvailable = true
    var unavailableReason = "Voice is off."
    private(set) var started = 0
    private(set) var stopped = 0
    private(set) var cancelled = 0
    private var callback: ((VoiceEvent) -> Void)?
    func start(onEvent: @escaping (VoiceEvent) -> Void) { started += 1; callback = onEvent }
    func stop() { stopped += 1 }
    func cancel() { cancelled += 1 }
    func emit(_ event: VoiceEvent) { callback?(event) }
}

@MainActor
final class ControllerTests: XCTestCase {
    private func make(
        _ transport: FakeTransport,
        runner: ActionRunner = RecordingRunner(),
        voice: VoiceInput = UnavailableVoiceInput(),
        speech: SpeechOutput = UnavailableSpeech(),
        mode: SpeakMode = .voiceOnly
    ) throws -> CapsuleController {
        CapsuleController(client: try makeClient(transport), voice: voice, runner: runner, speech: speech, speakMode: mode)
    }

    private func settle(_ c: CapsuleController) async {
        await waitUntil { !c.state.phase.isBusy || c.state.phase == .waiting }
    }

    // MARK: - typed requests

    func testReplyIsShown() async throws {
        let c = try make(.serving(["/route": "route_reply"]))
        XCTAssertTrue(c.submit("hi"))
        XCTAssertEqual(c.state.phase, .thinking)
        await settle(c)
        XCTAssertEqual(c.state.phase, .done)
        XCTAssertEqual(c.state.message, "Hello there.")
        XCTAssertEqual(c.state.lastAction, "reply")
    }

    func testStatesAreReportedInOrder() async throws {
        let c = try make(.serving(["/route": "route_allow_open_app"]), runner: RecordingRunner(result: .completed("Opened.")))
        var phases: [Phase] = []
        c.onChange = { phases.append($0.phase) }
        c.submit("open chrome")
        await settle(c)
        XCTAssertEqual(phases, [.thinking, .executing, .done])
    }

    func testAuthorizedActionReachesTheRunner() async throws {
        let runner = RecordingRunner(result: .completed("Opened Google Chrome."))
        let c = try make(.serving(["/route": "route_allow_open_app"]), runner: runner)
        c.submit("open chrome")
        await settle(c)
        XCTAssertEqual(runner.actions.map { $0.tool }, ["open_app"])
        XCTAssertEqual(c.state.tone, .success)
        XCTAssertEqual(c.state.message, "Opened Google Chrome.")
        XCTAssertEqual(c.state.lastAction, "open app (Google Chrome)")
    }

    func testWithoutARunnerNothingIsClaimedToHaveHappened() async throws {
        let c = try make(.serving(["/route": "route_allow_open_app"]), runner: UnavailableRunner())
        c.submit("open chrome")
        await settle(c)
        XCTAssertEqual(c.state.phase, .done)
        XCTAssertEqual(c.state.tone, .warning)
        XCTAssertTrue(c.state.message.contains("nothing was done"))
        XCTAssertEqual(c.state.lastAction, "open app (Google Chrome) (not run)")
    }

    func testRunnerFailureIsShownAsAnError() async throws {
        let c = try make(.serving(["/route": "route_allow_open_app"]), runner: RecordingRunner(result: .failed("No such app.")))
        c.submit("open chrome")
        await settle(c)
        XCTAssertEqual(c.state.tone, .error)
        XCTAssertEqual(c.state.lastAction, "open app (Google Chrome) (failed)")
    }

    func testBlockedRequestsAreRefusedAndNeverRun() async throws {
        let runner = RecordingRunner()
        let c = try make(.serving(["/route": "route_block_url"]), runner: runner)
        c.submit("go there")
        await settle(c)
        XCTAssertEqual(c.state.phase, .done)
        XCTAssertEqual(c.state.tone, .warning)
        XCTAssertTrue(c.state.message.hasPrefix("I can't do that"))
        XCTAssertTrue(runner.actions.isEmpty)
    }

    func testDryRunPreviewIsShownAndNeverRun() async throws {
        let runner = RecordingRunner()
        let c = try make(.serving(["/route": "route_dry_run"]), runner: runner)
        c.submit("quit safari")
        await settle(c)
        XCTAssertTrue(c.state.message.hasPrefix("Dry run:"))
        XCTAssertTrue(runner.actions.isEmpty)
    }

    func testInconsistentAnswerIsAnErrorAndNeverRun() async throws {
        let body = Data(#"{"tool":"open_app","args":{},"requires_confirmation":false,"fallback":false,"note":"","attempts":1,"guard":{"outcome":"allow","authorized":false,"risk":"low","reason":"","message":"","tool":"open_app","args":{},"confirmation_id":null,"requires_acknowledgment":false}}"#.utf8)
        let runner = RecordingRunner()
        let c = try make(FakeTransport { _ in (body, 200) }, runner: runner)
        c.submit("open")
        await settle(c)
        XCTAssertEqual(c.state.phase, .error)
        XCTAssertTrue(runner.actions.isEmpty)
    }

    // MARK: - confirmation

    func testApprovedConfirmationRunsExactlyTheAuthorizedAction() async throws {
        let runner = RecordingRunner(result: .completed("Closed Safari."))
        let transport = FakeTransport.serving(["/route": "route_confirm_close_app", "/confirm": "confirm_allow"])
        let c = try make(transport, runner: runner)
        c.submit("quit safari")
        await settle(c)
        XCTAssertEqual(c.state.phase, .waiting)
        XCTAssertEqual(c.state.pending?.id, "abc123def456")
        XCTAssertTrue(runner.actions.isEmpty)   // nothing runs before the user answers

        c.confirm(approved: true)
        await settle(c)
        XCTAssertEqual(c.state.phase, .done)
        XCTAssertEqual(runner.actions.map { $0.tool }, ["close_app"])
        XCTAssertEqual(runner.actions.first?.args["app_name"], .string("Safari"))
        let body = transport.body(of: try XCTUnwrap(transport.requests(to: "/confirm").first))
        XCTAssertEqual(body["confirmation_id"] as? String, "abc123def456")
        XCTAssertEqual(body["approved"] as? Bool, true)
    }

    func testCancellingNeverRunsAndDoesNotWaitForTheBackend() async throws {
        let runner = RecordingRunner()
        let transport = FakeTransport.serving(["/route": "route_confirm_close_app", "/confirm": "confirm_expired"])
        let c = try make(transport, runner: runner)
        c.submit("quit safari")
        await settle(c)

        c.confirm(approved: false)
        XCTAssertEqual(c.state.phase, .done)       // immediately, no waiting
        XCTAssertEqual(c.state.message, "Cancelled. I didn't do it.")
        XCTAssertNil(c.state.pending)
        await waitUntil { transport.requests(to: "/confirm").count == 1 }
        let body = transport.body(of: try XCTUnwrap(transport.requests(to: "/confirm").first))
        XCTAssertEqual(body["approved"] as? Bool, false)
        XCTAssertTrue(runner.actions.isEmpty)
    }

    func testHighRiskCannotBeApprovedWithoutAcknowledgment() async throws {
        let transport = FakeTransport.serving(["/route": "route_confirm_high_risk", "/confirm": "confirm_allow"])
        let runner = RecordingRunner()
        let c = try make(transport, runner: runner)
        c.submit("open ssh")
        await settle(c)
        XCTAssertEqual(c.state.pending?.requiresAcknowledgment, true)

        c.confirm(approved: true, acknowledgeRisk: false)
        XCTAssertEqual(c.state.phase, .waiting)   // nothing happened
        XCTAssertTrue(transport.requests(to: "/confirm").isEmpty)

        c.confirm(approved: true, acknowledgeRisk: true)
        await settle(c)
        let body = transport.body(of: try XCTUnwrap(transport.requests(to: "/confirm").first))
        XCTAssertEqual(body["acknowledge_risk"] as? Bool, true)
    }

    func testTypingANewRequestWhileWaitingDeniesTheOldOne() async throws {
        let transport = FakeTransport.serving(["/route": "route_confirm_close_app", "/confirm": "confirm_expired"])
        let c = try make(transport)
        c.submit("quit safari")
        await settle(c)
        XCTAssertEqual(c.state.phase, .waiting)

        XCTAssertTrue(c.submit("something else"))
        await waitUntil { transport.requests(to: "/confirm").count == 1 }
        let body = transport.body(of: try XCTUnwrap(transport.requests(to: "/confirm").first))
        XCTAssertEqual(body["approved"] as? Bool, false)
    }

    func testConfirmingWhenNothingIsPendingDoesNothing() async throws {
        let transport = FakeTransport.serving([:])
        let c = try make(transport)
        c.confirm(approved: true, acknowledgeRisk: true)
        XCTAssertEqual(c.state.phase, .idle)
        XCTAssertTrue(transport.requests.isEmpty)
    }

    // MARK: - input rules

    func testEmptyInputIsIgnored() throws {
        let transport = FakeTransport.serving([:])
        let c = try make(transport)
        XCTAssertFalse(c.submit("   \n "))
        XCTAssertEqual(c.state.phase, .idle)
        XCTAssertTrue(transport.requests.isEmpty)
    }

    func testTooLongInputIsRefusedLocally() throws {
        let transport = FakeTransport.serving([:])
        let c = try make(transport)
        XCTAssertFalse(c.submit(String(repeating: "a", count: CapsuleController.maxInputLength + 1)))
        XCTAssertEqual(c.state.tone, .warning)
        XCTAssertTrue(c.state.message.contains("too long"))
        XCTAssertTrue(transport.requests.isEmpty)
        XCTAssertTrue(c.submit(String(repeating: "a", count: CapsuleController.maxInputLength)))
    }

    func testSecondRequestWhileThinkingIsIgnored() async throws {
        let c = try make(FakeTransport { _ in
            try await Task.sleep(nanoseconds: 300_000_000)
            return (try Fixtures.data("route_reply"), 200)
        })
        XCTAssertTrue(c.submit("first"))
        XCTAssertFalse(c.submit("second"))
        c.cancel()
    }

    func testCancelStopsWaitingAndIgnoresALateAnswer() async throws {
        let c = try make(FakeTransport { _ in
            try await Task.sleep(nanoseconds: 200_000_000)
            return (try Fixtures.data("route_reply"), 200)
        })
        c.submit("slow")
        c.cancel()
        XCTAssertEqual(c.state.phase, .done)
        XCTAssertEqual(c.state.message, "Cancelled.")
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(c.state.message, "Cancelled.")   // the late reply did not overwrite it
    }

    func testCancelOnlyAppliesWhileThinking() throws {
        let c = try make(.serving([:]))
        c.cancel()
        XCTAssertEqual(c.state.phase, .idle)
    }

    // MARK: - failures

    func testBackendDownIsExplained() async throws {
        let c = try make(FakeTransport { _ in throw URLError(.cannotConnectToHost) })
        c.submit("hi")
        await settle(c)
        XCTAssertEqual(c.state.phase, .error)
        XCTAssertTrue(c.state.message.contains("scripts/start.sh"))
    }

    func testSlowModelIsExplained() async throws {
        let c = try make(FakeTransport { _ in throw URLError(.timedOut) })
        c.submit("hi")
        await settle(c)
        XCTAssertTrue(c.state.message.contains("too long"))
    }

    func testModelNotReadyShowsTheBackendsReason() async throws {
        let c = try make(.serving(["/route": "error_503"], status: ["/route": 503]))
        c.submit("hi")
        await settle(c)
        XCTAssertEqual(c.state.phase, .error)
        XCTAssertTrue(c.state.message.contains("isn't ready"))
        XCTAssertTrue(c.state.message.contains("cannot connect to Ollama"))
    }

    func testAfterAnErrorTheNextRequestWorks() async throws {
        var fail = true
        let c = try make(FakeTransport { _ in
            if fail { throw URLError(.cannotConnectToHost) }
            return (try Fixtures.data("route_reply"), 200)
        })
        c.submit("hi")
        await settle(c)
        XCTAssertEqual(c.state.phase, .error)
        fail = false
        XCTAssertTrue(c.submit("hi again"))
        await settle(c)
        XCTAssertEqual(c.state.message, "Hello there.")
    }

    // MARK: - voice

    func testMicWithoutAVoiceEngineFallsBackToTyping() throws {
        let c = try make(.serving([:]))
        var focusRequests = 0
        c.onFocusRequest = { focusRequests += 1 }
        c.toggleMic()
        XCTAssertEqual(c.state.phase, .idle)
        XCTAssertEqual(c.state.tone, .warning)
        XCTAssertTrue(c.state.message.contains("type"))
        XCTAssertEqual(focusRequests, 1)
    }

    func testSpokenRequestIsSentLikeATypedOne() async throws {
        let voice = FakeVoice()
        let transport = FakeTransport.serving(["/route": "route_reply"])
        let c = try make(transport, voice: voice)
        c.toggleMic()
        XCTAssertEqual(c.state.phase, .listening)
        XCTAssertEqual(voice.started, 1)

        voice.emit(.transcript("open chrome"))
        await waitUntil { c.state.phase == .done }
        XCTAssertEqual(voice.stopped, 0)   // the recording had already ended
        let body = transport.body(of: try XCTUnwrap(transport.requests(to: "/route").first))
        XCTAssertEqual(body["text"] as? String, "open chrome")
    }

    func testVoiceFailureFallsBackToTyping() async throws {
        let voice = FakeVoice()
        let c = try make(.serving([:]), voice: voice)
        var focusRequests = 0
        c.onFocusRequest = { focusRequests += 1 }
        c.toggleMic()
        voice.emit(.failed("Microphone access was denied."))
        await waitUntil { c.state.phase == .idle }
        XCTAssertEqual(c.state.phase, .idle)
        XCTAssertTrue(c.state.message.contains("Microphone access was denied."))
        XCTAssertTrue(c.state.message.contains("type"))
        XCTAssertEqual(focusRequests, 1)
    }

    func testEmptyTranscriptDoesNotLeaveTheMicStuck() async throws {
        let voice = FakeVoice()
        let c = try make(.serving([:]), voice: voice)
        c.toggleMic()
        voice.emit(.transcript("   "))
        await waitUntil { c.state.phase == .idle }
        XCTAssertEqual(c.state.phase, .idle)
        XCTAssertTrue(c.state.message.contains("didn't catch"))
    }

    func testTappingTheMicAgainSendsWhatWasSaid() throws {
        let voice = FakeVoice()
        let c = try make(.serving([:]), voice: voice)
        c.toggleMic()
        c.toggleMic()
        XCTAssertEqual(c.state.phase, .transcribing)   // waiting for the transcript
        XCTAssertEqual(voice.stopped, 1)
        XCTAssertEqual(voice.cancelled, 0)
    }

    func testLateVoiceEventsAfterCancellingAreIgnored() async throws {
        let voice = FakeVoice()
        let transport = FakeTransport.serving(["/route": "route_reply"])
        let c = try make(transport, voice: voice)
        c.toggleMic()
        c.cancelVoice()
        XCTAssertEqual(voice.cancelled, 1)
        XCTAssertEqual(c.state.phase, .idle)
        voice.emit(.transcript("too late"))
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertTrue(transport.requests.isEmpty)
    }

    func testMicIsIgnoredWhileBusy() async throws {
        let voice = FakeVoice()
        let c = try make(FakeTransport { _ in
            try await Task.sleep(nanoseconds: 200_000_000)
            return (try Fixtures.data("route_reply"), 200)
        }, voice: voice)
        c.submit("hi")
        c.toggleMic()
        XCTAssertEqual(voice.started, 0)
        c.cancel()
    }

    // MARK: - window lifecycle

    func testHidingClearsAFinishedResultButNotWorkInProgress() async throws {
        let c = try make(.serving(["/route": "route_reply"]))
        c.submit("hi")
        await settle(c)
        c.dismissed()
        XCTAssertEqual(c.state.phase, .idle)
        XCTAssertEqual(c.state.message, "")
        XCTAssertEqual(c.state.lastAction, "reply")
    }

    func testBackendCheckExplainsAStoppedBackend() async throws {
        let c = try make(FakeTransport { _ in throw URLError(.cannotConnectToHost) })
        c.checkBackend()
        await waitUntil { !c.state.message.isEmpty }
        XCTAssertTrue(c.state.message.contains("scripts/start.sh"))
        XCTAssertEqual(c.state.phase, .idle)
    }

    func testBackendCheckIsSilentWhenHealthy() async throws {
        let c = try make(FakeTransport { _ in (Data(), 200) })
        c.checkBackend()
        try await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(c.state, CapsuleState())
    }

    func testNoticesDoNotInterruptWork() async throws {
        let c = try make(FakeTransport { _ in
            try await Task.sleep(nanoseconds: 200_000_000)
            return (try Fixtures.data("route_reply"), 200)
        })
        c.submit("hi")
        c.notice("hello", tone: .warning)
        XCTAssertEqual(c.state.phase, .thinking)
        c.cancel()
    }

    // MARK: - the voice pipeline

    func testRecordingEndingByItselfShowsTranscribing() async throws {
        let voice = FakeVoice()
        let c = try make(.serving([:]), voice: voice)
        c.toggleMic()
        voice.emit(.processing)
        await waitUntil { c.state.phase == .transcribing }
        XCTAssertEqual(c.state.phase, .transcribing)
    }

    func testFullVoicePipelineStatesAreInOrder() async throws {
        let voice = FakeVoice()
        let speech = FakeSpeech()
        let c = try make(.serving(["/route": "route_allow_open_app"]),
                         runner: RecordingRunner(result: .completed("Opened Google Chrome.")),
                         voice: voice, speech: speech)
        var phases: [Phase] = []
        c.onChange = { phases.append($0.phase) }

        c.toggleMic()
        voice.emit(.processing)
        voice.emit(.transcript("open chrome"))
        await waitUntil { c.state.phase == .speaking }
        XCTAssertEqual(phases, [.listening, .transcribing, .thinking, .executing, .done, .speaking])
        XCTAssertEqual(speech.spoken, ["Opened Google Chrome."])

        speech.emit(.finished)
        await waitUntil { c.state.phase == .done }
        XCTAssertEqual(c.state.message, "Opened Google Chrome.")
    }

    func testSpokenRequestsAreReadAloudButTypedOnesAreNot() async throws {
        let voice = FakeVoice()
        let speech = FakeSpeech()
        let c = try make(.serving(["/route": "route_reply"]), voice: voice, speech: speech)

        c.submit("hi")
        await waitUntil { c.state.phase == .done }
        XCTAssertTrue(speech.spoken.isEmpty)           // typed: silent by default

        c.toggleMic()
        voice.emit(.transcript("hi"))
        await waitUntil { c.state.phase == .speaking }
        XCTAssertEqual(speech.spoken, ["Hello there."])
    }

    func testSpeakModes() async throws {
        for (mode, spokenTyped, spokenVoice) in [(SpeakMode.always, 1, 2), (.never, 0, 0), (.voiceOnly, 0, 1)] {
            let voice = FakeVoice()
            let speech = FakeSpeech()
            let c = try make(.serving(["/route": "route_reply"]), voice: voice, speech: speech, mode: mode)
            c.submit("hi")
            await waitUntil { c.state.phase == .done || c.state.phase == .speaking }
            XCTAssertEqual(speech.spoken.count, spokenTyped, "typed, \(mode)")
            speech.emit(.finished)
            await waitUntil { c.state.phase == .done }

            c.toggleMic()
            voice.emit(.transcript("hi"))
            await waitUntil { c.state.phase == .done || c.state.phase == .speaking }
            try await Task.sleep(nanoseconds: 20_000_000)
            XCTAssertEqual(speech.spoken.count, spokenVoice, "voice, \(mode)")
        }
    }

    func testConfirmationQuestionsAreAnsweredWithButtonsNotRepeatedAloud() async throws {
        let voice = FakeVoice()
        let speech = FakeSpeech()
        let c = try make(.serving(["/route": "route_confirm_close_app"]), voice: voice, speech: speech)
        c.toggleMic()
        voice.emit(.transcript("quit safari"))
        await waitUntil { c.state.phase == .waiting }
        XCTAssertEqual(c.state.phase, .waiting)
        XCTAssertTrue(speech.spoken.isEmpty)
    }

    func testRefusalsAreReadAloudToo() async throws {
        let voice = FakeVoice()
        let speech = FakeSpeech()
        let c = try make(.serving(["/route": "route_block_url"]), voice: voice, speech: speech)
        c.toggleMic()
        voice.emit(.transcript("go there"))
        await waitUntil { c.state.phase == .speaking }
        XCTAssertTrue(speech.spoken.first?.hasPrefix("I can't do that") ?? false)
    }

    func testLongRepliesAreShortenedBeforeSpeaking() async throws {
        let long = String(repeating: "word ", count: 200)
        let body = Data(("{\"tool\":\"reply\",\"args\":{\"text\":\"\(long)\"},\"requires_confirmation\":false,\"fallback\":false,\"note\":\"\",\"attempts\":1,"
            + "\"guard\":{\"outcome\":\"allow\",\"authorized\":true,\"risk\":\"low\",\"reason\":\"\",\"message\":\"\",\"tool\":\"reply\","
            + "\"args\":{\"text\":\"\(long)\"},\"confirmation_id\":null,\"requires_acknowledgment\":false}}").utf8)
        let speech = FakeSpeech()
        let c = try make(FakeTransport { _ in (body, 200) }, speech: speech, mode: .always)
        c.submit("hi")
        await waitUntil { c.state.phase == .speaking }
        XCTAssertLessThanOrEqual(speech.spoken.first?.count ?? 0, SpokenText.maxLength)
        XCTAssertFalse((speech.spoken.first ?? "").isEmpty)
    }

    func testSpeechFailureKeepsTheResultOnScreen() async throws {
        let voice = FakeVoice()
        let speech = FakeSpeech()
        let c = try make(.serving(["/route": "route_reply"]), voice: voice, speech: speech)
        c.toggleMic()
        voice.emit(.transcript("hi"))
        await waitUntil { c.state.phase == .speaking }

        speech.emit(.failed("couldn't speak: voice output isn't set up"))
        await waitUntil { c.state.phase == .done }
        XCTAssertEqual(c.state.message, "Hello there.")
        XCTAssertTrue(c.state.lastAction.contains("couldn't speak"))
    }

    func testANewRequestInterruptsSpeech() async throws {
        let voice = FakeVoice()
        let speech = FakeSpeech()
        let c = try make(.serving(["/route": "route_reply"]), voice: voice, speech: speech)
        c.toggleMic()
        voice.emit(.transcript("hi"))
        await waitUntil { c.state.phase == .speaking }

        XCTAssertTrue(c.submit("something else"))
        XCTAssertEqual(speech.stopped, 1)
        XCTAssertEqual(c.state.phase, .thinking)
    }

    func testStoppingSpeechIgnoresItsLateEvents() async throws {
        let voice = FakeVoice()
        let speech = FakeSpeech()
        let c = try make(.serving(["/route": "route_reply"]), voice: voice, speech: speech)
        c.toggleMic()
        voice.emit(.transcript("hi"))
        await waitUntil { c.state.phase == .speaking }

        c.stopSpeaking()
        XCTAssertEqual(c.state.phase, .done)
        XCTAssertEqual(speech.stopped, 1)
        speech.emit(.failed("late"))
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertEqual(c.state.phase, .done)
        XCTAssertFalse(c.state.lastAction.contains("late"))
    }

    func testTheMicInterruptsSpeechAndStartsListening() async throws {
        let voice = FakeVoice()
        let speech = FakeSpeech()
        let c = try make(.serving(["/route": "route_reply"]), voice: voice, speech: speech)
        c.toggleMic()
        voice.emit(.transcript("hi"))
        await waitUntil { c.state.phase == .speaking }

        c.toggleMic()
        XCTAssertEqual(speech.stopped, 1)
        XCTAssertEqual(c.state.phase, .listening)
        XCTAssertEqual(voice.started, 2)
    }

    func testHidingTheWindowStopsSpeechAndRecording() async throws {
        let voice = FakeVoice()
        let speech = FakeSpeech()
        let c = try make(.serving(["/route": "route_reply"]), voice: voice, speech: speech)

        c.toggleMic()
        c.dismissed()                                  // hidden while recording
        XCTAssertEqual(voice.cancelled, 1)
        XCTAssertEqual(c.state.phase, .idle)

        c.toggleMic()
        voice.emit(.transcript("hi"))
        await waitUntil { c.state.phase == .speaking }
        c.dismissed()                                  // hidden while speaking
        XCTAssertEqual(speech.stopped, 1)
        XCTAssertEqual(c.state.phase, .idle)
    }

    func testCancellingWhileTranscribingDiscardsTheTranscript() async throws {
        let voice = FakeVoice()
        let transport = FakeTransport.serving(["/route": "route_reply"])
        let c = try make(transport, voice: voice)
        c.toggleMic()
        c.toggleMic()                                  // sent for transcription
        XCTAssertEqual(c.state.phase, .transcribing)
        c.cancelVoice()
        XCTAssertEqual(c.state.phase, .idle)
        voice.emit(.transcript("hello"))
        try await Task.sleep(nanoseconds: 30_000_000)
        XCTAssertTrue(transport.requests.isEmpty)
    }

    func testTypingWhileTranscribingIsIgnored() throws {
        let voice = FakeVoice()
        let c = try make(.serving([:]), voice: voice)
        c.toggleMic()
        c.toggleMic()
        XCTAssertFalse(c.submit("typed"))
        XCTAssertEqual(c.state.phase, .transcribing)
    }

    func testTypingWhileListeningDiscardsTheRecording() throws {
        let voice = FakeVoice()
        let c = try make(.serving(["/route": "route_reply"]), voice: voice)
        c.toggleMic()
        XCTAssertTrue(c.submit("typed instead"))
        XCTAssertEqual(voice.cancelled, 1)
        XCTAssertEqual(c.state.phase, .thinking)
    }

    func testSpokenRequestsGoThroughTheSameSafetyDecisions() async throws {
        let voice = FakeVoice()
        let runner = RecordingRunner()
        let c = try make(.serving(["/route": "route_block_url"]), runner: runner, voice: voice)
        c.toggleMic()
        voice.emit(.transcript("go there"))
        await waitUntil { c.state.phase == .done }
        XCTAssertEqual(c.state.tone, .warning)
        XCTAssertTrue(runner.actions.isEmpty)           // a spoken request is no more privileged than a typed one
    }
}
