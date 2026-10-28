import XCTest
@testable import CapsuleCore
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

final class SilenceDetectorTests: XCTestCase {
    private let t0 = Date(timeIntervalSince1970: 1_000)

    func testKeepsListeningWhileTheUserTalks() {
        var d = SilenceDetector()
        for i in 0..<30 {
            XCTAssertEqual(d.feed(level: -20, at: t0.addingTimeInterval(Double(i) * 0.1)), .keepListening)
        }
    }

    func testStopsAfterSilenceFollowingSpeech() {
        var d = SilenceDetector(silenceToStop: 1.2)
        XCTAssertEqual(d.feed(level: -20, at: t0), .keepListening)                              // speech at 0 s
        XCTAssertEqual(d.feed(level: -60, at: t0.addingTimeInterval(1.0)), .keepListening)
        XCTAssertEqual(d.feed(level: -60, at: t0.addingTimeInterval(1.19)), .keepListening)
        XCTAssertEqual(d.feed(level: -60, at: t0.addingTimeInterval(1.2)), .stop(.silenceAfterSpeech))
    }

    func testSpeechResetsTheSilenceTimer() {
        var d = SilenceDetector(silenceToStop: 1.2)
        _ = d.feed(level: -20, at: t0)
        _ = d.feed(level: -60, at: t0.addingTimeInterval(1.0))
        XCTAssertEqual(d.feed(level: -20, at: t0.addingTimeInterval(1.1)), .keepListening)     // talking again
        XCTAssertEqual(d.feed(level: -60, at: t0.addingTimeInterval(2.0)), .keepListening)
        XCTAssertEqual(d.feed(level: -60, at: t0.addingTimeInterval(2.4)), .stop(.silenceAfterSpeech))
    }

    func testGivesUpIfNobodyEverSpeaks() {
        var d = SilenceDetector(giveUpIfNoSpeech: 6)
        XCTAssertEqual(d.feed(level: -70, at: t0), .keepListening)
        XCTAssertEqual(d.feed(level: -70, at: t0.addingTimeInterval(5.9)), .keepListening)
        XCTAssertEqual(d.feed(level: -70, at: t0.addingTimeInterval(6.0)), .stop(.noSpeechHeard))
    }

    func testNeverRecordsPastTheTimeLimit() {
        var d = SilenceDetector(maxDuration: 15)
        _ = d.feed(level: -20, at: t0)
        XCTAssertEqual(d.feed(level: -20, at: t0.addingTimeInterval(14.9)), .keepListening)
        XCTAssertEqual(d.feed(level: -20, at: t0.addingTimeInterval(15)), .stop(.tooLong))
    }

    func testQuietRoomNoiseIsNotSpeech() {
        var d = SilenceDetector(speechThreshold: -35, giveUpIfNoSpeech: 3)
        XCTAssertEqual(d.feed(level: -45, at: t0), .keepListening)
        XCTAssertEqual(d.feed(level: -45, at: t0.addingTimeInterval(3)), .stop(.noSpeechHeard))
    }
}

final class SpokenTextTests: XCTestCase {
    func testShortTextIsKeptAsItIs() {
        XCTAssertEqual(SpokenText.make(from: "Opened Google Chrome."), "Opened Google Chrome.")
    }

    func testWhitespaceAndNewlinesBecomeOneLine() {
        XCTAssertEqual(SpokenText.make(from: "  Line one\n\n  line   two\t"), "Line one line two")
        XCTAssertEqual(SpokenText.make(from: "a\nb"), "a b")
    }

    func testLongTextStopsAtAWholeSentence() {
        let text = String(repeating: "word ", count: 15) + "end. " + String(repeating: "word ", count: 100)
        XCTAssertEqual(SpokenText.make(from: text, maxLength: 100), String(repeating: "word ", count: 15) + "end.")
    }

    func testOtherwiseItStopsAtAWordBoundary() {
        let text = "First. " + String(repeating: "word ", count: 100)
        let spoken = SpokenText.make(from: text, maxLength: 100)
        XCTAssertLessThanOrEqual(spoken.count, 100)
        XCTAssertTrue(spoken.hasSuffix("word"))
    }

    func testEmptyStaysEmpty() {
        XCTAssertEqual(SpokenText.make(from: " \n "), "")
    }

    func testTheDefaultLimitIsRespected() {
        XCTAssertLessThanOrEqual(SpokenText.make(from: String(repeating: "x ", count: 1000)).count, SpokenText.maxLength)
    }
}

