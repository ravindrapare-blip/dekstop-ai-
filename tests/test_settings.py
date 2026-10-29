import os
import stat

import pytest

from backend.settings import DEFAULT_GEMINI_MODEL, ENV_FILE_VAR, apply_to_config, load_settings, read_env_file

KEY = "test-key-not-a-real-key-0123456789"


def load(environ=None, cfg=None, env_file=None):
    return load_settings(cfg or {}, environ if environ is not None else {}, env_file)


def test_the_defaults_are_fully_local():
    s = load()
    assert (s.use_gemini, s.use_local_fallback, s.safe_mode, s.stt_engine, s.tts_engine) == (False, True, False, "whisper", "piper")
    assert s.gemini_api_key == "" and s.local_model_name == "" and s.gemini_model == DEFAULT_GEMINI_MODEL and s.warnings == ()
    assert not s.gemini_configured


def test_every_environment_variable_is_read():
    s = load({"GEMINI_API_KEY": KEY, "GEMINI_MODEL": "gemini-test-1", "USE_GEMINI": "true", "USE_LOCAL_FALLBACK": "false", "SAFE_MODE": "1",
              "LOCAL_MODEL_NAME": "qwen2.5:3b", "STT_ENGINE": "macos", "TTS_ENGINE": "say"})
    assert (s.gemini_api_key, s.gemini_model, s.use_gemini, s.use_local_fallback, s.safe_mode) == (KEY, "gemini-test-1", True, False, True)
    assert (s.local_model_name, s.stt_engine, s.tts_engine, s.key_source) == ("qwen2.5:3b", "macos", "say", "environment")
    assert s.gemini_configured and s.warnings == ()


