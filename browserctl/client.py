"""Client for the Node browser worker (browser/src/server.js). Localhost only, shared token on every request."""
from pathlib import Path
from urllib.parse import urlparse

import httpx

from automation.token import read_token

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class WorkerUnavailable(Exception):
    """The worker is not running, not set up, or refused the request. The message says what to do."""


class WorkerError(Exception):
    """The worker ran but could not do what was asked. `code` says why; the message is fit to show the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WorkerClient:
    def __init__(self, url: str, token_file: Path, *, timeout: float = 75.0, transport=None) -> None:
        if urlparse(url).hostname not in LOOPBACK_HOSTS:
            raise ValueError(f"browser URL must be a local address, got {url!r}")
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.timeout = timeout
        self._transport = transport

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        token = read_token(self.token_file)
        if token is None:
            raise WorkerUnavailable("The shared token file is missing, so I can't talk to the browser helper.")
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as http:
                return http.request(method, self.url + path, headers={"X-Assistant-Token": token}, **kwargs)
        except httpx.TimeoutException:
            raise WorkerError("timeout", "The browser took too long to answer.") from None
        except httpx.HTTPError:
            raise WorkerUnavailable("The browser helper isn't running.") from None

    def ping(self) -> bool:
        try:
            resp = self._send("GET", "/ping")
        except (WorkerUnavailable, WorkerError):
            return False
        return resp.status_code == 200

    def call(self, action: str, **args) -> dict:
        resp = self._send("POST", "/action", json={"action": action, "args": args})
        if resp.status_code in (401, 503):
            raise WorkerUnavailable("The browser helper rejected the token. Stop it and try again.")
        try:
            data = resp.json()
        except ValueError:
            raise WorkerError("failed", "The browser helper sent an unreadable answer.") from None
        if not isinstance(data, dict):
            raise WorkerError("failed", "The browser helper sent an unreadable answer.")
        if resp.status_code != 200 or not data.get("ok"):
            raise WorkerError(str(data.get("code", "failed")), str(data.get("error", "The browser action failed.")))
        return data