final class LocalVoiceInputTests: XCTestCase {
    private var events: [VoiceEvent] = []
    private let clock = ManualClock()

    private func make(
        _ recorder: FakeRecorder, transport: FakeTransport = .serving(["/voice/transcribe": "voice_transcribe"])
    ) throws -> LocalVoiceInput {
        events = []
        return LocalVoiceInput(client: try makeClient(transport), recorder: recorder, clock: clock.read)
    }

    private func start(_ input: LocalVoiceInput) {
        input.start { [weak self] in self?.events.append($0) }
    }

    private func waitForEvents(_ count: Int) async {
        let deadline = Date().addingTimeInterval(2)
        while events.count < count && Date() < deadline {
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
    }

    // MARK: transcription path

    func testRecordingIsSentForTranscription() async throws {
        let recorder = FakeRecorder()
        let transport = FakeTransport.serving(["/voice/transcribe": "voice_transcribe"])
        let input = try make(recorder, transport: transport)
        start(input)
        XCTAssertEqual(recorder.started, 1)

        input.stop()
        await waitForEvents(2)
        XCTAssertEqual(events, [.processing, .transcript("open chrome")])
        XCTAssertEqual(recorder.finished, 1)
        XCTAssertEqual(transport.requests(to: "/voice/transcribe").first?.httpBody, Data([1, 2, 3]))
    }

    func testSilenceEndsTheRecordingByItself() async throws {
        let recorder = FakeRecorder()
        let input = try make(recorder)
        start(input)
        recorder.emit(-20)                      // speech
        clock.advance(0.5)
        recorder.emit(-20)
        clock.advance(1.5)
        recorder.emit(-60)                      // quiet for longer than 1.2 s
        await waitForEvents(2)
        XCTAssertEqual(events, [.processing, .transcript("open chrome")])
        XCTAssertEqual(recorder.finished, 1)
    }

    func testLevelsAfterTheRecordingEndedAreIgnored() async throws {
        let recorder = FakeRecorder()
        let input = try make(recorder)
        start(input)
        input.stop()
        recorder.emit(-60)
        clock.advance(30)
        recorder.emit(-60)
        await waitForEvents(2)
        XCTAssertEqual(recorder.finished, 1)    // finished once, not again
    }

    func testNothingRecordedGivesAnEmptyTranscriptWithoutCallingTheBackend() async throws {
        let recorder = FakeRecorder()
        recorder.audio = nil
        let transport = FakeTransport.serving([:])
        let input = try make(recorder, transport: transport)
        start(input)
        input.stop()
        await waitForEvents(2)
        XCTAssertEqual(events, [.processing, .transcript("")])
        XCTAssertTrue(transport.requests.isEmpty)
    }

    func testSilenceTranscribesToAnEmptyString() async throws {
        let input = try make(FakeRecorder(), transport: .serving(["/voice/transcribe": "voice_transcribe_empty"]))
        start(input)
        input.stop()
        await waitForEvents(2)
        XCTAssertEqual(events, [.processing, .transcript("")])
    }

    func testCancellingDiscardsTheRecordingAndAnyLateTranscript() async throws {
        let recorder = FakeRecorder()
        let input = try make(recorder, transport: FakeTransport { _ in
            try await Task.sleep(nanoseconds: 100_000_000)
            return (try Fixtures.data("voice_transcribe"), 200)
        })
        start(input)
        input.stop()                            // transcription is now in flight
        input.cancel()
        try await Task.sleep(nanoseconds: 250_000_000)
        XCTAssertEqual(events, [.processing])   // the transcript never arrived
        XCTAssertGreaterThanOrEqual(recorder.cancelled, 1)

        start(input)
        input.cancel()                          // cancelling while still recording
        input.stop()
        XCTAssertEqual(recorder.finished, 1)    // only the first recording was ever finished
    }

    func testStartingAgainReplacesTheOldRecording() async throws {
        let recorder = FakeRecorder()
        let input = try make(recorder)
        start(input)
        start(input)
        XCTAssertEqual(recorder.started, 2)
        input.stop()
        await waitForEvents(2)
        XCTAssertEqual(recorder.finished, 1)
    }

    // MARK: fallback to typing (the controller turns these into messages)

    func testBackendProblemsBecomeFailures() async throws {
        let cases: [(FakeTransport, String)] = [
            (FakeTransport { _ in throw URLError(.cannotConnectToHost) }, "I can't reach the assistant backend."),
            (FakeTransport { _ in throw URLError(.timedOut) }, "Transcribing took too long."),
            (.serving(["/voice/transcribe": "voice_error_503"], status: ["/voice/transcribe": 503]),
             "Voice recognition isn't set up. The speech model is missing. Download one."),
            (FakeTransport { _ in (Data("{}".utf8), 400) }, "That recording couldn't be used."),
            (FakeTransport { _ in (Data("{}".utf8), 500) }, "Voice recognition failed."),
        ]
        for (transport, message) in cases {
            let input = try make(FakeRecorder(), transport: transport)
            start(input)
            input.stop()
            await waitForEvents(2)
            XCTAssertEqual(events, [.processing, .failed(message)], message)
        }
    }

    func testMicrophoneThatCannotStartIsAFailure() throws {
        let recorder = FakeRecorder()
        recorder.startError = URLError(.unknown)
        let input = try make(recorder)
        start(input)
        XCTAssertEqual(events, [.failed(VoiceMessages.cannotStartMic)])
    }

    // MARK: permission

    func testDeniedPermissionNeverRecordsAndExplainsWhereToFixIt() throws {
        let recorder = FakeRecorder(permission: .denied)
        let input = try make(recorder)
        start(input)
        XCTAssertEqual(recorder.started, 0)
        XCTAssertEqual(recorder.permissionRequests, 0)     // no point asking again: macOS would not show the prompt
        XCTAssertEqual(events, [.failed(VoiceMessages.micDenied)])
        guard case .failed(let message)? = events.first else { return XCTFail("expected a failure") }
        XCTAssertTrue(message.contains("System Preferences"))
        XCTAssertTrue(message.contains("Microphone"))
    }

    func testRestrictedPermissionIsExplainedDifferently() throws {
        let recorder = FakeRecorder(permission: .restricted)
        let input = try make(recorder)
        start(input)
        XCTAssertEqual(recorder.started, 0)
        XCTAssertEqual(events, [.failed(VoiceMessages.micRestricted)])
        XCTAssertNotEqual(VoiceMessages.micRestricted, VoiceMessages.micDenied)
    }

    func testFirstUseAsksForPermissionAndThenRecords() throws {
        let recorder = FakeRecorder(permission: .notDetermined)
        let input = try make(recorder)
        start(input)
        XCTAssertEqual(recorder.permissionRequests, 1)
        XCTAssertEqual(recorder.started, 1)
        XCTAssertTrue(events.isEmpty)
    }

    func testRefusingThePromptIsAFailureAndNothingIsRecorded() throws {
        let recorder = FakeRecorder(permission: .notDetermined)
        recorder.grantOnRequest = false
        let input = try make(recorder)
        start(input)
        XCTAssertEqual(recorder.permissionRequests, 1)
        XCTAssertEqual(recorder.started, 0)
        XCTAssertEqual(events, [.failed(VoiceMessages.micDenied)])
    }

    func testAnAnswerToThePromptAfterCancellingDoesNothing() throws {
        var pending: ((Bool) -> Void)?
        let delayed = DelayedPermissionRecorder(permission: .notDetermined) { pending = $0 }
        let input = LocalVoiceInput(client: try makeClient(.serving([:])), recorder: delayed, clock: clock.read)
        events = []
        start(input)
        input.cancel()
        pending?(true)                          // the user answered after the request was abandoned
        XCTAssertEqual(delayed.started, 0)
        XCTAssertTrue(events.isEmpty)
    }
}

/// A recorder whose permission prompt stays open until the test answers it.
final class DelayedPermissionRecorder: AudioRecording {
    var permission: MicPermission
    private(set) var started = 0
    private let hold: (@escaping (Bool) -> Void) -> Void

