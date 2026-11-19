"""Role definitions: which prompt and which tools each agent gets."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

READ = ["read_file", "list_dir", "grep"]
WRITE = ["write_file", "edit_file"]


@dataclass
class Role:
    name: str
    summary: str
    tools: list[str]            # tool names; "mcp:*" = all MCP tools
    can_ask_user: bool = False

    @property
    def prompt(self) -> str:
        return resources.files(__package__).joinpath(f"prompts/{self.name}.md").read_text()


ROLES: dict[str, Role] = {r.name: r for r in [
    Role("orchestrator", "coordinates the team", READ + ["delegate"], can_ask_user=True),
    Role("clarifier", "asks questions until the task is clear, writes a spec", READ, can_ask_user=True),
    Role("planner", "designs the implementation plan", READ),
    Role("coder", "writes and edits code", READ + WRITE),
    Role("reviewer", "reviews code, APPROVED / CHANGES_REQUESTED", READ),
    Role("tester", "runs code and tests, PASS / FAIL", READ + ["run_shell"]),
    Role("researcher", "web / document research via MCP", READ + ["mcp:*"]),
]}

SPECIALISTS = [n for n in ROLES if n != "orchestrator"]
