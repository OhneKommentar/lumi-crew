"""Events from agents to the UI, and the interface every UI implements."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Event:
    kind: str           # agent_start | agent_end | text | tool_call | tool_result | info | warn | error
    agent: str          # agent instance name, e.g. "coder" or "coder#2" when several run in parallel
    text: str = ""
    data: dict = field(default_factory=dict)


class UI:
    """Implemented by the Textual app and by the headless console runner."""

    def emit(self, event: Event) -> None:
        pass

    async def ask_user(self, agent: str, question: str, options: list[str] | None = None) -> str:
        """Ask a question; `options` are suggested answers, the user may also write their own."""
        raise NotImplementedError

    async def approve(self, agent: str, title: str, preview: str) -> str:
        """Return "yes", "no" or "always"."""
        raise NotImplementedError

    async def choose_fallback(self, agent: str, role: str, current: str, error: str) -> str | None:
        """The model of `agent` hit a usage limit: return a new "provider:model" spec, or None to give up."""
        return None
