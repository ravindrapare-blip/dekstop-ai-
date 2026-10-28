"""Startup checks: is Ollama installed and running, and is the configured model pulled?"""
import os
import shutil
from dataclasses import dataclass

from backend.llm import OllamaClient, OllamaError

BINARY_CANDIDATES = (
    "/Applications/Ollama.app/Contents/Resources/ollama",
    "/usr/local/bin/ollama",
    "/opt/homebrew/bin/ollama",
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    fix: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "fix": self.fix}


def find_ollama_binary(which=shutil.which, candidates=BINARY_CANDIDATES) -> str | None:
    found = which("ollama")
    if found:
        return found
    return next((c for c in candidates if os.access(c, os.X_OK)), None)


def _with_tag(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def model_installed(model: str, installed: list[str]) -> bool:
    return _with_tag(model) in {_with_tag(n) for n in installed}


def run_checks(client: OllamaClient, which=shutil.which, candidates=BINARY_CANDIDATES) -> list[CheckResult]:
    results: list[CheckResult] = []
    binary = find_ollama_binary(which, candidates)

    server_ok = True
    try:
        version = client.version()
        server = CheckResult("ollama server", True, f"running, version {version}")
    except OllamaError as e:
        server_ok = False
        server = CheckResult(
            "ollama server", False, str(e),
            "Start it with `ollama serve` (or open the Ollama app), then check again.",
        )

    if binary:
        results.append(CheckResult("ollama installed", True, binary))
    elif server_ok:
        results.append(CheckResult("ollama installed", True, "server is reachable (binary not on PATH)"))
    else:
        results.append(CheckResult(
            "ollama installed", False, "ollama not found",
            "Install Ollama from https://ollama.com. On macOS 12 the newest release may not run; "
            "see docs/install.md for older releases.",
        ))
    results.append(server)

    if not server_ok:
        results.append(CheckResult("model", False, f"cannot check {client.model!r}: server not running",
                                   "Fix the server first."))
        return results
    try:
        installed = client.list_models()
    except OllamaError as e:
        results.append(CheckResult("model", False, str(e)))
        return results
    if model_installed(client.model, installed):
        results.append(CheckResult("model", True, f"{client.model} is available"))
    else:
        have = ", ".join(installed) if installed else "none"
        results.append(CheckResult(
            "model", False, f"{client.model} is not pulled (installed: {have})",
            f"Run `ollama pull {client.model}`, or change ollama.model in config.yaml.",
        ))
    return results


def all_ok(results: list[CheckResult]) -> bool:
    return all(r.ok for r in results)
