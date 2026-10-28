import io
import json
import logging
import stat
import subprocess
import sys
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import ROOT
from backend.llm import OllamaClient
from backend.main import create_app
from fakes import quick_actions
from safety import GuardrailManager, Policy
from voice import VoiceError, VoiceService, voice_service_from_config
from voice.audio import MAX_AUDIO_BYTES, is_playable_wav, wav_duration
from voice.service import prepare_text
from voice.stt import WhisperTranscriber, clean_transcript
from voice.tts import PiperSynthesizer, SaySynthesizer


def make_wav(seconds=1.0, rate=16000, channels=1, width=2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate) * channels * width)
    return buf.getvalue()


# ---- fake engines: real executables, so real argument passing, stdin and files are exercised ----------

def script(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\nimport json, os, sys, wave\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


RECORD = "open(os.environ['FAKE_LOG'], 'a').write(json.dumps({'argv': sys.argv[1:], 'stdin': STDIN}) + '\\n')\n"

WHISPER = "STDIN = ''\n" + RECORD + "print(os.environ.get('FAKE_TRANSCRIPT', ' Open Chrome. '))\n"

WRITE_WAV = """
def write_wav(path):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050); w.writeframes(b'\\x00\\x00' * 11025)
"""

PIPER = ("STDIN = sys.stdin.read()\n" + RECORD + WRITE_WAV +
         "out = sys.argv[sys.argv.index('--output_file') + 1]\nwrite_wav(out)\n")

SAY = ("STDIN = sys.stdin.read()\n" + RECORD + WRITE_WAV +
       "out = sys.argv[sys.argv.index('-o') + 1]\nwrite_wav(out)\n")


@pytest.fixture
def fake_log(tmp_path, monkeypatch):
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return calls


@pytest.fixture
def whisper(tmp_path):
    binary = script(tmp_path / "whisper-cli", WHISPER)
    model = tmp_path / "ggml-tiny.en.bin"
    model.write_bytes(b"model")
    return WhisperTranscriber(str(binary), str(model), tmp_path, threads=2, language="en", timeout=20)


@pytest.fixture
def piper(tmp_path):
    binary = script(tmp_path / "piper", PIPER)
    voice = tmp_path / "voice.onnx"
    voice.write_bytes(b"onnx")
    Path(str(voice) + ".json").write_text("{}")
    return PiperSynthesizer(str(binary), str(voice), tmp_path, timeout=20)


# ---- the transcription path ---------------------------------------------------------------------------

def test_transcript_cleaning():
    assert clean_transcript(" Open   Chrome. \n") == "Open Chrome."
    assert clean_transcript("[BLANK_AUDIO]") == ""
    assert clean_transcript(" [ Silence ] ") == ""
    assert clean_transcript("(music) call mom *Music*") == "call mom"
    assert clean_transcript("call mom (not dad)") == "call mom (not dad)"   # real speech is kept


def test_a_recording_is_transcribed_by_whisper(whisper, fake_log):
    assert whisper.transcribe(make_wav(1.0)) == "Open Chrome."
    (call,) = fake_log()
    argv = call["argv"]
    assert argv[argv.index("-t") + 1] == "2" and argv[argv.index("-l") + 1] == "en"
    assert "-nt" in argv and "-np" in argv
    assert argv[argv.index("-m") + 1].endswith("ggml-tiny.en.bin")
    audio = Path(argv[argv.index("-f") + 1])
    assert audio.suffix == ".wav"
    assert not audio.exists()                      # the recording is deleted afterwards
    assert not audio.parent.exists()


def test_recording_is_deleted_even_when_whisper_fails(tmp_path, fake_log):
    binary = script(tmp_path / "whisper-cli", WHISPER + "sys.exit(3)\n")
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    stt = WhisperTranscriber(str(binary), str(model), tmp_path, timeout=20)
    with pytest.raises(VoiceError) as e:
        stt.transcribe(make_wav(1.0))
    assert e.value.code == "failed"
    audio = Path(fake_log()[0]["argv"][-1])
    assert not audio.exists()


def test_silence_markers_give_an_empty_transcript(whisper, monkeypatch, fake_log):
    monkeypatch.setenv("FAKE_TRANSCRIPT", " [BLANK_AUDIO]")
    assert whisper.transcribe(make_wav(1.0)) == ""


def test_a_tap_is_not_sent_to_the_engine(tmp_path):
    def must_not_run(*a, **k):
        raise AssertionError("engine should not run")

    stt = WhisperTranscriber("x", "y", tmp_path, runner=must_not_run)
    assert VoiceService(stt, []).transcribe(make_wav(0.1)) == ""


def test_missing_engine_and_model_explain_the_fix(tmp_path):
    stt = WhisperTranscriber("nope-whisper", "nope.bin", tmp_path)
    status = stt.availability()
    assert not status.ok and status.code == "engine_missing" and "docs/voice.md" in status.fix
    with pytest.raises(VoiceError) as e:
        stt.transcribe(make_wav(1.0))
    assert e.value.code == "engine_missing" and e.value.fix

    binary = script(tmp_path / "whisper-cli", WHISPER)
    stt = WhisperTranscriber(str(binary), "missing.bin", tmp_path)
    assert stt.availability().code == "model_missing"


@pytest.mark.parametrize("problem,code", [
    (subprocess.TimeoutExpired("whisper", 1), "timeout"),
    (PermissionError("denied"), "failed"),
])
def test_engine_problems_are_reported(whisper, problem, code):
    def broken(*a, **k):
        raise problem

    whisper._run = broken
    with pytest.raises(VoiceError) as e:
        whisper.transcribe(make_wav(1.0))
    assert e.value.code == code


def test_engine_errors_do_not_leak_details(whisper):
    whisper._run = lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="/secret/path exploded")
    with pytest.raises(VoiceError) as e:
        whisper.transcribe(make_wav(1.0))
    assert "secret" not in e.value.detail