    init(permission: MicPermission, hold: @escaping (@escaping (Bool) -> Void) -> Void) {
        self.permission = permission
        self.hold = hold
    }

    func requestPermission(_ completion: @escaping (Bool) -> Void) { hold(completion) }
    func startRecording(onLevel: @escaping (Float) -> Void) throws { started += 1 }
    func finishRecording() -> Data? { nil }
    func cancelRecording() {}
}

final class LocalSpeechTests: XCTestCase {
    private var events: [SpeechEvent] = []

    private func make(_ transport: FakeTransport, player: FakePlayer) throws -> LocalSpeech {
        events = []
        return LocalSpeech(client: try makeClient(transport), player: player)
    }

    private func speak(_ speech: LocalSpeech, _ text: String = "Hello there.") {
        speech.speak(text) { [weak self] in self?.events.append($0) }
    }

    private func waitForPlayback(_ player: FakePlayer) async {
        let deadline = Date().addingTimeInterval(2)
        while player.played.isEmpty && Date() < deadline {
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
    }

    func testSpeechIsGeneratedAndPlayed() async throws {
        let audio = Data([5, 6, 7])
        let transport = FakeTransport { _ in (audio, 200) }
        let player = FakePlayer()
        let speech = try make(transport, player: player)
        speak(speech, "Opened Google Chrome.")
        await waitForPlayback(player)

        XCTAssertEqual(player.played, [audio])
        XCTAssertEqual(transport.body(of: try XCTUnwrap(transport.requests.first))["text"] as? String, "Opened Google Chrome.")
        player.finishPlaying()
        XCTAssertEqual(events, [.finished])
    }

    func testPlaybackFailureIsReported() async throws {
        let player = FakePlayer()
        let speech = try make(FakeTransport { _ in (Data([1]), 200) }, player: player)
        speak(speech)
        await waitForPlayback(player)
        player.finishPlaying(URLError(.unknown))
        XCTAssertEqual(events, [.failed("couldn't play the audio")])
    }

    func testAPlayerThatCannotStartIsReported() async throws {
        let player = FakePlayer()
        player.playError = URLError(.unknown)
        let speech = try make(FakeTransport { _ in (Data([1]), 200) }, player: player)
        speak(speech)
        let deadline = Date().addingTimeInterval(2)
        while events.isEmpty && Date() < deadline { try? await Task.sleep(nanoseconds: 5_000_000) }
        XCTAssertEqual(events, [.failed("couldn't speak")])
    }

    func testVoiceEngineProblemsBecomeShortReasons() async throws {
        let cases: [(FakeTransport, String)] = [
            (FakeTransport { _ in (Data(#"{"detail":"Piper is not installed."}"#.utf8), 503) },
             "couldn't speak: voice output isn't set up"),
            (FakeTransport { _ in throw URLError(.cannotConnectToHost) }, "couldn't speak: backend unreachable"),
            (FakeTransport { _ in throw URLError(.timedOut) }, "couldn't speak: took too long"),
            (FakeTransport { _ in (Data("{}".utf8), 502) }, "couldn't speak"),
        ]
        for (transport, reason) in cases {
            let speech = try make(transport, player: FakePlayer())
            speak(speech)
            let deadline = Date().addingTimeInterval(2)
            while events.isEmpty && Date() < deadline { try? await Task.sleep(nanoseconds: 5_000_000) }
            XCTAssertEqual(events, [.failed(reason)], reason)
        }
    }

    func testStoppingSilencesLateAudioAndEvents() async throws {
        let player = FakePlayer()
        let speech = try make(FakeTransport { _ in
            try await Task.sleep(nanoseconds: 100_000_000)
            return (Data([1]), 200)
        }, player: player)
        speak(speech)
        speech.stop()                           // before the audio is ready
        try await Task.sleep(nanoseconds: 250_000_000)
        XCTAssertTrue(player.played.isEmpty)
        XCTAssertTrue(events.isEmpty)
        XCTAssertGreaterThanOrEqual(player.stopped, 1)
    }

    func testSpeakingAgainReplacesTheEarlierReply() async throws {
        let player = FakePlayer()
        var replies = [Data([1]), Data([2])]
        let speech = try make(FakeTransport { _ in (replies.removeFirst(), 200) }, player: player)
        speak(speech, "first")
        await waitForPlayback(player)
        speak(speech, "second")
        let deadline = Date().addingTimeInterval(2)
        while player.played.count < 2 && Date() < deadline { try? await Task.sleep(nanoseconds: 5_000_000) }
        XCTAssertEqual(player.played, [Data([1]), Data([2])])
        player.finishPlaying()
        XCTAssertEqual(events, [.finished])     // only the current reply reports back
    }

    func testTheStandInSaysItCannotSpeak() {
        let speech = UnavailableSpeech()
        XCTAssertFalse(speech.isAvailable)
        var got: [SpeechEvent] = []
        speech.speak("hi") { got.append($0) }
        XCTAssertEqual(got, [.failed("voice output isn't set up")])
    }
}
