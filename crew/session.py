"""A crew session: workspace + config + permissions + agents, driven by a UI."""
from __future__ import annotations

from pathlib import Path

from .agents.base import Agent
from .agents.roles import ROLES, SPECIALISTS
from .config import Config
from .events import Event, UI
from .permissions import Permissions
from .providers import create_provider
from .providers.base import Provider
from .tools.base import Tool, schema
from .tools.fs import READ_TOOLS, WRITE_TOOLS
from .tools.mcp import load_mcp_tools
from .tools.shell import SHELL_TOOLS


class Session:
    def __init__(self, workspace: Path, config: Config, ui: UI, mode: str | None = None):
        self.workspace = workspace.resolve()
        self.config = config
        self.ui = ui
        self.permissions = Permissions(mode or config.mode)
        self.overrides: dict[str, str] = {}          # role -> spec, set with /model
        self._providers: dict[str, Provider] = {}
        self.mcp_tools: list[Tool] = []
        self.delegations = 0
        self.review_rounds = 0
        self.orchestrator: Agent | None = None
        self.active: set[str] = set()                # running agent instance names

    # ------------------------------------------------------------------ setup
    async def start(self):
        if self.config.mcp:
            self.mcp_tools, warnings = await load_mcp_tools(self.config.mcp)
            for w in warnings:
                self.ui.emit(Event("warn", "crew", w))
            if self.mcp_tools:
                self.ui.emit(Event("info", "crew", f"MCP tools: {', '.join(t.name for t in self.mcp_tools)}"))
        self.orchestrator = self._make_agent("orchestrator")

    def spec_for(self, role: str) -> str:
        return self.overrides.get(role) or self.config.spec_for(role)

    def provider_for(self, role: str) -> Provider:
        spec = self.spec_for(role)
        if spec not in self._providers:
            self._providers[spec] = create_provider(spec, self.config.providers)
        return self._providers[spec]

    def set_model(self, role: str, spec: str, keep_history: bool = False):
        create_provider(spec, self.config.providers)   # validate early
        targets = list(ROLES) if role == "all" else [role]
        for r in targets:
            if r not in ROLES:
                raise ValueError(f"unknown agent `{r}`")
            self.overrides[r] = spec
        if self.orchestrator and "orchestrator" in targets:
            if keep_history:        # switching mid-run (usage limit): keep the conversation, drop native blocks
                self.orchestrator.switch_provider(self.provider_for("orchestrator"))
            else:
                self.orchestrator.provider = self.provider_for("orchestrator")
                self.orchestrator.messages.clear()

    def tools_for(self, role_name: str) -> list[Tool]:
        role = ROLES[role_name]
        available = {t.name: t for t in READ_TOOLS + WRITE_TOOLS + SHELL_TOOLS + self._meta_tools()}
        tools = []
        for name in role.tools:
            if name == "mcp:*":
                tools += self.mcp_tools
            elif name in available:
                tools.append(available[name])
        if role.can_ask_user:
            tools.append(available["ask_user"])
        return tools

    def _make_agent(self, role_name: str, instance: str | None = None) -> Agent:
        return Agent(ROLES[role_name], self.provider_for(role_name), self.tools_for(role_name), self, instance)

    # ------------------------------------------------------------------ meta tools
    def _meta_tools(self) -> list[Tool]:
        return [
            Tool("delegate",
                 "Hand a self-contained task to a specialist agent and get its result back. "
                 "Agents: " + "; ".join(f"{n}: {ROLES[n].summary}" for n in SPECIALISTS),
                 schema({"agent": {"type": "string", "enum": SPECIALISTS},
                         "task": {"type": "string", "description": "complete task description incl. all context"}},
                        ["agent", "task"]),
                 "meta", self._delegate),
            Tool("ask_user",
                 "Ask the user ONE question and wait for the answer. Always give 2-5 concrete answer `options` "
                 "(put the recommended one first and end it with ' (empfohlen)'); the user can also type their own.",
                 schema({"question": {"type": "string"},
                         "options": {"type": "array", "items": {"type": "string"},
                                     "description": "2-5 short suggested answers"}}, ["question", "options"]),
                 "meta", self._ask_user),
        ]

    async def _delegate(self, args, ctx) -> str:
        role, task = args["agent"], args["task"]
        if role not in SPECIALISTS:
            return f"ERROR: unknown agent `{role}`"
        if self.delegations >= self.config.max_delegations:
            return (f"LIMIT: maximum of {self.config.max_delegations} delegations reached. "
                    "Stop delegating and report the current state to the user.")
        if role == "reviewer":
            if self.review_rounds >= self.config.max_review_rounds:
                return (f"LIMIT: {self.config.max_review_rounds} review rounds done. Do not request another review; "
                        "mention the remaining issues in your report.")
            self.review_rounds += 1
        self.delegations += 1
        n, name = 1, role
        while name in self.active:                   # parallel agents of the same role: coder, coder#2 …
            n += 1
            name = f"{role}#{n}"
        self.active.add(name)
        agent = self._make_agent(role, name)
        self.ui.emit(Event("agent_start", name, task,
                           {"parent": ctx.agent, "role": role, "model": agent.provider.label}))
        try:
            result = await agent.run(task)
        finally:
            self.active.discard(name)
        self.ui.emit(Event("agent_end", name, result, {"role": role, "model": agent.provider.label}))
        return f"Result from {name}:\n{result}"

    async def _ask_user(self, args, ctx) -> str:
        options = [str(o) for o in (args.get("options") or []) if str(o).strip()][:6]
        answer = await self.ui.ask_user(ctx.agent, args["question"], options)
        return f"User answered: {answer}" if answer.strip() else "User gave no answer; use a sensible default."

    # ------------------------------------------------------------------ main entry
    async def send(self, text: str) -> str:
        if self.orchestrator is None:
            await self.start()
        self.delegations = 0
        self.review_rounds = 0
        o = self.orchestrator
        self.ui.emit(Event("agent_start", "orchestrator", text,
                           {"parent": None, "role": "orchestrator", "model": o.provider.label}))
        result = await o.run(text)
        self.ui.emit(Event("agent_end", "orchestrator", result))
        return result

    def reset(self):
        if self.orchestrator:
            self.orchestrator.messages.clear()
