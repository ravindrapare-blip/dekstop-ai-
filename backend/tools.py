"""Tool metadata and the registry. Specs only: executing tools is done by later phases."""
from dataclasses import dataclass
from enum import Enum

PARAM_TYPES = ("str", "int", "bool")


class Category(str, Enum):
    APP = "app"
    FILE = "file"
    BROWSER = "browser"
    REMINDER = "reminder"
    NOTE = "note"
    SYSTEM = "system"


@dataclass(frozen=True)
class Param:
    name: str
    type: str = "str"
    required: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"param {self.name!r}: unknown type {self.type!r}")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: Category
    description: str
    params: tuple[Param, ...] = ()
    requires_confirmation: bool = False  # default; the model can only raise it, never lower it

    def signature(self) -> str:
        parts = []
        for p in self.params:
            label = p.name + ("" if p.required else "?")
            parts.append(label if p.type == "str" else f"{label}:{p.type}")
        return f"{self.name}({', '.join(parts)})"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category.value,
            "description": self.description,
            "params": [
                {"name": p.name, "type": p.type, "required": p.required, "description": p.description}
                for p in self.params
            ],
            "requires_confirmation": self.requires_confirmation,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if not spec.name.isidentifier():
            raise ValueError(f"invalid tool name {spec.name!r}")
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool {spec.name!r}")
        names = [p.name for p in spec.params]
        if len(set(names)) != len(names):
            raise ValueError(f"tool {spec.name!r} has duplicate param names")
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def by_category(self, category: Category) -> list[ToolSpec]:
        return [t for t in self._tools.values() if t.category is category]

    def describe(self) -> str:
        """Compact tool list for the prompt. One line per tool keeps small context windows free."""
        lines = []
        for t in self._tools.values():
            flag = " [confirm]" if t.requires_confirmation else ""
            lines.append(f"- {t.signature()}: {t.description}{flag}")
        return "\n".join(lines)
