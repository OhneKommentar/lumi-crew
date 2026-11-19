"""Tool definition shared by all tools."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..providers.base import ToolSpec

if TYPE_CHECKING:
    from ..session import Session

MAX_RESULT_CHARS = 12_000


@dataclass
class ToolContext:
    session: "Session"
    agent: str                                  # name of the agent calling the tool

    @property
    def workspace(self) -> Path:
        return self.session.workspace


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                                            # JSON schema
    kind: str                                                   # read | write | shell | mcp | meta
    fn: Callable[[dict, ToolContext], Awaitable[str]]
    preview: Callable[[dict, ToolContext], str] | None = None   # shown in the approval dialog

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [],
            "additionalProperties": False}


def truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n\n[… {len(text) - limit} characters truncated …]\n\n{text[-half:]}"
