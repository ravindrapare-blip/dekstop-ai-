"""Settings that can come from the environment: which brain to use, the Gemini key, safe mode, speech engines.

Order of precedence (first one found wins):  real environment variable  >  the project's `.env` file  >  config.yaml  >  default.
The Gemini API key is read ONLY from the environment or `.env` (which git ignores), never from config.yaml, so it can't be
committed by accident. It is never logged, never shown in /status, and never put in a URL.

Variables: GEMINI_API_KEY, GEMINI_MODEL, USE_GEMINI, USE_LOCAL_FALLBACK, SAFE_MODE, LOCAL_MODEL_NAME, STT_ENGINE, TTS_ENGINE.
Bad values never stop the assistant from starting: they are ignored, the default is used, and a warning explains.
"""
import copy
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE_VAR = "ASSISTANT_ENV_FILE"          # lets tests (or you) point at a different .env file
KNOWN = ("GEMINI_API_KEY", "GEMINI_MODEL", "USE_GEMINI", "USE_LOCAL_FALLBACK", "SAFE_MODE", "LOCAL_MODEL_NAME", "STT_ENGINE", "TTS_ENGINE")

DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
STT_ENGINES = ("whisper", "macos")
TTS_ENGINES = ("piper", "say", "google")
_KEY = re.compile(r"^[A-Za-z0-9_-]{20,200}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")            # it goes into a web address, so only plain characters
_LOCAL_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = field(default="", repr=False)          # repr=False: it can't leak through a log line or a traceback
    gemini_model: str = DEFAULT_GEMINI_MODEL
    use_gemini: bool = False
    use_local_fallback: bool = True
    safe_mode: bool = False
    local_model_name: str = ""                                    # "" means: whatever ollama.model says in config.yaml
    stt_engine: str = "whisper"
    tts_engine: str = "piper"
    warnings: tuple = ()
    key_source: str = ""                                          # "environment", ".env" or "" (never the key itself)

    @property
    def gemini_configured(self) -> bool:
        return self.use_gemini and bool(self.gemini_api_key)


def read_env_file(path: Path) -> tuple[dict[str, str], list[str]]:
    """KEY=VALUE lines of a .env file (only the known names). Returns the values and any warnings."""
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, warnings
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.removeprefix("export ").partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name in KNOWN:
            values[name] = value
    if values.get("GEMINI_API_KEY"):
        try:
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                warnings.append(f"{path.name} holds your Gemini key but other users on this Mac can read it. Run: chmod 600 {path.name}")
        except OSError:
            pass
    return values, warnings


def _flag(raw: str | None, default: bool, name: str, warnings: list[str]) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    warnings.append(f"{name} should be true or false, not “{raw[:20]}”; using {str(default).lower()}.")
    return default


def load_settings(cfg: dict | None = None, environ: dict | None = None, env_file: Path | None = None) -> Settings:
    cfg = cfg or {}
    brain, voice, safety = cfg.get("brain", {}), cfg.get("voice", {}), cfg.get("safety", {})
    environ = os.environ if environ is None else environ
    path = env_file or Path(environ.get(ENV_FILE_VAR) or ROOT / ".env")
    file_values, warnings = read_env_file(path)

    def get(name: str) -> tuple[str | None, str]:
        if environ.get(name) not in (None, ""):
            return environ[name], "environment"
        if name in file_values and file_values[name] != "":
            return file_values[name], ".env"
        return None, ""

    key_raw, key_source = get("GEMINI_API_KEY")
    key = ""
    if key_raw is not None:
        if _KEY.match(key_raw.strip()):
            key = key_raw.strip()
        else:
            warnings.append("GEMINI_API_KEY doesn't look like a Gemini key (letters, digits, - and _ only); ignoring it.")
            key_source = ""

    model_raw, _ = get("GEMINI_MODEL")
    model = str(brain.get("gemini_model", DEFAULT_GEMINI_MODEL))
    if model_raw is not None:
        if _MODEL.match(model_raw.strip()):
            model = model_raw.strip()
        else:
            warnings.append(f"GEMINI_MODEL has characters that aren't allowed in a model name; using {model}.")
    if not _MODEL.match(model):
        warnings.append(f"gemini_model in config.yaml isn't a valid model name; using {DEFAULT_GEMINI_MODEL}.")
        model = DEFAULT_GEMINI_MODEL

    raw, _ = get("USE_GEMINI")
    use_gemini = _flag(raw, bool(brain.get("use_gemini", False)), "USE_GEMINI", warnings)
    raw, _ = get("USE_LOCAL_FALLBACK")
    fallback = _flag(raw, bool(brain.get("use_local_fallback", True)), "USE_LOCAL_FALLBACK", warnings)
    raw, _ = get("SAFE_MODE")
    safe = _flag(raw, bool(safety.get("safe_mode", False)), "SAFE_MODE", warnings)

    local_raw, _ = get("LOCAL_MODEL_NAME")
    local = ""
    if local_raw is not None:
        if _LOCAL_MODEL.match(local_raw.strip()):
            local = local_raw.strip()
        else:
            warnings.append("LOCAL_MODEL_NAME isn't a valid model name; using ollama.model from config.yaml.")

    stt_raw, _ = get("STT_ENGINE")
    stt = (stt_raw if stt_raw is not None else str(voice.get("stt_engine", "whisper"))).strip().lower()
    if stt not in STT_ENGINES:
        warnings.append(f"STT_ENGINE “{stt[:20]}” isn't known (choose whisper or macos); using whisper.")
        stt = "whisper"
    tts_raw, _ = get("TTS_ENGINE")
    tts = (tts_raw if tts_raw is not None else str(voice.get("tts_engine", "piper"))).strip().lower()
    if tts == "google":
        warnings.append("TTS_ENGINE=google isn't built yet; speaking with Piper instead.")
        tts = "piper"
    elif tts not in TTS_ENGINES:
        warnings.append(f"TTS_ENGINE “{tts[:20]}” isn't known (choose piper or say); using piper.")
        tts = "piper"

    if use_gemini and not key:
        warnings.append("USE_GEMINI is on but there is no GEMINI_API_KEY, so the local model is used. See README.md (Gemini setup).")

    return Settings(key, model, use_gemini, fallback, safe, local, stt, tts, tuple(warnings), key_source)


def apply_to_config(cfg: dict, settings: Settings) -> dict:
    """A copy of config.yaml's contents with the environment's choices applied (the original is left alone)."""
    out = copy.deepcopy(cfg)
    if settings.local_model_name and "ollama" in out:
        out["ollama"]["model"] = settings.local_model_name
    out.setdefault("voice", {}).update({"stt_engine": settings.stt_engine, "tts_engine": settings.tts_engine})
    out.setdefault("safety", {})["safe_mode"] = settings.safe_mode
    return out
