"""Build the prompt: rules, compact tool list, a few examples, then the user's request."""
import json
from datetime import datetime

from backend.toolcall import ToolCall
from backend.tools import ToolRegistry

SYSTEM = """You control a local desktop assistant. Reply with ONE JSON object and nothing else:
{{"tool": "<tool name>", "args": {{...}}, "requires_confirmation": false}}

Rules:
- Choose exactly one tool from the list. Never invent tools or arguments.
- If the request is unclear, no tool fits, or the user is just chatting, use the "reply" tool.
- There are no tools to delete, rename or move files, change settings, or log in to websites. Use "reply" to explain.
- Set requires_confirmation to true when the action changes or closes something.
- For websites use the browser tools. Never type passwords or card numbers.

Current local time: {now}

Tools:
{tools}

Examples:
{examples}"""


def _examples(now: datetime) -> list[tuple[str, ToolCall]]:
    today = now.strftime("%Y-%m-%d")
    return [
        ("open chrome", ToolCall("open_app", {"app_name": "Google Chrome"})),
        ("remind me at 6 pm to call mom",
         ToolCall("create_reminder", {"text": "call mom", "due_datetime": f"{today}T18:00:00"})),
        ("open youtube and play lofi music", ToolCall("play_youtube_video", {"query": "lofi music"})),
        ("delete my old photos",
         ToolCall("reply", {"text": "I can't delete files. I can open the folder for you instead."})),
    ]


def build_messages(registry: ToolRegistry, user_text: str, now: datetime) -> list[dict]:
    examples = "\n".join(
        f"User: {text}\n{json.dumps(call.to_dict())}" for text, call in _examples(now)
    )
    system = SYSTEM.format(
        now=now.strftime("%Y-%m-%d %H:%M (%A)"), tools=registry.describe(), examples=examples
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]
