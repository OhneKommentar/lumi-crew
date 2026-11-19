"""MCP client: exposes the tools of configured MCP servers as crew tools."""
from __future__ import annotations

import json

from .base import Tool, truncate


def _result_text(res) -> str:
    parts = []
    for c in getattr(res, "content", None) or []:
        parts.append(getattr(c, "text", None) or str(c))
    if not parts and getattr(res, "data", None) is not None:
        parts.append(json.dumps(res.data, ensure_ascii=False, indent=1, default=str))
    return "\n".join(parts) or "(empty result)"


def _make_call(url: str, remote_name: str):
    async def call(args, ctx):
        from fastmcp import Client
        async with Client(url) as client:
            res = await client.call_tool(remote_name, args, raise_on_error=False)
        text = _result_text(res)
        return truncate(f"ERROR: {text}" if getattr(res, "is_error", False) else text)
    return call


async def load_mcp_tools(servers: dict[str, dict]) -> tuple[list[Tool], list[str]]:
    """Connect to every server once, list its tools. Returns (tools, warnings)."""
    from fastmcp import Client
    tools, warnings, seen = [], [], set()
    for server, cfg in servers.items():
        url = cfg.get("url")
        if not url:
            warnings.append(f"MCP server `{server}`: no url configured")
            continue
        try:
            async with Client(url) as client:
                remote = await client.list_tools()
        except Exception as e:
            warnings.append(f"MCP server `{server}` ({url}) not reachable: {type(e).__name__}")
            continue
        for t in remote:
            name = t.name if t.name not in seen else f"{server}__{t.name}"
            seen.add(name)
            params = t.input_schema or {"type": "object", "properties": {}}
            tools.append(Tool(name, f"[MCP {server}] {t.description or ''}".strip(), params, "mcp",
                              _make_call(url, t.name),
                              lambda a, c, n=t.name: f"{n}({json.dumps(a, ensure_ascii=False)})"))
    return tools, warnings
