"""Backend entry point. Phase 0: health endpoint only."""
import uvicorn
from fastapi import FastAPI

from backend.config import load_config

PHASE = 0
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

app = FastAPI(title="local-assistant", docs_url=None, redoc_url=None)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "phase": PHASE}


def check_host(host: str) -> str:
    """The backend must only listen on the loopback interface."""
    if host not in LOOPBACK_HOSTS:
        raise SystemExit(
            f"Refusing to listen on {host!r}: backend.host must be 127.0.0.1 in config.yaml"
        )
    return host


def main() -> None:
    cfg = load_config()["backend"]
    uvicorn.run(app, host=check_host(cfg["host"]), port=cfg["port"], log_level="info")


if __name__ == "__main__":
    main()