def test_audio_must_be_what_whisper_needs():
    assert wav_duration(make_wav(1.5)) == pytest.approx(1.5)
    for data, code in [
        (b"not a wav", "bad_audio"),
        (b"", "bad_audio"),
        (make_wav(1.0, rate=44100), "bad_audio"),
        (make_wav(1.0, channels=2), "bad_audio"),
        (make_wav(1.0, width=1), "bad_audio"),
        (make_wav(31.0), "too_large"),
        (b"\0" * (MAX_AUDIO_BYTES + 1), "too_large"),
    ]:
        with pytest.raises(VoiceError) as e:
            wav_duration(data)
        assert e.value.code == code


# ---- speech generation -----------------------------------------------------------------------------------

def test_piper_generates_playable_speech(piper, fake_log):
    wav = piper.synthesize("Opened Google Chrome.")
    assert is_playable_wav(wav)
    (call,) = fake_log()
    assert call["stdin"] == "Opened Google Chrome."
    argv = call["argv"]
    assert argv[argv.index("--model") + 1].endswith("voice.onnx")
    assert "--quiet" in argv


@pytest.mark.parametrize("text", ["--help", "; rm -rf / #", "$(touch /tmp/pwned)", "-m other.onnx"])
def test_text_is_never_a_command_line_argument(piper, fake_log, text):
    piper.synthesize(text)
    (call,) = fake_log()
    assert call["stdin"] == text
    assert text not in call["argv"] and not any(text in a for a in call["argv"])


def test_missing_piper_or_voice_is_reported(tmp_path):
    p = PiperSynthesizer("no-such-piper", "v.onnx", tmp_path)
    assert p.availability().code == "engine_missing"
    binary = script(tmp_path / "piper", PIPER)
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"x")
    p = PiperSynthesizer(str(binary), str(voice), tmp_path)
    status = p.availability()
    assert status.code == "model_missing" and ".json" in status.reason     # the config file is needed too
    Path(str(voice) + ".json").write_text("{}")
    assert p.availability().ok


def test_a_failing_or_silent_engine_is_an_error(tmp_path, piper):
    piper._run = lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="boom")
    with pytest.raises(VoiceError) as e:
        piper.synthesize("hi")
    assert e.value.code == "failed"
    piper._run = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr="")   # wrote no file
    with pytest.raises(VoiceError, match="no audio"):
        piper.synthesize("hi")


def test_mac_voice_is_used_through_stdin_and_only_on_a_mac(tmp_path, monkeypatch, fake_log):
    script(tmp_path / "say", SAY)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    assert not SaySynthesizer(platform="linux").availability().ok
    say = SaySynthesizer(platform="darwin", timeout=20)
    assert say.availability().ok
    assert is_playable_wav(say.synthesize("Hello there"))
    (call,) = fake_log()
    assert call["stdin"] == "Hello there"
    assert "--file-format=WAVE" in call["argv"] and call["argv"][-2:] == ["-f", "-"]
    assert "Hello there" not in call["argv"]


# ---- the service: fallback chain, limits, status ---------------------------------------------------------

