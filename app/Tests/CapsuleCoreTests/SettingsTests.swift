import XCTest
@testable import CapsuleCore

final class SettingsTests: XCTestCase {
    // MARK: - idle policy

    func testHidesOnlyAfterTheDelay() {
        let policy = IdlePolicy(hideAfter: 20)
        let start = Date(timeIntervalSince1970: 1_000)
        XCTAssertFalse(policy.shouldHide(phase: .idle, lastActivity: start, now: start.addingTimeInterval(19.9)))
        XCTAssertTrue(policy.shouldHide(phase: .idle, lastActivity: start, now: start.addingTimeInterval(20)))
    }

    func testNeverHidesWhileBusy() {
        let policy = IdlePolicy(hideAfter: 5)
        let start = Date(timeIntervalSince1970: 1_000)
        let later = start.addingTimeInterval(600)
        for phase in [Phase.listening, .thinking, .waiting, .executing] {
            XCTAssertFalse(policy.shouldHide(phase: phase, lastActivity: start, now: later), "\(phase)")
        }
        for phase in [Phase.idle, .done, .error] {
            XCTAssertTrue(policy.shouldHide(phase: phase, lastActivity: start, now: later), "\(phase)")
        }
    }

    func testZeroOrNegativeTurnsAutoHideOff() {
        for seconds in [0.0, -5.0] {
            let policy = IdlePolicy(hideAfter: seconds)
            XCTAssertFalse(policy.isEnabled)
            XCTAssertFalse(policy.shouldHide(phase: .idle, lastActivity: .distantPast, now: Date()))
        }
    }

    // MARK: - hotkey

    func testDefaultHotkeyIsCommandShiftSpace() {
        let spec = HotkeySpec.parse("cmd+shift+space")
        XCTAssertEqual(spec, HotkeySpec.default)
        XCTAssertEqual(spec?.keyCode, 49)
        XCTAssertEqual(spec?.modifiers, 256 | 512)
    }

    func testHotkeyParsing() {
        XCTAssertEqual(HotkeySpec.parse("Ctrl + Option + A")?.keyCode, 0)
        XCTAssertEqual(HotkeySpec.parse("ctrl+opt+a")?.modifiers, 4096 | 2048)
        XCTAssertEqual(HotkeySpec.parse("command+shift+9")?.keyCode, 25)
        XCTAssertEqual(HotkeySpec.parse("alt+k")?.modifiers, 2048)
        XCTAssertEqual(HotkeySpec.parse("CMD+SHIFT+SPACE")?.text, "cmd+shift+space")
    }

    func testBadHotkeysAreRefused() {
        for text in ["space", "a", "cmd", "cmd+shift", "cmd+a+b", "cmd+escape", "", "+", "cmd+", "hyper+a"] {
            XCTAssertNil(HotkeySpec.parse(text), "'\(text)' should be refused")
        }
    }

    // MARK: - ui.json

    private func parse(_ json: String) -> (config: UIConfig, warnings: [String]) {
        UIConfig.parse(Data(json.utf8))
    }

    func testDefaults() {
        let d = UIConfig.defaults
        XCTAssertEqual(d.backendURL.absoluteString, "http://127.0.0.1:8765")
        XCTAssertEqual(d.hotkey, HotkeySpec.default)
        XCTAssertEqual(d.hideAfterSeconds, 20)
    }

    func testEmptyObjectKeepsDefaultsWithoutWarnings() {
        let r = parse("{}")
        XCTAssertEqual(r.config, UIConfig.defaults)
        XCTAssertTrue(r.warnings.isEmpty)
    }

    func testValidSettingsAreUsed() {
        let r = parse(#"{"backend_url":"http://localhost:9000","hotkey":"ctrl+opt+space","hide_after_seconds":45}"#)
        XCTAssertTrue(r.warnings.isEmpty)
        XCTAssertEqual(r.config.backendURL.absoluteString, "http://localhost:9000")
        XCTAssertEqual(r.config.hotkey.keyCode, 49)
        XCTAssertEqual(r.config.hideAfterSeconds, 45)
    }

    func testZeroTurnsAutoHideOffWithoutAWarning() {
        let r = parse(#"{"hide_after_seconds":0}"#)
        XCTAssertEqual(r.config.hideAfterSeconds, 0)
        XCTAssertTrue(r.warnings.isEmpty)
    }

    func testRemoteBackendAddressesAreRefused() {
        for url in ["https://example.com", "http://192.168.0.2:8765", "http://0.0.0.0:8765", "ftp://127.0.0.1"] {
            let r = parse(#"{"backend_url":"\#(url)"}"#)
            XCTAssertEqual(r.config.backendURL, UIConfig.defaultBackendURL, url)
            XCTAssertEqual(r.warnings.count, 1, url)
        }
    }

    func testBadValuesFallBackWithAWarning() {
        let bad = [
            #"{"hotkey":"space"}"#,
            #"{"hotkey":5}"#,
            #"{"hide_after_seconds":"20"}"#,
            #"{"hide_after_seconds":true}"#,
            #"{"hide_after_seconds":-1}"#,
            #"{"backend_url":42}"#,
        ]
        for json in bad {
            let r = parse(json)
            XCTAssertEqual(r.config, UIConfig.defaults, json)
            XCTAssertEqual(r.warnings.count, 1, json)
        }
    }

    func testOutOfRangeDelaysAreClamped() {
        XCTAssertEqual(parse(#"{"hide_after_seconds":1}"#).config.hideAfterSeconds, 3)
        XCTAssertEqual(parse(#"{"hide_after_seconds":99999}"#).config.hideAfterSeconds, 600)
        XCTAssertEqual(parse(#"{"hide_after_seconds":1}"#).warnings.count, 1)
    }

    func testNotJSONGivesDefaultsAndAWarning() {
        for text in ["", "nope", "[1,2]", "42"] {
            let r = parse(text)
            XCTAssertEqual(r.config, UIConfig.defaults, text)
            XCTAssertEqual(r.warnings.count, 1, text)
        }
    }

    func testUnknownKeysAreIgnored() {
        let r = parse(#"{"colour":"red","hide_after_seconds":10}"#)
        XCTAssertTrue(r.warnings.isEmpty)
        XCTAssertEqual(r.config.hideAfterSeconds, 10)
    }

    func testMissingFileIsNotAnError() {
        let r = UIConfig.load(from: URL(fileURLWithPath: "/nonexistent/ui.json"))
        XCTAssertEqual(r.config, UIConfig.defaults)
        XCTAssertTrue(r.warnings.isEmpty)
    }

    func testConfigFileLocation() {
        XCTAssertTrue(UIConfig.userConfigURL().path.hasSuffix("LocalAssistant/ui.json"))
    }
}
