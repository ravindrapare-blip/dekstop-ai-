"""Backend entry point: health, status checks, tool list and the /route endpoint."""
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.config import ROOT, load_config
from backend.llm import ModelNotFound, OllamaClient, OllamaError, OllamaUnavailable, client_from_config
from backend.router import MAX_INPUT_CHARS, Router
from backend.startup import all_ok, run_checks
from backend.tool_defs import build_registry
from safety import AuditLog, GuardrailManager, Policy
from voice import VoiceError, VoiceService, voice_service_from_config
from voice.audio import MAX_AUDIO_BYTES

PHASE = 4

# How each speech problem is reported over HTTP.
VOICE_STATUS = {
    "disabled": 503, "engine_missing": 503, "model_missing": 503,
    "failed": 502, "timeout": 502, "bad_audio": 400, "bad_text": 400, "too_large": 413,
}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

log = logging.getLogger("assistant.backend")


class RouteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    dry_run: bool | None = None  # None uses safety.dry_run from config.yaml


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ConfirmRequest(BaseModel):
    confirmation_id: str = Field(min_length=1, max_length=64)
    approved: bool
    acknowledge_risk: bool = False  # required to approve a high-risk action


def check_host(host: str) -> str:
    """The backend must only listen on the loopback interface."""
    if host not in LOOPBACK_HOSTS:
        raise SystemExit(
            f"Refusing to listen on {host!r}: backend.host must be 127.0.0.1 in config.yaml"
        )
    return host


def build_guard(cfg: dict) -> GuardrailManager:
    s = cfg.get("safety", {})
    policy = Policy.load(ROOT / s.get("policy_file", "safety/policy.yaml"))
    return GuardrailManager(
        policy,
        AuditLog(ROOT / s.get("audit_file", "data/logs/audit.jsonl")),
        timeout_seconds=s.get("approval_timeout_seconds", 60),
        max_pending=s.get("max_pending", 5),
        dry_run=s.get("dry_run", False),
    )


def voice_error(error: VoiceError) -> JSONResponse:
    return JSONResponse(
        status_code=VOICE_STATUS.get(error.code, 500),
        content={"detail": error.detail, "code": error.code},
    )


def create_app(
    cfg: dict | None = None,
    llm: OllamaClient | None = None,
    guard: GuardrailManager | None = None,
    voice: VoiceService | None = None,
) -> FastAPI:
    cfg = cfg or load_config()
    llm = llm or client_from_config(cfg)
    guard = guard or build_guard(cfg)
    voice = voice or voice_service_from_config(cfg, ROOT)
    registry = build_registry()
    unruled = sorted(t.name for t in registry.all() if t.name not in guard.policy.rules)
    if unruled:
        log.warning("tools with no safety rule can never run: %s", ", ".join(unruled))
    router = Router(llm, registry, max_retries=cfg.get("router", {}).get("max_retries", 1))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            for r in run_checks(llm):
                (log.info if r.ok else log.warning)("startup check %s: %s %s", r.name, r.detail, r.fix)
        except Exception:  # checks must never stop the backend from starting
            log.exception("startup checks failed unexpectedly")
        yield
        llm.close()

    app = FastAPI(title="local-assistant", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "phase": PHASE}

    @app.get("/status")
    def status() -> dict:
        results = run_checks(llm)
        return {"ready": all_ok(results), "model": llm.model, "checks": [r.to_dict() for r in results]}

    @app.get("/tools")
    def tools() -> list[dict]:
        return [t.to_dict() for t in registry.all()]

    @app.post("/route")
    def route(req: RouteRequest) -> dict:
        try:
            result = router.route(req.text)
        except ModelNotFound as e:
            raise HTTPException(503, f"model not available: {e}") from e
        except OllamaUnavailable as e:
            raise HTTPException(503, str(e)) from e
        except OllamaError as e:
            raise HTTPException(502, str(e)) from e
        decision = guard.evaluate(result.call, dry_run=req.dry_run)
        return {
            **result.call.to_dict(),
            "fallback": result.fallback,
            "note": result.note,
            "attempts": result.attempts,
            "guard": decision.to_dict(),
        }

    @app.post("/confirm")
    def confirm(req: ConfirmRequest) -> dict:
        return guard.confirm(
            req.confirmation_id, req.approved, acknowledge_risk=req.acknowledge_risk
        ).to_dict()

    # Speech. The app records the microphone (it owns the permission) and plays the replies;
    # these endpoints only convert. A transcript still goes through /route and the safety layer.
    @app.get("/voice/status")
    def voice_status() -> dict:
        return voice.status()

    @app.post("/voice/transcribe", response_model=None)
    async def voice_transcribe(request: Request):
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_AUDIO_BYTES:
            return voice_error(VoiceError("too_large", "That recording is too large."))
        body = await request.body()
        try:
            text = await run_in_threadpool(voice.transcribe, body)
        except VoiceError as e:
            return voice_error(e)
        return {"text": text}

    @app.post("/voice/speak", response_model=None)
    async def voice_speak(req: SpeakRequest):
        try:
            wav = await run_in_threadpool(voice.speak, req.text)
        except VoiceError as e:
            return voice_error(e)
        return Response(content=wav, media_type="audio/wav")

    return app


app = create_app()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()["backend"]
    uvicorn.run(app, host=check_host(cfg["host"]), port=cfg["port"], log_level="info")


if __name__ == "__main__":
    main()
