import XCTest
@testable import CapsuleCore
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

final class BackendClientTests: XCTestCase {
    func testOnlyLoopbackAddressesAreAccepted() {
        let transport = FakeTransport.serving([:])
        for text in ["https://example.com", "http://192.168.1.5:8765", "http://0.0.0.0:8765", "http://evil.localhost.example.com"] {
            XCTAssertThrowsError(try BackendClient(baseURL: URL(string: text)!, transport: transport), text) { error in
                XCTAssertEqual(error as? BackendError, .notLocal(text))
            }
        }
        for text in ["http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765"] {
            XCTAssertNoThrow(try BackendClient(baseURL: URL(string: text)!, transport: transport), text)
        }
    }

    func testRouteRequestShape() async throws {
        let transport = FakeTransport.serving(["/route": "route_allow_open_app"])
        let client = try makeClient(transport)
        let response = try await client.route(text: "open chrome")
        XCTAssertEqual(response.tool, "open_app")

        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:8765/route")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        let body = transport.body(of: request)
        XCTAssertEqual(body["text"] as? String, "open chrome")
        XCTAssertNil(body["dry_run"])   // omitted unless asked for
    }

    func testDryRunIsSentWhenRequested() async throws {
        let transport = FakeTransport.serving(["/route": "route_dry_run"])
        _ = try await makeClient(transport).route(text: "quit safari", dryRun: true)
        let body = transport.body(of: try XCTUnwrap(transport.requests.first))
        XCTAssertEqual(body["dry_run"] as? Bool, true)
    }

    func testConfirmRequestShape() async throws {
        let transport = FakeTransport.serving(["/confirm": "confirm_done"])
        let response = try await makeClient(transport).confirm(id: "abc123def456", approved: true, acknowledgeRisk: true)
        XCTAssertTrue(response.decision.authorized)
        XCTAssertEqual(response.result?.message, "Asked Safari to quit.")
        let body = transport.body(of: try XCTUnwrap(transport.requests.first))
        XCTAssertEqual(body["confirmation_id"] as? String, "abc123def456")
        XCTAssertEqual(body["approved"] as? Bool, true)
        XCTAssertEqual(body["acknowledge_risk"] as? Bool, true)
    }

    func testConnectionProblemsAreMapped() async throws {
        let cases: [(URLError.Code, BackendError)] = [
            (.cannotConnectToHost, .unreachable),
            (.networkConnectionLost, .unreachable),
            (.timedOut, .timedOut),
        ]
        for (code, expected) in cases {
            let client = try makeClient(FakeTransport { _ in throw URLError(code) })
            let error = await thrownError(try await client.route(text: "hi"))
            XCTAssertEqual(error as? BackendError, expected)
        }
    }

    func testCancellationIsNotReportedAsAFailure() async throws {
        let client = try makeClient(FakeTransport { _ in throw URLError(.cancelled) })
        let error = await thrownError(try await client.route(text: "hi"))
        XCTAssertTrue(error is CancellationError)
    }

    func testServerErrorsCarryTheDetail() async throws {
        let client = try makeClient(FakeTransport.serving(["/route": "error_503"], status: ["/route": 503]))
        let error = await thrownError(try await client.route(text: "hi"))
        XCTAssertEqual(error as? BackendError, .server(status: 503, detail: "cannot connect to Ollama at http://127.0.0.1:11434"))
    }

