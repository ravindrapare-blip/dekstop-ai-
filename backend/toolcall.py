"""Parse and validate the model's JSON output. Only registered tools with valid args get through."""
import json
import re
from dataclasses import dataclass, field

from backend.tools import Param, ToolRegistry, ToolSpec

MAX_STR_LEN = 2000
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class ToolCallError(ValueError):
    """The model output cannot be used as a tool call."""


@dataclass
class ToolCall:
    tool: str
    args: dict = field(default_factory=dict)
    requires_confirmation: bool = False

    def to_dict(self) -> dict:
        return {"tool": self.tool, "args": self.args, "requires_confirmation": self.requires_confirmation}


def _load_json(raw: str) -> object:
    text = _FENCE.sub("", raw.strip()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ToolCallError("output is not valid JSON")


def _coerce(spec: ToolSpec, param: Param, value: object) -> object:
    where = f"{spec.name}.{param.name}"
    if param.type == "str":
        if not isinstance(value, str):
            raise ToolCallError(f"{where} must be a string")
        value = value.strip()
        if param.required and not value:
            raise ToolCallError(f"{where} must not be empty")
        if len(value) > MAX_STR_LEN:
            raise ToolCallError(f"{where} is too long")
        return value
    if param.type == "ref":
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ToolCallError(f"{where} must be a number or some words")
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ToolCallError(f"{where} must not be empty")
            if len(value) > MAX_STR_LEN:
                raise ToolCallError(f"{where} is too long")
        return value
    if param.type == "int":
        if isinstance(value, bool):
            raise ToolCallError(f"{where} must be an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            return int(value.strip())
        raise ToolCallError(f"{where} must be an integer")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ToolCallError(f"{where} must be true or false")


def _clean_args(spec: ToolSpec, args: dict) -> dict:
    known = {p.name for p in spec.params}
    extra = sorted(set(args) - known)
    if extra:
        raise ToolCallError(f"{spec.name} has no argument {extra[0]!r}")
    clean = {}
    for p in spec.params:
        value = args.get(p.name)
        if value is None:
            if p.required:
                raise ToolCallError(f"{spec.name} is missing argument {p.name!r}")
            continue
        clean[p.name] = _coerce(spec, p, value)
    return clean


def parse_tool_call(raw: str, registry: ToolRegistry) -> ToolCall:
    obj = _load_json(raw)
    if not isinstance(obj, dict):
        raise ToolCallError("JSON must be an object")
    name = obj.get("tool")
    if not isinstance(name, str):
        raise ToolCallError("missing 'tool'")
    spec = registry.get(name.strip())
    if spec is None:
        raise ToolCallError(f"unknown tool {name.strip()!r}")
    args = obj.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolCallError("'args' must be an object")

    # The model may ask for confirmation but can never waive what the tool requires.
    flag = obj.get("requires_confirmation", False)
    model_flag = flag if isinstance(flag, bool) else True
    return ToolCall(
        tool=spec.name,
        args=_clean_args(spec, args),
        requires_confirmation=spec.requires_confirmation or model_flag,
    )
