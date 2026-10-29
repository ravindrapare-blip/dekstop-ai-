"""Minimal Ollama HTTP client. Local only: the URL must be a loopback address."""
from dataclasses import dataclass
from urllib.parse import urlparse

import time

import httpx

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class OllamaError(Exception):
    """Ollama answered, but not usefully (bad status, bad body, timeout)."""

    transient = False       # True when trying again a moment later may well work


class OllamaUnavailable(OllamaError):
    """Ollama cannot be reached (it may be starting up)."""

    transient = True


class OllamaServerError(OllamaError):
    """Ollama had a hiccup of its own (a 5xx answer, or the connection dropped mid-answer)."""

    transient = True


class ModelNotFound(OllamaError):
    """The configured model has not been pulled."""


@dataclass
class ChatResult:
    content: str
    eval_count: int = 0
    eval_duration_ns: int = 0
    total_duration_ns: int = 0
    engine: str = "ollama"        # which brain answered: "ollama" or "gemini"
    notice: str = ""              # something the user should hear once (for example: "switched to the local model")
    speak_notice: bool = False

    @property
    def tokens_per_second(self) -> float:
        return self.eval_count / (self.eval_duration_ns / 1e9) if self.eval_duration_ns else 0.0


class OllamaClient:
    def __init__(
        self,
        url: str,
        model: str,
        *,
        timeout: float = 120.0,
        keep_alive: str = "5m",
        options: dict | None = None,
        transport: httpx.BaseTransport | None = None,
        retries: int = 1,
        retry_delay: float = 0.5,
        sleep=time.sleep,
    ) -> None:
        if urlparse(url).hostname not in LOOPBACK_HOSTS:
            raise ValueError(f"ollama.url must be a local address, got {url!r}")
        self.model = model
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.options = options or {}
        self.retries = max(0, retries)
        self.retry_delay = retry_delay
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=url, timeout=httpx.Timeout(timeout, connect=3.0), transport=transport
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = self._http.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise OllamaUnavailable(f"cannot connect to Ollama at {self._http.base_url}") from e
        except httpx.TimeoutException as e:
            raise OllamaError(f"Ollama timed out after {self.timeout:.0f}s") from e
        except httpx.HTTPError as e:
            raise OllamaServerError(f"Ollama request failed: {e}") from e

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", resp.text)
            except ValueError:
                detail = resp.text
            if resp.status_code == 404 and "not found" in str(detail).lower():
                raise ModelNotFound(str(detail))
            raise (OllamaServerError if resp.status_code >= 500 else OllamaError)(f"Ollama returned {resp.status_code}: {detail}")
        try:
            data = resp.json()
        except ValueError as e:
            raise OllamaError("Ollama returned a non-JSON response") from e
        if not isinstance(data, dict):
            raise OllamaError("Ollama returned an unexpected response")
        return data

    def _with_retries(self, call):
        """Tries again, after a short pause, only for hiccups that usually pass (Ollama still starting, a 5xx).
        A timeout is not retried: it would only make the wait twice as long."""
        for attempt in range(self.retries + 1):
            try:
                return call()
            except OllamaError as e:
                if not e.transient or attempt >= self.retries:
                    raise
                self._sleep(self.retry_delay * (attempt + 1))

    def version(self) -> str:
        return str(self._request("GET", "/api/version").get("version", "unknown"))

    def list_models(self) -> list[str]:
        models = self._request("GET", "/api/tags").get("models", [])
        return [m["name"] for m in models if isinstance(m, dict) and "name" in m]

    def chat(self, messages: list[dict], *, json_mode: bool = True) -> ChatResult:
        # format="json" works on every Ollama release, including older ones that may be
        # needed on Monterey. The reply is still validated by backend.toolcall.
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": self.options,
        }
        if json_mode:
            payload["format"] = "json"
        data = self._with_retries(lambda: self._request("POST", "/api/chat", json=payload))
        message = data.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise OllamaError("Ollama reply has no message content")
        return ChatResult(
            content=message["content"],
            eval_count=int(data.get("eval_count") or 0),
            eval_duration_ns=int(data.get("eval_duration") or 0),
            total_duration_ns=int(data.get("total_duration") or 0),
        )


def client_from_config(cfg: dict) -> OllamaClient:
    o = cfg["ollama"]
    return OllamaClient(
        o["url"],
        o["model"],
        timeout=o.get("timeout_seconds", 120),
        keep_alive=o["keep_alive"],
        retries=o.get("retries", 1),
        options={
            "temperature": 0,
            "num_ctx": o["num_ctx"],
            "num_predict": o.get("num_predict", 256),
        },
    )
