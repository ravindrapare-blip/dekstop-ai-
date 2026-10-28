"""Builds the browser service from config.yaml."""
from pathlib import Path

from automation.choices import ChoiceStore
from browserctl.client import WorkerClient
from browserctl.launcher import WorkerLauncher
from browserctl.service import BrowserService


def build_browser(cfg: dict, root: Path, choices: ChoiceStore | None = None) -> BrowserService:
    b = cfg.get("browser", {})
    token_file = root / cfg.get("automation", {}).get("token_file", "data/.token")
    client = WorkerClient(f"http://{b.get('host', '127.0.0.1')}:{b.get('port', 8766)}", token_file)
    launcher = WorkerLauncher(
        client=client,
        node=b.get("node", "node"),
        script=root / "browser" / "src" / "server.js",
        port=b.get("port", 8766),
        token_file=token_file,
        chrome_path=b.get("chrome_path", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        profile_dir=root / b.get("profile_dir", "data/chrome-profile"),
        log_file=root / "data" / "logs" / "browser.log",
        idle_minutes=b.get("idle_minutes", 30),
    )
    return BrowserService(client, launcher, choices=choices, enabled=b.get("enabled", True))