class FakeEngine:
    def __init__(self, name, wav=b"", error=None, ok=True):
        self.name, self._wav, self._error, self._ok = name, wav, error, ok
        self.heard = []

    def availability(self):
        from voice.base import Availability
        return Availability(self._ok, "" if self._ok else f"{self.name} is missing", "" if self._ok else "engine_missing")

    def synthesize(self, text):
        self.heard.append(text)
        if self._error:
            raise self._error
        return self._wav

    def transcribe(self, wav):
        if self._error:
            raise self._error
        return self._wav.decode()


GOOD_WAV = make_wav(0.2)


def test_speech_falls_back_to_the_next_engine():
    broken = FakeEngine("Piper", error=VoiceError("engine_missing", "Piper is not installed."))
    backup = FakeEngine("Mac voice", wav=GOOD_WAV)
    service = VoiceService(FakeEngine("stt"), [broken, backup])
    assert service.speak("Hello") == GOOD_WAV
    assert backup.heard == ["Hello"]


def test_when_every_engine_fails_the_preferred_engines_problem_is_reported():
    first = VoiceError("model_missing", "The Piper voice is missing.", "Download one.")
    service = VoiceService(FakeEngine("stt"), [
        FakeEngine("Piper", error=first),
        FakeEngine("Mac voice", error=VoiceError("failed", "say failed.")),
    ])
    with pytest.raises(VoiceError) as e:
        service.speak("Hello")
    assert e.value is first


def test_no_engines_at_all():
    with pytest.raises(VoiceError) as e:
        VoiceService(FakeEngine("stt"), []).speak("Hello")
    assert e.value.code == "engine_missing"


def test_replies_are_cleaned_and_capped_before_speaking():
    assert prepare_text("Line one\n\tline\x00 two") == "Line one line two"
    # cut at the last full sentence when that keeps most of the text...
    long = "word " * 15 + "end. " + "word " * 100
    assert prepare_text(long, 100) == "word " * 15 + "end."
    # ...otherwise at a word boundary, with an ellipsis
    rambling = "First. " + "word " * 200
    cut = prepare_text(rambling, 100)
    assert cut.endswith("word…") and len(cut) <= 101
    assert prepare_text("x " * 1000, 50).endswith("…")
    assert prepare_text("  \n\x07 ") == ""
    engine = FakeEngine("Piper", wav=GOOD_WAV)
    service = VoiceService(FakeEngine("stt"), [engine], max_text_chars=30)
    service.speak("Hello\nthere. " + "more " * 50)
    assert engine.heard[0].startswith("Hello there. more") and len(engine.heard[0]) <= 31


def test_nothing_to_say_is_an_error():
    with pytest.raises(VoiceError) as e:
        VoiceService(FakeEngine("stt"), [FakeEngine("Piper", wav=GOOD_WAV)]).speak(" \n ")
    assert e.value.code == "bad_text"


def test_voice_can_be_turned_off():
    service = VoiceService(FakeEngine("stt", wav=b"hi"), [FakeEngine("Piper", wav=GOOD_WAV)], enabled=False)
    for call in (lambda: service.speak("x"), lambda: service.transcribe(GOOD_WAV)):
        with pytest.raises(VoiceError) as e:
            call()
        assert e.value.code == "disabled" and "config.yaml" in e.value.fix
    assert service.status()["enabled"] is False


def test_status_lists_every_engine():
    service = VoiceService(FakeEngine("whisper.cpp"), [FakeEngine("Piper", ok=False), FakeEngine("Mac voice")])
    status = service.status()
    assert status["stt"]["available"] is True
    assert status["tts"]["available"] is True
    assert [e["engine"] for e in status["tts"]["engines"]] == ["Piper", "Mac voice"]
    assert status["tts"]["engines"][0]["available"] is False


def test_service_is_built_from_config_with_paths_relative_to_the_project(tmp_path):
    cfg = {"voice": {"whisper": {"binary": "bin/w", "model": "m.bin", "threads": 1},
                     "piper": {"binary": "piper", "voice": "v.onnx"}, "system_voice_fallback": False,
                     "max_text_chars": 123}}
    service = voice_service_from_config(cfg, tmp_path)
    assert service.max_text_chars == 123 and service.enabled
    assert [e.name for e in service.synthesizers] == ["Piper"]
    assert service.stt.threads == 1 and service.stt.root == tmp_path
    default = voice_service_from_config({}, tmp_path)
    assert [e.name for e in default.synthesizers] == ["Piper", "the macOS voice"]


def test_the_project_config_builds_a_service():
    from backend.config import load_config
    service = voice_service_from_config(load_config(), ROOT)
    assert service.status()["stt"]["engine"] == "whisper.cpp"


