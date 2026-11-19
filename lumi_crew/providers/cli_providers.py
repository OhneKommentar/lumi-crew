"""
Providers that drive an installed agent CLI as a plain text LLM.
Each call runs in a fresh, empty temp directory with the CLI's own tools disabled
or sandboxed read-only, so only crew's tools touch the workspace.
Auth comes from the CLI's own login (no keys stored in crew).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time

from .. import usage
from .base import LimitError, ProviderError, is_limit_message
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
        cls = LimitError if is_limit_message(msg) else ProviderError
        raise cls(f"`{args[0]}` exited with {proc.returncode}: {msg}")
    return out.decode(errors="replace")


class AgyProvider(TextToolProvider):
    """Google Antigravity CLI (`agy -p`), uses the Antigravity Google login."""
    kind = "agy"

    async def complete_text(self, system: str, prompt: str) -> str:
        full = f"{system}\n\n{prompt}"
        args = ["agy", "-p", full, "--disable-slash-commands", "--output-format", "json"]
        if self.model:
            args += ["--model", self.model]
        start = time.time()
        with tempfile.TemporaryDirectory(prefix="crew-agy-") as tmp:
            try:
                out = await run_cli(args, cwd=tmp)
            except ProviderError:  # incl. LimitError
                usage.record(self.name, self.model, seconds=time.time() - start, error=True)
                raise
        try:
            d = json.loads(out)
        except json.JSONDecodeError:            # older agy without JSON output
            usage.record(self.name, self.model, seconds=time.time() - start)
            return out.strip()
        u = d.get("usage") or {}
        usage.record(self.name, self.model, input_tokens=u.get("input_tokens", 0),
                     output_tokens=u.get("output_tokens", 0) + u.get("thinking_tokens", 0),
                     seconds=time.time() - start)
        return str(d.get("response", "")).strip()


class ClaudeCliProvider(TextToolProvider):
    """Claude Code CLI (`claude -p`), uses the Claude Code login / subscription."""
    kind = "claude_cli"

    async def complete_text(self, system: str, prompt: str) -> str:
        args = ["claude", "-p", "--tools", "", "--system-prompt", system,
                "--no-session-persistence", "--strict-mcp-config", "--output-format", "json"]
        if self.model:
            args += ["--model", self.model]
        start = time.time()
        with tempfile.TemporaryDirectory(prefix="crew-claude-") as tmp:
            try:
                out = await run_cli(args, cwd=tmp, stdin_text=prompt)
            except ProviderError:  # incl. LimitError
                usage.record(self.name, self.model, seconds=time.time() - start, error=True)
                raise
        try:
            d = json.loads(out)
        except json.JSONDecodeError:
            usage.record(self.name, self.model, seconds=time.time() - start)
            return out.strip()
        u = d.get("usage") or {}
        usage.record(self.name, self.model,
                     input_tokens=u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                     + u.get("cache_creation_input_tokens", 0),
                     output_tokens=u.get("output_tokens", 0), cost_usd=d.get("total_cost_usd") or 0,
                     seconds=time.time() - start)
        if d.get("is_error"):
            msg = str(d.get("result") or "error")
            raise (LimitError if is_limit_message(msg) else ProviderError)(f"claude: {msg}")
        return str(d.get("result", "")).strip()


class CodexCliProvider(TextToolProvider):
    """OpenAI Codex CLI (`codex exec`), uses the Codex login. Sandbox is read-only."""
    kind = "codex_cli"

    async def complete_text(self, system: str, prompt: str) -> str:
        start = time.time()
        with tempfile.TemporaryDirectory(prefix="crew-codex-") as tmp:
            out_file = os.path.join(tmp, "last_message.txt")
            args = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                    "--ephemeral", "--color", "never", "--json", "-o", out_file]
            if self.model:
                args += ["-m", self.model]
            args.append("-")  # read prompt from stdin
            try:
                stdout = await run_cli(args, cwd=tmp, stdin_text=f"{system}\n\n{prompt}")
            except ProviderError:  # incl. LimitError
                usage.record(self.name, self.model, seconds=time.time() - start, error=True)
                raise
            tokens_in = tokens_out = 0
            last_text = ""
            for line in stdout.splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "turn.completed":
                    u = ev.get("usage") or {}
                    tokens_in += u.get("input_tokens", 0)
                    tokens_out += u.get("output_tokens", 0) + u.get("reasoning_output_tokens", 0)
                elif ev.get("type") == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
                    last_text = ev["item"].get("text", "")
            usage.record(self.name, self.model, input_tokens=tokens_in, output_tokens=tokens_out,
                         seconds=time.time() - start)
            if os.path.exists(out_file):
                with open(out_file, encoding="utf-8", errors="replace") as f:
                    return f.read().strip()
            return last_text.strip()
