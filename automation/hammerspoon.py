"""Client for the small server that automation/hammerspoon/init.lua runs inside Hammerspoon.

Only named actions are sent (open_app, focus_app, quit_app, ping); nothing is ever evaluated as code.
Requests carry the shared token and go to localhost only.
"""
from pathlib import Path
from urllib.parse import urlparse

import httpx

from automation.token import read_token

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class HammerspoonUnavailable(Exception):
    """Hammerspoon is not running, not set up, or refused the request."""


class HammerspoonClient:
    def __init__(self, url: str, token_file: Path, *, timeout: float = 4.0, transport=None) -> None:
        if urlparse(url).hostname not in LOOPBACK_HOSTS:
            raise ValueError(f"automation URL must be a local address, got {url!r}")
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.timeout = timeout
        self._transport = transport

    def _request(self, method: str, path: str, **kwargs) -> dict:
        token = read_token(self.token_file)
        if token is None:
            raise HammerspoonUnavailable("the shared token file is missing")
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as http:
                resp = http.request(method, self.url + path, headers={"X-Assistant-Token": token}, **kwargs)
        except httpx.HTTPError as e:
            raise HammerspoonUnavailable(f"Hammerspoon did not answer ({type(e).__name__})") from None
        if resp.status_code in (401, 403):
            raise HammerspoonUnavailable("Hammerspoon rejected the token; reload its config")
        try:
            data = resp.json()
        except ValueError:
            raise HammerspoonUnavailable("Hammerspoon sent an unreadable answer") from None
        if resp.status_code != 200 or not isinstance(data, dict) or not data.get("ok"):
            error = data.get("error", "unknown error") if isinstance(data, dict) else "unknown error"
            raise HammerspoonUnavailable(f"Hammerspoon could not do that: {error}")
        return data

    def available(self) -> bool:
        try:
            self._request("GET", "/ping")
        except HammerspoonUnavailable:
            return False
        return True

    def act(self, action: str, **args) -> dict:
        return self._request("POST", "/action", json={"action": action, "args": args})
