"""
Tool calling over plain text, for providers without native function calling
(the agy / claude / codex CLIs). The model replies with exactly one JSON object:

    {"tool": "<name>", "args": {...}}     -> call a tool
    {"final": "<answer>"}                 -> done

Plain text without any JSON is accepted as a final answer.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

from .base import Message, Provider, Reply, ToolCall, ToolSpec

PROTOCOL = """## How to reply
You act ONLY through the tools listed below; you have no other tools, files or shell.
Every reply must be exactly ONE JSON object in a ```json code block, nothing else:

To call a tool (one per reply, you will get the result in the next message):
```json
{"tool": "<tool name>", "args": {<arguments>}}
```

When you are finished, give your final result:
```json
{"final": "<your complete final answer, markdown allowed>"}
```
Remember: JSON strings must escape newlines as \\n and quotes as \\".

## Available tools
"""

_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.S)
OMITTED = "[… earlier messages omitted to save space …]"


def tool_instructions(tools: list[ToolSpec]) -> str:
    if not tools:
        return "## How to reply\nReply with your final answer as plain text."
    lines = [PROTOCOL]
    for t in tools:
        lines.append(f"- `{t.name}`: {t.description}\n  args schema: {json.dumps(t.parameters, ensure_ascii=False)}")
    return "\n".join(lines)


@dataclass
class Parsed:
    final: str | None = None
    call: ToolCall | None = None
    error: str | None = None


def _candidates(text: str):
    for m in _FENCE.finditer(text):
        yield m.group(1).strip()
    dec = json.JSONDecoder(strict=False)
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text[i:])
                yield obj
            except json.JSONDecodeError:
                continue


def parse_reply(text: str) -> Parsed:
    for cand in _candidates(text):
        obj = cand
        if isinstance(cand, str):
            try:
                obj = json.loads(cand, strict=False)
            except json.JSONDecodeError:
                continue
        if not isinstance(obj, dict):
            continue
        if "tool" in obj:
            args = obj.get("args") or {}
            if not isinstance(args, dict):
                return Parsed(error="`args` must be a JSON object.")
            return Parsed(call=ToolCall(id=uuid.uuid4().hex[:8], name=str(obj["tool"]), args=args))
        if "final" in obj:
            final = obj["final"]
            return Parsed(final=final if isinstance(final, str) else json.dumps(final, indent=2, ensure_ascii=False))
    if '"tool"' in text:
        return Parsed(error="Your tool call was not valid JSON.")
    return Parsed(final=text.strip())


def render_transcript(messages: list[Message], budget: int) -> str:
    parts = []
    for m in messages:
        if m.role == "user":
            parts.append(f"[USER]:\n{m.content}")
        elif m.role == "assistant":
            if m.tool_calls:
                tc = m.tool_calls[0]
                parts.append("[YOU]:\n```json\n" + json.dumps({"tool": tc.name, "args": tc.args}, ensure_ascii=False) + "\n```")
            else:
                parts.append(f"[YOU]:\n{m.content}")
        elif m.role == "tool":
            parts.append(f"[TOOL RESULT `{m.name}`]:\n{m.content}")
    # keep the first message (the task) and drop the oldest others until it fits
    head, rest, omitted = parts[:1], parts[1:], False
    while len(rest) > 1 and sum(map(len, head + rest)) > budget:
        rest.pop(0)
        omitted = True
    if omitted:
        rest.insert(0, OMITTED)
    return "## Conversation so far\n\n" + "\n\n".join(head + rest) + "\n\n## Your next reply (one JSON object):"


class TextToolProvider(Provider):
    """Base for providers that only do text in -> text out."""

    char_budget = 100_000

    async def complete_text(self, system: str, prompt: str) -> str:
        raise NotImplementedError

    async def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> Reply:
        full_system = system + "\n\n" + tool_instructions(tools)
        prompt = render_transcript(messages, self.char_budget)
        out = ""
        for _ in range(3):
            out = await self.complete_text(full_system, prompt)
            p = parse_reply(out)
            if p.error:
                prompt += f"\n\n[YOU]:\n{out}\n\n[SYSTEM]: {p.error} Reply again with exactly one valid JSON object."
                continue
            if p.call:
                if tools and p.call.name not in {t.name for t in tools}:
                    prompt += (f"\n\n[YOU]:\n{out}\n\n[SYSTEM]: Unknown tool `{p.call.name}`. "
                               f"Available: {', '.join(t.name for t in tools)}.")
                    continue
                return Reply(text="", tool_calls=[p.call])
            return Reply(text=p.final or "")
        return Reply(text=out.strip())
