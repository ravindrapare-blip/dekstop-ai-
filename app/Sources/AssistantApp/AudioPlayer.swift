import AVFoundation
import CapsuleCore

enum PlaybackError: Error {
    case couldNotPlay
}

/// Plays the WAV audio of a spoken reply with AVAudioPlayer.
final class AudioPlayer: NSObject, AudioPlaying, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private var completion: ((Error?) -> Void)?

    func play(wav: Data, completion: @escaping (Error?) -> Void) throws {
        stop()
        let player = try AVAudioPlayer(data: wav)
        player.delegate = self
        guard player.prepareToPlay(), player.play() else { throw PlaybackError.couldNotPlay }
        self.player = player
        self.completion = completion
    }

    /// Stops playback. The completion is not called: the caller asked for the stop.
    func stop() {
        player?.stop()
        player = nil
        completion = nil
    }

    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        let done = completion
        self.player = nil
        completion = nil
        done?(flag ? nil : PlaybackError.couldNotPlay)
    }

    func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        let done = completion
        self.player = nil
        completion = nil
        done?(error ?? PlaybackError.couldNotPlay)
    }
}
