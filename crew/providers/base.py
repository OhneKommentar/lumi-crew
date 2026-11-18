"""Common data types and the Provider interface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Message:
    role: str                      # "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None        # tool name for role == "tool"
    raw: Any = None                # provider-native assistant content (thinking blocks, signatures …)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict               # JSON schema


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None


class ProviderError(Exception):
    pass


class Provider:
    """One LLM backend. `complete` gets the whole conversation and returns the next assistant turn."""

    kind = "base"

    def __init__(self, name: str, model: str | None = None, **options):
        self.name = name
        self.model = model
        self.options = options

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model or 'default'}"

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> Reply:
        raise NotImplementedError
