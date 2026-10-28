"""Backend entry point: health, status checks, tool list and the /route endpoint."""
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.config import load_config
from backend.llm import ModelNotFound, OllamaClient, OllamaError, OllamaUnavailable, client_from_config
from backend.router import MAX_INPUT_CHARS, Router
from backend.startup import all_ok, run_checks
from backend.tool_defs import build_registry

PHASE = 1
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

log = logging.getLogger("assistant.backend")


class RouteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)


def check_host(host: str) -> str:
    """The backend must only listen on the loopback interface."""
    if host not in LOOPBACK_HOSTS:
        raise SystemExit(
            f"Refusing to listen on {host!r}: backend.host must be 127.0.0.1 in config.yaml"
        )
    return host


def create_app(cfg: dict | None = None, llm: OllamaClient | None = None) -> FastAPI:
    cfg = cfg or load_config()
    llm = llm or client_from_config(cfg)
    registry = build_registry()
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
        return {
            **result.call.to_dict(),
            "fallback": result.fallback,
            "note": result.note,
            "attempts": result.attempts,
        }

    return app


app = create_app()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()["backend"]
    uvicorn.run(app, host=check_host(cfg["host"]), port=cfg["port"], log_level="info")


if __name__ == "__main__":
    main()
