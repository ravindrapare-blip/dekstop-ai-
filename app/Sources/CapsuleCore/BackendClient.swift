import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public enum BackendError: Error, Equatable {
    case notLocal(String)
    case unreachable
    case timedOut
    case server(status: Int, detail: String)
    case badResponse
}

/// The only network surface of the app. Tests replace it with a fake.
public protocol HTTPTransport {
    /// Returns the body and HTTP status. Throws URLError for connection problems.
    func send(_ request: URLRequest) async throws -> (Data, Int)
}

private final class TaskBox: @unchecked Sendable {
    var task: URLSessionDataTask?
}

public struct URLSessionTransport: HTTPTransport {
    private let session: URLSession

    /// The default timeout is a little longer than the backend's own model timeout (120 s),
    /// because a 2017 MacBook Air can be slow to answer.
    public init(timeout: TimeInterval = 135) {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = timeout
        config.timeoutIntervalForResource = timeout
        config.waitsForConnectivity = false
        config.urlCache = nil
        session = URLSession(configuration: config)
    }

    public func send(_ request: URLRequest) async throws -> (Data, Int) {
        let box = TaskBox()
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<(Data, Int), Error>) in
                let task = session.dataTask(with: request) { data, response, error in
                    if let error = error {
                        continuation.resume(throwing: error)
                    } else if let http = response as? HTTPURLResponse {
                        continuation.resume(returning: (data ?? Data(), http.statusCode))
                    } else {
                        continuation.resume(throwing: BackendError.badResponse)
                    }
                }
                box.task = task
                task.resume()
            }
        }, onCancel: {
            box.task?.cancel()
        })
    }
}

public func isLoopbackHost(_ host: String?) -> Bool {
    guard let host = host?.lowercased() else { return false }
    return host == "127.0.0.1" || host == "localhost" || host == "::1" || host == "[::1]"
}

/// Talks to the Python backend. Only loopback addresses are accepted.
public struct BackendClient {
    public let baseURL: URL
    private let transport: HTTPTransport

    public init(baseURL: URL, transport: HTTPTransport = URLSessionTransport()) throws {
        guard isLoopbackHost(baseURL.host) else { throw BackendError.notLocal(baseURL.absoluteString) }
        self.baseURL = baseURL
        self.transport = transport
    }

    private struct RouteBody: Encodable {
        let text: String
        let dry_run: Bool?
    }

    private struct ChooseBody: Encodable {
        let choice_id: String
        let option_id: String?
    }

    private struct ConfirmBody: Encodable {
        let confirmation_id: String
        let approved: Bool
        let acknowledge_risk: Bool
    }

    public func health() async -> Bool {
        var request = URLRequest(url: baseURL.appendingPathComponent("health"))
        request.timeoutInterval = 3
        guard let result = try? await transport.send(request) else { return false }
        return result.1 == 200
    }

    public func route(text: String, dryRun: Bool? = nil) async throws -> RouteResponse {
        try await post("route", body: RouteBody(text: text, dry_run: dryRun))
    }

    public func confirm(id: String, approved: Bool, acknowledgeRisk: Bool) async throws -> ConfirmResponse {
        try await post(
            "confirm",
            body: ConfirmBody(confirmation_id: id, approved: approved, acknowledge_risk: acknowledgeRisk)
        )
    }

    /// Answer "which one did you mean?". Passing nil cancels.
    public func choose(choiceId: String, optionId: String?) async throws -> RouteResponse {
        try await post("choose", body: ChooseBody(choice_id: choiceId, option_id: optionId))
    }

    /// Sends WAV audio to the backend and returns the words. An empty string means nothing was heard.
    public func transcribe(wav: Data) async throws -> String {
        var request = URLRequest(url: baseURL.appendingPathComponent("voice/transcribe"))
        request.httpMethod = "POST"
        request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        request.httpBody = wav
        let reply: TranscribeReply = try decode(try await perform(request))
        return reply.text
    }

    /// Asks the backend to turn text into speech and returns WAV audio.
    public func speak(text: String) async throws -> Data {
        var request = URLRequest(url: baseURL.appendingPathComponent("voice/speak"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(SpeakBody(text: text))
        let audio = try await perform(request)
        guard !audio.isEmpty else { throw BackendError.badResponse }
        return audio
    }

    private struct TranscribeReply: Decodable {
        let text: String
    }

    private struct SpeakBody: Encodable {
        let text: String
    }

    private func post<Body: Encodable, Response: Decodable>(_ path: String, body: Body) async throws -> Response {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        return try decode(try await perform(request))
    }

    /// Sends the request and returns the body of a successful answer. Every failure becomes a BackendError.
    private func perform(_ request: URLRequest) async throws -> Data {
        let result: (Data, Int)
        do {
            result = try await transport.send(request)
        } catch let error as URLError {
            switch error.code {
            case .cancelled: throw CancellationError()
            case .timedOut: throw BackendError.timedOut
            default: throw BackendError.unreachable
            }
        }
        let data = result.0
        let status = result.1
        guard (200..<300).contains(status) else {
            throw BackendError.server(status: status, detail: Self.detail(from: data))
        }
        return data
    }

    private func decode<Response: Decodable>(_ data: Data) throws -> Response {
        do {
            return try JSONDecoder().decode(Response.self, from: data)
        } catch {
            throw BackendError.badResponse
        }
    }

    /// FastAPI errors look like {"detail": "..."}; validation errors carry a list instead.
    static func detail(from data: Data) -> String {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return "" }
        if let text = object["detail"] as? String { return text }
        return ""
    }
}