# ---- HTTP -------------------------------------------------------------------------------------------------

POLICY = Policy.load(ROOT / "safety" / "policy.yaml")


def api(voice):
    llm = OllamaClient("http://127.0.0.1:11434", "m", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    cfg = {"ollama": {}, "router": {"max_retries": 0}}
    return TestClient(create_app(cfg, llm, guard=GuardrailManager(POLICY), voice=voice, actions=quick_actions()))


def ok_service(text="open chrome"):
    return VoiceService(FakeEngine("stt", wav=text.encode()), [FakeEngine("Piper", wav=GOOD_WAV)])


def test_api_transcribe_returns_the_text():
    with api(ok_service("open chrome")) as c:
        res = c.post("/voice/transcribe", content=make_wav(1.0), headers={"content-type": "audio/wav"})
    assert res.status_code == 200 and res.json() == {"text": "open chrome"}


def test_api_transcribe_rejects_bad_audio():
    with api(ok_service()) as c:
        for body in (b"", b"garbage", make_wav(1.0, rate=8000)):
            res = c.post("/voice/transcribe", content=body)
            assert res.status_code == 400 and res.json()["code"] == "bad_audio"
        assert c.post("/voice/transcribe", content=b"x" * (MAX_AUDIO_BYTES + 1)).status_code == 413


def test_api_voice_problems_have_clear_statuses_and_fixes():
    missing = VoiceService(
        FakeEngine("stt", error=VoiceError("model_missing", "The speech model is missing.", "Download one.")),
        [FakeEngine("Piper", error=VoiceError("engine_missing", "Piper is not installed.", "Install it."))],
    )
    with api(missing) as c:
        res = c.post("/voice/transcribe", content=make_wav(1.0))
        assert res.status_code == 503 and res.json()["code"] == "model_missing" and "Download one." in res.json()["detail"]
        res = c.post("/voice/speak", json={"text": "hello"})
        assert res.status_code == 503 and res.json()["code"] == "engine_missing"
    flaky = VoiceService(FakeEngine("stt", error=VoiceError("timeout", "Too slow.")), [])
    with api(flaky) as c:
        assert c.post("/voice/transcribe", content=make_wav(1.0)).status_code == 502


def test_api_speak_returns_audio():
    with api(ok_service()) as c:
        res = c.post("/voice/speak", json={"text": "Opened Google Chrome."})
    assert res.status_code == 200 and res.headers["content-type"] == "audio/wav"
    assert res.content == GOOD_WAV


def test_api_speak_validates_input():
    with api(ok_service()) as c:
        assert c.post("/voice/speak", json={}).status_code == 422
        assert c.post("/voice/speak", json={"text": ""}).status_code == 422
        assert c.post("/voice/speak", json={"text": "x" * 2001}).status_code == 422
        assert c.post("/voice/speak", json={"text": " \n "}).status_code == 400


def test_api_status():
    with api(ok_service()) as c:
        body = c.get("/voice/status").json()
    assert body["enabled"] and body["stt"]["available"] and body["tts"]["available"]


def test_what_you_say_is_not_written_to_the_log(caplog):
    caplog.set_level(logging.DEBUG)
    with api(ok_service("my secret plan")) as c:
        c.post("/voice/transcribe", content=make_wav(1.0))
        c.post("/voice/speak", json={"text": "another private sentence"})
    assert "secret" not in caplog.text and "private" not in caplog.text


# ---- the self-test round trip (scripts/voice_check.py) ---------------------------------------------------

def test_speech_can_be_converted_for_whisper():
    from voice.audio import to_16k_mono
    stereo_22k = make_wav(1.0, rate=22050, channels=2)
    converted = to_16k_mono(stereo_22k)
    assert wav_duration(converted) == pytest.approx(1.0, abs=0.01)    # valid for whisper, same length
    assert to_16k_mono(make_wav(0.5)) is not None
    with pytest.raises(VoiceError):
        to_16k_mono(b"junk")
    with pytest.raises(VoiceError):
        to_16k_mono(make_wav(1.0, width=1))


def test_round_trip_with_real_processes(whisper, piper, monkeypatch, fake_log):
    from voice.audio import to_16k_mono
    monkeypatch.setenv("FAKE_TRANSCRIPT", "Testing one two three.")
    service = VoiceService(whisper, [piper])
    heard = service.transcribe(to_16k_mono(service.speak("Testing one two three.")))
    assert heard == "Testing one two three."
    assert [bool(c["stdin"]) for c in fake_log()] == [True, False]   # piper got text, whisper got a file
