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

    static func decision(_ name: String) throws -> GuardDecision {
        try JSONDecoder().decode(GuardDecision.self, from: data(name))
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
