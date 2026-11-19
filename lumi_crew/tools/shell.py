"""Shell tool: runs a command inside the workspace."""
from __future__ import annotations

import asyncio
import os
import sys

from .base import Tool, schema, truncate


def shell_env(workspace) -> dict:
    """PATH gets the workspace's own venv first; $CREW_PYTHON is a Python that has pytest installed."""
    env = dict(os.environ)
    for venv in (".venv", "venv"):
        bin_dir = os.path.join(workspace, venv, "bin")
        if os.path.isdir(bin_dir):
            env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = os.path.join(workspace, venv)
            break
    env["CREW_PYTHON"] = sys.executable
    return env


async def run_shell(args, ctx):
    timeout = min(int(args.get("timeout", ctx.session.config.shell_timeout)), 600)
    proc = await asyncio.create_subprocess_shell(
        args["command"], cwd=ctx.workspace, env=shell_env(str(ctx.workspace)),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"ERROR: command timed out after {timeout}s"
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    text = out.decode(errors="replace").strip() or "(no output)"
    return f"exit code {proc.returncode}\n{truncate(text, 8000)}"


SHELL_TOOLS = [
    Tool("run_shell", "Run a shell command in the workspace directory (non-interactive). Returns exit code and output.",
         schema({"command": {"type": "string"},
                 "timeout": {"type": "integer", "description": "seconds, default 120"}}, ["command"]),
         "shell", run_shell, lambda a, c: f"$ {a['command']}"),
]
