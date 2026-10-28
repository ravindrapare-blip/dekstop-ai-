import AVFoundation
import CapsuleCore

enum RecordingError: Error {
    case couldNotStart
}

/// The microphone, using AVAudioRecorder. It records 16 kHz, 16-bit, mono WAV, which is what
/// whisper.cpp needs, into a temporary file that is deleted as soon as the audio has been read.
/// The permission belongs to this app, which is why recording happens here and not in the backend.
final class MicRecorder: NSObject, AudioRecording {
    private var recorder: AVAudioRecorder?
    private var fileURL: URL?
    private var meterTimer: Timer?

    var permission: MicPermission {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return .granted
        case .notDetermined: return .notDetermined
        case .restricted: return .restricted
        case .denied: return .denied
        @unknown default: return .denied
        }
    }

    func requestPermission(_ completion: @escaping (Bool) -> Void) {
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            DispatchQueue.main.async { completion(granted) }
        }
    }

    func startRecording(onLevel: @escaping (Float) -> Void) throws {
        cancelRecording()

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("assistant-\(UUID().uuidString).wav")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatLinearPCM),
            AVSampleRateKey: 16_000.0,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        let recorder = try AVAudioRecorder(url: url, settings: settings)
        recorder.isMeteringEnabled = true
        guard recorder.prepareToRecord(), recorder.record() else {
            try? FileManager.default.removeItem(at: url)
            throw RecordingError.couldNotStart
        }
        self.recorder = recorder
        self.fileURL = url

        // Loudness ten times a second, only while recording; the controller decides when to stop.
        meterTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            guard let recorder = self?.recorder else { return }
            recorder.updateMeters()
            onLevel(recorder.averagePower(forChannel: 0))
        }
    }

    func finishRecording() -> Data? {
        meterTimer?.invalidate()
        meterTimer = nil
        guard let recorder = recorder, let url = fileURL else { return nil }
        recorder.stop()
        self.recorder = nil
        self.fileURL = nil
        defer { try? FileManager.default.removeItem(at: url) }   // never keep audio
        return try? Data(contentsOf: url)
    }

    func cancelRecording() {
        meterTimer?.invalidate()
        meterTimer = nil
        recorder?.stop()
        if let url = fileURL { try? FileManager.default.removeItem(at: url) }
        recorder = nil
        fileURL = nil
    }
}