    func testValidationErrorsHaveNoDetailText() async throws {
        let body = Data(#"{"detail":[{"loc":["body","text"],"msg":"too short"}]}"#.utf8)
        let client = try makeClient(FakeTransport { _ in (body, 422) })
        let error = await thrownError(try await client.route(text: "hi"))
        XCTAssertEqual(error as? BackendError, .server(status: 422, detail: ""))
    }

    func testGarbageIsABadResponse() async throws {
        let client = try makeClient(FakeTransport { _ in (Data("<html>".utf8), 200) })
        let error = await thrownError(try await client.route(text: "hi"))
        XCTAssertEqual(error as? BackendError, .badResponse)
    }

    func testHealth() async throws {
        let up = try makeClient(FakeTransport { _ in (Data(), 200) })
        let wrongStatus = try makeClient(FakeTransport { _ in (Data(), 500) })
        let down = try makeClient(FakeTransport { _ in throw URLError(.cannotConnectToHost) })
        let upResult = await up.health()
        let wrongStatusResult = await wrongStatus.health()
        let downResult = await down.health()
        XCTAssertTrue(upResult)
        XCTAssertFalse(wrongStatusResult)
        XCTAssertFalse(downResult)
    }

    // MARK: - voice

    func testTranscribeSendsTheAudioAndReturnsTheWords() async throws {
        let transport = FakeTransport.serving(["/voice/transcribe": "voice_transcribe"])
        let audio = Data([0x52, 0x49, 0x46, 0x46, 1, 2, 3])
        let text = try await makeClient(transport).transcribe(wav: audio)
        XCTAssertEqual(text, "open chrome")
        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:8765/voice/transcribe")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "audio/wav")
        XCTAssertEqual(request.httpBody, audio)
    }

    func testSilenceTranscribesToAnEmptyString() async throws {
        let transport = FakeTransport.serving(["/voice/transcribe": "voice_transcribe_empty"])
        let text = try await makeClient(transport).transcribe(wav: Data([1]))
        XCTAssertEqual(text, "")
    }

    func testTranscribeErrorsCarryTheBackendsExplanation() async throws {
        let transport = FakeTransport.serving(["/voice/transcribe": "voice_error_503"], status: ["/voice/transcribe": 503])
        let error = await thrownError(try await makeClient(transport).transcribe(wav: Data([1])))
        XCTAssertEqual(error as? BackendError, .server(status: 503, detail: "The speech model is missing. Download one."))
    }

    func testSpeakSendsTheTextAndReturnsTheAudio() async throws {
        let reply = Data([9, 8, 7, 6])
        let transport = FakeTransport { _ in (reply, 200) }
        let audio = try await makeClient(transport).speak(text: "Opened Google Chrome.")
        XCTAssertEqual(audio, reply)
        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:8765/voice/speak")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertEqual(transport.body(of: request)["text"] as? String, "Opened Google Chrome.")
    }

    func testSpeakWithNoAudioIsABadResponse() async throws {
        let client = try makeClient(FakeTransport { _ in (Data(), 200) })
        let error = await thrownError(try await client.speak(text: "hi"))
        XCTAssertEqual(error as? BackendError, .badResponse)
    }

    func testSpeakErrorsAreMapped() async throws {
        let unavailable = try makeClient(FakeTransport { _ in (Data(#"{"detail":"Piper is not installed."}"#.utf8), 503) })
        let error = await thrownError(try await unavailable.speak(text: "hi"))
        XCTAssertEqual(error as? BackendError, .server(status: 503, detail: "Piper is not installed."))
        let down = try makeClient(FakeTransport { _ in throw URLError(.cannotConnectToHost) })
        let downError = await thrownError(try await down.speak(text: "hi"))
        XCTAssertEqual(downError as? BackendError, .unreachable)
    }

    // MARK: - choosing between matches

    func testChooseSendsThePickAndReturnsTheOutcome() async throws {
        let transport = FakeTransport.serving(["/choose": "choose_done"])
        let response = try await makeClient(transport).choose(choiceId: "c0ffee123456", optionId: "2")
        XCTAssertEqual(response.result?.status, .done)
        XCTAssertEqual(response.decision?.outcome, .allow)
        let request = try XCTUnwrap(transport.requests.first)
        XCTAssertEqual(request.url?.absoluteString, "http://127.0.0.1:8765/choose")
        let body = transport.body(of: request)
        XCTAssertEqual(body["choice_id"] as? String, "c0ffee123456")
        XCTAssertEqual(body["option_id"] as? String, "2")
    }

    func testCancellingSendsNoOption() async throws {
        let transport = FakeTransport.serving(["/choose": "choose_expired"])
        _ = try await makeClient(transport).choose(choiceId: "c0ffee123456", optionId: nil)
        XCTAssertNil(transport.body(of: try XCTUnwrap(transport.requests.first))["option_id"])
    }
}
