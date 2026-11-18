"""
Providers that drive an installed agent CLI as a plain text LLM.
Each call runs in a fresh, empty temp directory with the CLI's own tools disabled
or sandboxed read-only, so only crew's tools touch the workspace.
Auth comes from the CLI's own login (no keys stored in crew).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

from .base import ProviderError
from .text_tools import TextToolProvider


async def run_cli(args: list[str], cwd: str, stdin_text: str | None = None, timeout: float = 900) -> str:
    if not shutil.which(args[0]):
        raise ProviderError(f"`{args[0]}` is not installed or not on PATH")
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin_text.encode() if stdin_text is not None else None), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        msg = (err.decode(errors="replace") or out.decode(errors="replace")).strip()[-800:]
        raise ProviderError(f"`{args[0]}` exited with {proc.returncode}: {msg}")
    return out.decode(errors="replace")


class AgyProvider(TextToolProvider):
    """Google Antigravity CLI (`agy -p`), uses the Antigravity Google login."""
    kind = "agy"

    async def complete_text(self, system: str, prompt: str) -> str:
        full = f"{system}\n\n{prompt}"
        args = ["agy", "-p", full, "--disable-slash-commands"]
        if self.model:
            args += ["--model", self.model]
        with tempfile.TemporaryDirectory(prefix="crew-agy-") as tmp:
            return (await run_cli(args, cwd=tmp)).strip()


class ClaudeCliProvider(TextToolProvider):
    """Claude Code CLI (`claude -p`), uses the Claude Code login / subscription."""
    kind = "claude_cli"

    async def complete_text(self, system: str, prompt: str) -> str:
        args = ["claude", "-p", "--tools", "", "--system-prompt", system,
                "--no-session-persistence", "--strict-mcp-config", "--output-format", "text"]
        if self.model:
            args += ["--model", self.model]
        with tempfile.TemporaryDirectory(prefix="crew-claude-") as tmp:
            return (await run_cli(args, cwd=tmp, stdin_text=prompt)).strip()


class CodexCliProvider(TextToolProvider):
    """OpenAI Codex CLI (`codex exec`), uses the Codex login. Sandbox is read-only."""
    kind = "codex_cli"

    async def complete_text(self, system: str, prompt: str) -> str:
        with tempfile.TemporaryDirectory(prefix="crew-codex-") as tmp:
            out_file = os.path.join(tmp, "last_message.txt")
            args = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                    "--ephemeral", "--color", "never", "-o", out_file]
            if self.model:
                args += ["-m", self.model]
            args.append("-")  # read prompt from stdin
            stdout = await run_cli(args, cwd=tmp, stdin_text=f"{system}\n\n{prompt}")
            if os.path.exists(out_file):
                with open(out_file, encoding="utf-8", errors="replace") as f:
                    return f.read().strip()
            return stdout.strip()
