import Foundation
import XCTest
@testable import CapsuleCore
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// JSON captured from the real backend (see tests/test_app_contract.py in the Python project).
enum Fixtures {
    static func data(_ name: String) throws -> Data {
        let tests = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // CapsuleCoreTests
            .deletingLastPathComponent()   // Tests
        return try Data(contentsOf: tests.appendingPathComponent("Fixtures").appendingPathComponent("\(name).json"))
    }

    static func route(_ name: String) throws -> RouteResponse {
        try JSONDecoder().decode(RouteResponse.self, from: data(name))
    }

    static func decode<T: Decodable>(_ name: String) throws -> T {
        try JSONDecoder().decode(T.self, from: data(name))
    }

    static func confirm(_ name: String) throws -> ConfirmResponse {
        try JSONDecoder().decode(ConfirmResponse.self, from: data(name))
    }
}

/// A fake network. The handler decides every answer, and every request is recorded.
final class FakeTransport: HTTPTransport {
    typealias Handler = (URLRequest) async throws -> (Data, Int)
    private(set) var requests: [URLRequest] = []
    private let handler: Handler

    init(_ handler: @escaping Handler) {
        self.handler = handler
    }

    func send(_ request: URLRequest) async throws -> (Data, Int) {
        requests.append(request)
        return try await handler(request)
    }

    /// Answers by URL path with the named fixture; any other path gets 404.
    static func serving(_ fixtures: [String: String], status: [String: Int] = [:]) -> FakeTransport {
        FakeTransport { request in
            let path = request.url?.path ?? ""
            guard let name = fixtures[path] else { return (Data(), 404) }
            return (try Fixtures.data(name), status[path] ?? 200)
        }
    }

    func requests(to path: String) -> [URLRequest] {
        requests.filter { $0.url?.path == path }
    }

    func body(of request: URLRequest) -> [String: Any] {
        guard let data = request.httpBody,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return object
    }
}

func makeClient(_ transport: HTTPTransport) throws -> BackendClient {
    try BackendClient(baseURL: URL(string: "http://127.0.0.1:8765")!, transport: transport)
}

/// Returns the error thrown by an async expression, or fails the test if nothing was thrown.
func thrownError<T>(
    _ expression: @autoclosure () async throws -> T,
    file: StaticString = #filePath, line: UInt = #line
) async -> Error? {
    do {
        _ = try await expression()
        XCTFail("Expected an error", file: file, line: line)
        return nil
    } catch {
        return error
    }
}

/// Polls until the condition holds. The controller works in background tasks.
@MainActor
func waitUntil(timeout: TimeInterval = 2, _ condition: () -> Bool) async {
    let deadline = Date().addingTimeInterval(timeout)
    while !condition() && Date() < deadline {
        try? await Task.sleep(nanoseconds: 5_000_000)
    }
}

final class FakeRecorder: AudioRecording {
    var permission: MicPermission
    var grantOnRequest = true
    var startError: Error?
    var audio: Data? = Data([1, 2, 3])
    private(set) var started = 0
    private(set) var finished = 0
    private(set) var cancelled = 0
    private(set) var permissionRequests = 0
    private var onLevel: ((Float) -> Void)?

    init(permission: MicPermission = .granted) { self.permission = permission }

    func requestPermission(_ completion: @escaping (Bool) -> Void) {
        permissionRequests += 1
        if grantOnRequest { permission = .granted }
        completion(grantOnRequest)
    }

    func startRecording(onLevel: @escaping (Float) -> Void) throws {
        if let error = startError { throw error }
        started += 1
        self.onLevel = onLevel
    }

    func finishRecording() -> Data? {
        finished += 1
        return audio
    }

    func cancelRecording() { cancelled += 1 }

    /// Pretend the microphone measured this loudness (dB).
    func emit(_ level: Float) { onLevel?(level) }
}

final class FakePlayer: AudioPlaying {
    var playError: Error?
    private(set) var played: [Data] = []
    private(set) var stopped = 0
    private var completion: ((Error?) -> Void)?

    func play(wav: Data, completion: @escaping (Error?) -> Void) throws {
        if let error = playError { throw error }
        self.completion = completion
        played.append(wav)
    }

    func stop() {
        stopped += 1
        completion = nil
    }

    func finishPlaying(_ error: Error? = nil) {
        let done = completion
        completion = nil
        done?(error)
    }
}

final class FakeSpeech: SpeechOutput {
    var isAvailable = true
    private(set) var spoken: [String] = []
    private(set) var stopped = 0
    private var onEvent: ((SpeechEvent) -> Void)?

    func speak(_ text: String, onEvent: @escaping (SpeechEvent) -> Void) {
        spoken.append(text)
        self.onEvent = onEvent
    }

    func stop() { stopped += 1 }
    func emit(_ event: SpeechEvent) { onEvent?(event) }
}

/// A clock the test moves by hand.
final class ManualClock {
    var now = Date(timeIntervalSince1970: 1_000)
    func advance(_ seconds: TimeInterval) { now = now.addingTimeInterval(seconds) }
    func read() -> Date { now }
}
