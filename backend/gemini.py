"""Google Gemini over plain HTTPS (httpx, no SDK, nothing extra to install).

This is the only code in the project that talks to the internet for the model. It is deliberately narrow:
  * it can only reach Google's Gemini host (the address is fixed here, not taken from settings)
  * the API key travels in a request header, never in the address, and is removed from any text that is logged or shown
  * what it sends is the prompt and the user's request; with `send_private_context` off (the default) the line of
    remembered preferences is removed first
  * every failure becomes a GeminiError with a kind, so backend/brain.py can decide what to do and what to tell the user

Gemini's answer is just text. It goes through the same tool validation and safety layer as the local model's.
"""
import json
import time

import httpx

from backend.llm import ChatResult
from backend.prompt import strip_private_context

HOST = "https://generativelanguage.googleapis.com"
API_VERSION = "v1beta"

# Kinds of failure. The first group means "Gemini is not usable for a while": switch to the local model and wait.
OUTAGE_KINDS = ("quota", "auth", "model", "timeout", "network", "server")
# The second group is about this one request only (blocked content, an unreadable answer): use the local model for it, no waiting.
REQUEST_KINDS = ("blocked", "bad_answer", "bad_request")

WHY = {
    "quota": "Gemini's quota or rate limit was reached",
    "auth": "Gemini rejected the API key or access (check GEMINI_API_KEY)",
    "model": "Gemini doesn't know the model name (check GEMINI_MODEL)",
    "timeout": "Gemini didn't answer in time",
    "network": "Gemini couldn't be reached (is the internet on?)",
    "server": "Gemini is overloaded or having trouble",
    "blocked": "Gemini declined that request",
    "bad_answer": "Gemini's answer was unreadable",
    "bad_request": "Gemini couldn't use that request",
}
MAX_RETRY_AFTER = 3600.0


class GeminiError(Exception):
    """Gemini could not answer. `kind` says why; `retry_after` is how long Google asked us to wait (seconds), if it did."""

    def __init__(self, kind: str, retry_after: float | None = None, status: str = "") -> None:
        super().__init__(WHY.get(kind, "Gemini failed"))
        self.kind = kind
        self.retry_after = retry_after
        self.status = status                       # Google's own status word, e.g. RESOURCE_EXHAUSTED (safe to log)

    @property
    def is_outage(self) -> bool:
        return self.kind in OUTAGE_KINDS

    @property
    def why(self) -> str:
        return WHY.get(self.kind, "Gemini failed")


def _delay_from_details(error: dict) -> float | None:
    """Google puts "retryDelay": "32s" in the error details of a rate-limit answer."""
    for detail in error.get("details") or ():
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                return None
    return None


def classify_http_error(status_code: int, body: dict | None, retry_after_header: str | None) -> GeminiError:
    error = (body or {}).get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    word = str(error.get("status", ""))[:40]
    message = str(error.get("message", "")).lower()
    delay = None
    if retry_after_header and retry_after_header.strip().isdigit():
        delay = float(retry_after_header.strip())
    delay = delay if delay is not None else _delay_from_details(error)
    if delay is not None:
        delay = min(max(delay, 0.0), MAX_RETRY_AFTER)

    if status_code == 429 or word == "RESOURCE_EXHAUSTED":
        return GeminiError("quota", delay, word)
    if status_code in (401, 403) or word in ("UNAUTHENTICATED", "PERMISSION_DENIED") or "api key" in message:
        return GeminiError("auth", None, word)
    if status_code == 404 or word == "NOT_FOUND":
        return GeminiError("model", None, word)
    if status_code == 408 or status_code == 504 or word == "DEADLINE_EXCEEDED":
        return GeminiError("timeout", delay, word)
    if status_code >= 500 or word in ("UNAVAILABLE", "INTERNAL"):
        return GeminiError("server", delay, word)
    return GeminiError("bad_request", None, word)


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 10.0,
        max_output_tokens: int = 256,
        send_private_context: bool = False,
        transport: httpx.BaseTransport | None = None,
        base_url: str = HOST,
        clock=time.monotonic,
    ) -> None:
        if not api_key:
            raise ValueError("a Gemini API key is required")
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.send_private_context = send_private_context
        self._key = api_key
        self._clock = clock
        # Only Google's own address is accepted (tests may hand in a stand-in server through `transport`).
        if transport is None and base_url != HOST:
            raise ValueError("the Gemini address can't be changed")
        self._http = httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(timeout, connect=min(4.0, timeout)), transport=transport,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        )

    def close(self) -> None:
        self._http.close()

    def redact(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    # -- the request ---------------------------------------------------------------------------------------------

    def _payload(self, messages: list[dict], json_mode: bool) -> dict:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        if not self.send_private_context:
            system = strip_private_context(system)
        contents = []
        for m in messages:
            if m["role"] == "system":
                continue
            role = "model" if m["role"] == "assistant" else "user"
            if contents and contents[-1]["role"] == role:                  # Gemini wants the roles to alternate
                contents[-1]["parts"][0]["text"] += "\n\n" + m["content"]
            else:
                contents.append({"role": role, "parts": [{"text": m["content"]}]})
        config: dict = {"temperature": 0, "maxOutputTokens": self.max_output_tokens}
        if json_mode:
            config["responseMimeType"] = "application/json"
        payload: dict = {"contents": contents, "generationConfig": config}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        return payload

    def chat(self, messages: list[dict], *, json_mode: bool = True) -> ChatResult:
        started = self._clock()
        try:
            resp = self._http.post(f"/{API_VERSION}/models/{self.model}:generateContent", json=self._payload(messages, json_mode))
        except httpx.TimeoutException:
            raise GeminiError("timeout") from None
        except httpx.HTTPError:                                        # connection refused, DNS, TLS, dropped connection
            raise GeminiError("network") from None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code >= 400:
            raise classify_http_error(resp.status_code, body, resp.headers.get("retry-after"))
        text, usage = self._read_answer(body)
        elapsed = self._clock() - started
        return ChatResult(
            content=text,
            eval_count=int(usage.get("candidatesTokenCount") or 0),
            eval_duration_ns=int(elapsed * 1e9),
            total_duration_ns=int(elapsed * 1e9),
            engine="gemini",
        )

    @staticmethod
    def _read_answer(body) -> tuple[str, dict]:
        if not isinstance(body, dict):
            raise GeminiError("bad_answer")
        usage = body.get("usageMetadata") if isinstance(body.get("usageMetadata"), dict) else {}
        feedback = body.get("promptFeedback") if isinstance(body.get("promptFeedback"), dict) else {}
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise GeminiError("blocked" if feedback.get("blockReason") else "bad_answer")
        candidate = candidates[0]
        content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}
        parts = content.get("parts") if isinstance(content.get("parts"), list) else []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)).strip()
        if not text:
            reason = str(candidate.get("finishReason", ""))
            raise GeminiError("blocked" if reason in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY") else "bad_answer")
        return text, usage
