"""The agent loop: ask the model, run its tool calls, repeat until it gives a final answer."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..events import Event
from ..permissions import PathError
from ..providers.base import Message, Provider, ProviderError
from ..tools.base import Tool, ToolContext, truncate
from .roles import Role

if TYPE_CHECKING:
    from ..session import Session

MODE_NOTES = {
    "manual": "The user approves every file change and shell command; a denied call means: adapt, don't retry.",
    "accept-edits": "File changes run automatically; shell commands need user approval.",
    "auto": "File changes and shell commands run automatically (dangerous commands are blocked).",
    "plan": ("PLAN MODE: everything is read-only. Writing files and running commands is refused. "
             "Only analyse, clarify and plan; the user will approve the plan and switch modes to implement it."),
}


def short_args(args: dict, limit: int = 80) -> str:
    parts = []
    for k, v in args.items():
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        s = s.replace("\n", "⏎")
        parts.append(f"{k}={s[:40] + '…' if len(s) > 40 else s}")
    text = ", ".join(parts)
    return text[:limit] + ("…" if len(text) > limit else "")


class Agent:
    def __init__(self, role: Role, provider: Provider, tools: list[Tool], session: "Session",
                 instance: str | None = None):
        self.role = role
        self.provider = provider
        self.tools = {t.name: t for t in tools}
        self.session = session
        self.name = instance or role.name
        self.messages: list[Message] = []

    def system_prompt(self) -> str:
        s = self.session
        env = (f"{self.role.prompt}\n\n## Environment\n"
               f"- Workspace (all paths are relative to it): {s.workspace}\n"
               f"- Permission mode: {s.permissions.mode}. {MODE_NOTES[s.permissions.mode]}\n")
        if "mcp:*" in self.role.tools and not s.mcp_tools:
            env += ("- WARNING: no MCP/web tools are available in this session. Do NOT answer from memory as if "
                    "you had researched it. Start your answer with 'NO WEB ACCESS:' and clearly mark everything "
                    "as unverified model knowledge without sources.\n")
        return env

    def emit(self, kind: str, text: str = "", **data):
        self.session.ui.emit(Event(kind, self.name, text, data))

    async def run(self, task: str) -> str:
        """Add `task` as a user message and loop until the model answers without tool calls."""
        self.messages.append(Message("user", task))
        max_steps = self.session.config.max_steps
        last_text = ""
        for _ in range(max_steps):
            try:
                reply = await self.provider.complete(self.system_prompt(), self.messages,
                                                     [t.spec() for t in self.tools.values()])
            except ProviderError as e:
                self.emit("error", str(e))
                return f"ERROR: the {self.name} agent's model failed: {e}"
            self.messages.append(Message("assistant", reply.text, reply.tool_calls, raw=reply.raw))
            if reply.text:
                last_text = reply.text
            if not reply.tool_calls:
                return reply.text
            if reply.text:
                self.emit("text", reply.text)
            for call in reply.tool_calls:
                result = await self.execute(call.name, call.args)
                self.messages.append(Message("tool", result, tool_call_id=call.id, name=call.name))
        self.emit("warn", f"stopped after {max_steps} steps")
        return f"(stopped after {max_steps} steps) {last_text}"

    async def execute(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if not tool:
            return f"ERROR: unknown tool `{name}`. Available: {', '.join(self.tools)}"
        self.emit("tool_call", f"{name}({short_args(args)})", tool=name, args=args)
        ctx = ToolContext(self.session, self.name)
        perms = self.session.permissions
        decision, reason = perms.decide(tool.kind, name, args)
        if decision == "deny":
            result = f"DENIED: {reason}"
            self.emit("tool_result", result, tool=name, ok=False)
            return result
        if decision == "ask":
            try:
                preview = tool.preview(args, ctx) if tool.preview else json.dumps(args, indent=2, ensure_ascii=False)
            except Exception as e:
                preview = f"(no preview: {e})"
            if reason:
                preview = f"⚠ {reason}\n\n{preview}"
            answer = await self.session.ui.approve(self.name, f"{self.name} → {name}", preview)
            if answer == "always":
                perms.always_allowed.add(name)
            elif answer != "yes":
                result = "DENIED by the user. Do not retry this exact call; adapt your approach or report back."
                self.emit("tool_result", result, tool=name, ok=False)
                return result
        try:
            result = await tool.fn(args, ctx)
        except PathError as e:
            result = f"ERROR: {e}"
        except KeyError as e:
            result = f"ERROR: missing argument {e}"
        except Exception as e:
            result = f"ERROR: {type(e).__name__}: {e}"
        result = truncate(result)
        self.emit("tool_result", result, tool=name, ok=not result.startswith(("ERROR", "DENIED")))
        return result