@pytest.mark.parametrize("text,expected", [("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
                                           ("false", False), ("0", False), ("no", False), ("off", False)])
def test_flags_are_forgiving(text, expected):
    assert load({"USE_GEMINI": text}).use_gemini is expected


def test_a_flag_that_makes_no_sense_keeps_the_default_and_says_so():
    s = load({"USE_LOCAL_FALLBACK": "maybe"})
    assert s.use_local_fallback is True and any("USE_LOCAL_FALLBACK" in w for w in s.warnings)
    s = load({"SAFE_MODE": "perhaps"}, cfg={"safety": {"safe_mode": True}})
    assert s.safe_mode is True and any("SAFE_MODE" in w for w in s.warnings)


def test_the_order_is_environment_then_dotenv_then_config_then_default(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_MODEL=from-file\nUSE_GEMINI=true\nSAFE_MODE=true\n")
    cfg = {"brain": {"gemini_model": "from-config", "use_gemini": False}, "safety": {"safe_mode": False}}
    s = load({"GEMINI_MODEL": "from-env"}, cfg, env_file)
    assert s.gemini_model == "from-env"                       # the environment beats the file
    assert s.use_gemini is True and s.safe_mode is True       # the file beats config.yaml
    assert load({}, cfg, tmp_path / "missing.env").gemini_model == "from-config"      # config.yaml beats the built-in default
    assert load({"USE_GEMINI": ""}, cfg, env_file).use_gemini is True                 # an empty variable doesn't count as set


def test_the_dotenv_file_is_parsed_simply_and_only_known_names_are_kept(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# a comment\n\nexport USE_GEMINI = true\nGEMINI_MODEL="quoted-model"\nLOCAL_MODEL_NAME=\'qwen2.5:3b\'\nPATH=/evil\nNOT_A_SETTING=1\nbroken line\n')
    values, warnings = read_env_file(f)
    assert values == {"USE_GEMINI": "true", "GEMINI_MODEL": "quoted-model", "LOCAL_MODEL_NAME": "qwen2.5:3b"} and warnings == []
    assert read_env_file(tmp_path / "absent") == ({}, [])


def test_a_dotenv_file_other_users_can_read_gets_a_warning_only_when_it_holds_the_key(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"GEMINI_API_KEY={KEY}\n")
    os.chmod(f, 0o644)
    assert any("chmod 600" in w for w in load({}, env_file=f).warnings)
    os.chmod(f, 0o600)
    assert not any("chmod" in w for w in load({}, env_file=f).warnings)
    f.write_text("SAFE_MODE=true\n")
    os.chmod(f, 0o644)
    assert not any("chmod" in w for w in load({}, env_file=f).warnings)


def test_the_key_is_read_from_the_dotenv_file_and_its_source_is_named_without_showing_it(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"GEMINI_API_KEY={KEY}\nUSE_GEMINI=true\n")
    os.chmod(f, 0o600)
    s = load({}, env_file=f)
    assert s.gemini_api_key == KEY and s.key_source == ".env" and s.gemini_configured


def test_the_key_never_appears_when_the_settings_are_printed():
    s = load({"GEMINI_API_KEY": KEY})
    assert KEY not in repr(s) and KEY not in str(s)


@pytest.mark.parametrize("bad", ["short", "has spaces in it which is long enough?", "bad/chars/in/this/key/value/xx", "key\nwith newline 0123456789", "k" * 300])
def test_something_that_is_not_a_key_is_ignored_with_a_warning_that_does_not_repeat_it(bad):
    s = load({"GEMINI_API_KEY": bad, "USE_GEMINI": "true"})
    assert s.gemini_api_key == "" and not s.gemini_configured
    assert any("doesn't look like a Gemini key" in w for w in s.warnings) and not any(bad in w for w in s.warnings if len(bad) > 10)


def test_the_key_cannot_come_from_config_yaml():
    s = load({}, cfg={"brain": {"gemini_api_key": KEY, "use_gemini": True}, "gemini_api_key": KEY})
    assert s.gemini_api_key == ""


def test_turning_gemini_on_without_a_key_explains_what_happens():
    s = load({"USE_GEMINI": "true"})
    assert not s.gemini_configured and any("no GEMINI_API_KEY" in w and "local model" in w for w in s.warnings)


@pytest.mark.parametrize("model", ["gemini-x/../../v1/other", "gemini x", "gemini?key=1", "../etc", "", "g" * 80, "gemini\nx"])
def test_a_model_name_cannot_change_the_web_address_it_goes_into(model):
    s = load({"GEMINI_MODEL": model})
    if model == "":
        assert s.gemini_model == DEFAULT_GEMINI_MODEL           # empty means "not set"
    else:
        assert s.gemini_model == DEFAULT_GEMINI_MODEL and any("GEMINI_MODEL" in w for w in s.warnings)


def test_a_bad_model_name_in_config_yaml_is_replaced():
    s = load(cfg={"brain": {"gemini_model": "bad/name"}})
    assert s.gemini_model == DEFAULT_GEMINI_MODEL and any("config.yaml" in w for w in s.warnings)


@pytest.mark.parametrize("name", ["qwen2.5:3b", "llama3.1:8b", "library/qwen2.5-coder:7b", "mistral"])
def test_local_model_names(name):
    assert load({"LOCAL_MODEL_NAME": name}).local_model_name == name


@pytest.mark.parametrize("name", ["bad name", "x;rm -rf", "", "a" * 150, "-start"])
def test_a_bad_local_model_name_is_ignored(name):
    s = load({"LOCAL_MODEL_NAME": name})
    assert s.local_model_name == ""
    assert (name == "") or any("LOCAL_MODEL_NAME" in w for w in s.warnings)


def test_speech_engine_choices():
    assert load({"STT_ENGINE": "MacOS"}).stt_engine == "macos" and load({"TTS_ENGINE": " Say "}).tts_engine == "say"
    bad = load({"STT_ENGINE": "cloud", "TTS_ENGINE": "robot"})
    assert (bad.stt_engine, bad.tts_engine) == ("whisper", "piper") and len(bad.warnings) == 2
    google = load({"TTS_ENGINE": "google"})
    assert google.tts_engine == "piper" and any("isn't built yet" in w for w in google.warnings)


def test_config_yaml_provides_defaults_for_the_speech_engines():
    s = load(cfg={"voice": {"stt_engine": "macos", "tts_engine": "say"}})
    assert (s.stt_engine, s.tts_engine) == ("macos", "say")


def test_applying_settings_changes_a_copy_not_the_original():
    cfg = {"ollama": {"model": "qwen2.5:3b", "url": "u"}, "voice": {"enabled": True}, "safety": {"dry_run": False}}
    s = load({"LOCAL_MODEL_NAME": "llama3.1:8b", "SAFE_MODE": "true", "STT_ENGINE": "macos", "TTS_ENGINE": "say"})
    out = apply_to_config(cfg, s)
    assert out["ollama"] == {"model": "llama3.1:8b", "url": "u"} and out["safety"] == {"dry_run": False, "safe_mode": True}
    assert out["voice"] == {"enabled": True, "stt_engine": "macos", "tts_engine": "say"}
    assert cfg == {"ollama": {"model": "qwen2.5:3b", "url": "u"}, "voice": {"enabled": True}, "safety": {"dry_run": False}}
    assert apply_to_config({}, load())["voice"]["tts_engine"] == "piper"                  # a bare config still works


def test_the_real_environment_is_read_by_default(monkeypatch):
    monkeypatch.setenv("SAFE_MODE", "true")
    assert load_settings({}).safe_mode is True


def test_the_tests_never_see_the_developers_own_env_file(tmp_path):
    assert not os.path.exists(os.environ[ENV_FILE_VAR])
    assert load_settings({}).gemini_api_key == ""


def test_the_example_file_matches_the_names_the_code_reads(tmp_path):
    from backend.settings import KNOWN, ROOT
    values, _ = read_env_file(ROOT / ".env.example")
    assert set(values) == set(KNOWN) and values["USE_GEMINI"] == "false" and values["GEMINI_API_KEY"] == ""
    s = load_settings({}, {}, ROOT / ".env.example")
    assert not s.use_gemini and s.warnings == ()


def test_dotenv_is_ignored_by_git():
    from backend.settings import ROOT
    text = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in text and "!.env.example" in text
