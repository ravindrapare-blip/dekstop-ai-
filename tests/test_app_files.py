"""Checks on the parts of app/ that Python can verify. The Swift code itself is built and tested on a Mac
(see app/README.md)."""
import json
import plistlib
import re
import subprocess

from backend.config import ROOT

APP = ROOT / "app"


def test_info_plist_is_valid_and_set_up_for_a_menu_bar_app():
    info = plistlib.loads((APP / "Resources" / "Info.plist").read_bytes())
    assert info["LSUIElement"] is True                      # no Dock icon
    assert info["LSMinimumSystemVersion"] == "12.0"         # Monterey
    assert info["CFBundleExecutable"] == "Assistant"        # build.sh copies the binary under this name
    assert info["NSAppTransportSecurity"] == {"NSAllowsLocalNetworking": True}
    assert "microphone" in info["NSMicrophoneUsageDescription"].lower()


def test_package_targets_monterey_with_no_dependencies():
    text = (APP / "Package.swift").read_text()
    assert "swift-tools-version:5.7" in text                # the Swift that ships with Xcode 14.2
    assert ".macOS(.v12)" in text
    assert "dependencies:" not in text.replace('dependencies: ["CapsuleCore"]', "")
    assert ".package(" not in text


def test_example_config_matches_the_documented_defaults():
    example = json.loads((APP / "ui.example.json").read_text())
    assert example == {"backend_url": "http://127.0.0.1:8765", "hotkey": "cmd+shift+space",
                       "hide_after_seconds": 20, "speak_replies": "voice"}
    config_source = (APP / "Sources" / "CapsuleCore" / "UIConfig.swift").read_text()
    assert 'URL(string: "http://127.0.0.1:8765")' in config_source
    assert "hideAfterSeconds: 20" in config_source
    assert "speakReplies: .voiceOnly" in config_source


def test_default_backend_port_matches_the_python_config():
    import yaml
    port = yaml.safe_load((ROOT / "config.yaml").read_text())["backend"]["port"]
    assert f"127.0.0.1:{port}" in (APP / "ui.example.json").read_text()


def test_build_script_has_valid_syntax():
    subprocess.run(["bash", "-n", str(APP / "build.sh")], check=True)


def test_hotkey_table_has_no_typos():
    source = (APP / "Sources" / "CapsuleCore" / "Hotkey.swift").read_text()
    block = source[source.index("keyCodes"):]
    pairs = re.findall(r'"([a-z0-9]+)": (\d+)', block)
    codes = {name: int(code) for name, code in pairs}
    assert len(codes) == len(pairs)                          # no key listed twice
    assert len(set(codes.values())) == len(codes)            # no two keys share a code
    assert codes["space"] == 49
    assert {k: codes[k] for k in "asdfhgzxcvbqwertyuiopljknm"} == {
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
        "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "o": 31, "u": 32, "i": 34, "p": 35,
        "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    }
    assert [codes[d] for d in "1234567890"] == [18, 19, 20, 21, 23, 22, 26, 28, 25, 29]
    masks = dict(re.findall(r'"(cmd|shift|opt|ctrl)": (\d+)', source))
    assert masks == {"cmd": "256", "shift": "512", "opt": "2048", "ctrl": "4096"}


def test_swift_layout_matches_the_documented_sizes():
    source = (APP / "Sources" / "CapsuleCore" / "CapsuleLayout.swift").read_text()
    sizes = {name: float(value) for name, value in
             re.findall(r"static let (\w+): Double = (\d+)", source)}
    assert sizes["width"] == 440
    assert sizes["inputRow"] == 40
    assert sizes["verticalPadding"] * 2 + sizes["inputRow"] == 56   # the collapsed pill


def test_the_ui_never_imports_the_backend_code_or_runs_commands():
    for path in (APP / "Sources").rglob("*.swift"):
        text = path.read_text()
        assert not re.search(r"\b(Process\(\)|NSAppleScript|posix_spawn|NSWorkspace)|(?<![\w.])system\(", text), path.name
    # only the core module touches the network
    for path in (APP / "Sources" / "AssistantApp").glob("*.swift"):
        assert "URLSession" not in path.read_text(), path.name


def test_the_voice_pipeline_pieces_are_split_between_core_and_app():
    core = APP / "Sources" / "CapsuleCore"
    appdir = APP / "Sources" / "AssistantApp"
    # AVFoundation (microphone and speakers) lives only in the app target, so the core builds anywhere
    for path in core.glob("*.swift"):
        assert "import AVFoundation" not in path.read_text(), path.name
    assert "AVAudioRecorder" in (appdir / "MicRecorder.swift").read_text()
    assert "AVAudioPlayer" in (appdir / "AudioPlayer.swift").read_text()
    recorder = (appdir / "MicRecorder.swift").read_text()
    for setting in ("16_000", "AVNumberOfChannelsKey: 1", "AVLinearPCMBitDepthKey: 16"):
        assert setting in recorder            # what whisper.cpp needs
    assert "removeItem" in recorder           # the recording is deleted after use


def test_the_app_asks_macos_for_microphone_access_with_a_clear_reason():
    import plistlib
    info = plistlib.loads((APP / "Resources" / "Info.plist").read_bytes())
    text = info["NSMicrophoneUsageDescription"]
    assert "microphone button" in text and "on this Mac" in text


def test_voice_audio_settings_match_the_backend():
    from voice.audio import SAMPLE_RATE
    assert SAMPLE_RATE == 16_000
    assert "16 kHz" in (ROOT / "app" / "Sources" / "CapsuleCore" / "Ports.swift").read_text()
