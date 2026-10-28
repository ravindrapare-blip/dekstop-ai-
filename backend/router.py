"""Turn a user request into one validated tool call. Never executes anything."""
import logging
from dataclasses import dataclass
from datetime import datetime

from backend.llm import ChatResult, OllamaClient
from backend.prompt import build_messages
from backend.tool_defs import REPLY
from backend.toolcall import ToolCall, ToolCallError, parse_tool_call
from backend.tools import ToolRegistry

log = logging.getLogger("assistant.router")

MAX_INPUT_CHARS = 1000
RAW_ECHO_CHARS = 500


@dataclass
class RouteResult:
    call: ToolCall
    fallback: bool = False       # True when the model's output could not be used
    attempts: int = 0            # model calls made
    note: str = ""
    stats: ChatResult | None = None


def _reply(text: str, note: str, attempts: int = 0) -> RouteResult:
    return RouteResult(ToolCall(REPLY, {"text": text}), fallback=True, attempts=attempts, note=note)


class Router:
    def __init__(self, llm: OllamaClient, registry: ToolRegistry, max_retries: int = 1, clock=datetime.now):
        self.llm = llm
        self.registry = registry
        self.max_retries = max(0, max_retries)
        self.clock = clock

    def route(self, user_text: str) -> RouteResult:
        """Raises OllamaError subclasses if the model cannot be reached."""
        text = (user_text or "").strip()
        if not text:
            return _reply("Please type or say what you need.", "empty input")
        if len(text) > MAX_INPUT_CHARS:
            return _reply("That message is too long. Please shorten it.", "input too long")

        messages = build_messages(self.registry, text, self.clock())
        reason = ""
        for attempt in range(1, self.max_retries + 2):
            result = self.llm.chat(messages)
            try:
                call = parse_tool_call(result.content, self.registry)
            except ToolCallError as e:
                reason = str(e)
                log.warning("invalid model output (attempt %d): %s", attempt, reason)
                messages = messages + [
                    {"role": "assistant", "content": result.content[:RAW_ECHO_CHARS]},
                    {"role": "user", "content": f"Invalid: {reason}. Reply with one valid JSON object only."},
                ]
                continue
            log.info("routed to %s (attempt %d, confirm=%s)", call.tool, attempt, call.requires_confirmation)
            return RouteResult(call, attempts=attempt, stats=result)

        log.warning("falling back to reply after %d attempts: %s", self.max_retries + 1, reason)
        return _reply(
            "I didn't understand that well enough to act. Could you rephrase it?",
            f"invalid model output: {reason}",
            attempts=self.max_retries + 1,
        )
