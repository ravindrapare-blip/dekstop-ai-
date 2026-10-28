"""Backend entry point. Phase 0: health endpoint only."""
import uvicorn
from fastapi import FastAPI

from backend.config import load_config

PHASE = 0

app = FastAPI(title="local-assistant", docs_url=None, redoc_url=None)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "phase": PHASE}


def main() -> None:
    cfg = load_config()["backend"]
    uvicorn.run(app, host=cfg["host"], port=cfg["port"], log_level="info")


if __name__ == "__main__":
    main()
