"""
Permission modes (switch with Shift+Tab or /mode):

  manual        every file write and every shell command needs approval
  accept-edits  file writes run automatically, shell commands need approval
  auto          everything runs automatically, except blocked dangerous commands
  plan          read-only: writes and shell commands are refused
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import MODES

BLOCKLIST = [
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(/|~|\$HOME)(\s|$)",   # rm -rf / ~ $HOME
    r"\bsudo\b", r"\bsu\s", r"\bmkfs\b", r"\bdd\s+if=", r"\bshutdown\b", r"\breboot\b",
    r"(curl|wget)[^|]*\|\s*(ba|z|fi)?sh\b",                # pipe-to-shell
    r"\bgit\s+push\b", r"\bgit\s+reset\s+--hard\b",
    r":\(\)\s*\{.*\};\s*:",                                # fork bomb
    r"\bchmod\s+-R\s+777\s+/",
]


def blocked_reason(command: str) -> str | None:
    for pat in BLOCKLIST:
        if re.search(pat, command):
            return f"matches blocklist pattern `{pat}`"
    return None


class PathError(Exception):
    pass


def resolve_in_workspace(workspace: Path, path: str) -> Path:
    """Resolve `path` relative to the workspace and refuse anything outside it (symlinks resolved)."""
    p = Path(path).expanduser()
    full = (p if p.is_absolute() else workspace / p).resolve()
    try:
        full.relative_to(workspace.resolve())
    except ValueError:
        raise PathError(f"path `{path}` is outside the workspace {workspace}") from None
    return full


class Permissions:
    def __init__(self, mode: str = "manual"):
        self.set_mode(mode)
        self.always_allowed: set[str] = set()     # tool names approved with "always" this session

    def set_mode(self, mode: str):
        if mode not in MODES:
            raise ValueError(f"unknown mode `{mode}` (one of {', '.join(MODES)})")
        self.mode = mode

    def cycle(self) -> str:
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        return self.mode

    def decide(self, kind: str, tool_name: str, args: dict) -> tuple[str, str]:
        """Return ("allow" | "ask" | "deny", reason)."""
        if kind in ("read", "meta"):
            return "allow", ""
        if kind == "shell":
            reason = blocked_reason(args.get("command", ""))
            if self.mode == "plan":
                return "deny", "plan mode is read-only"
            if reason and self.mode == "auto":
                return "deny", f"blocked in auto mode: {reason}"
            if self.mode == "auto" or tool_name in self.always_allowed:
                return ("ask", f"warning: {reason}") if reason else ("allow", "")
            return "ask", f"warning: {reason}" if reason else ""
        if kind == "write":
            if self.mode == "plan":
                return "deny", "plan mode is read-only"
            if self.mode in ("accept-edits", "auto") or tool_name in self.always_allowed:
                return "allow", ""
            return "ask", ""
        if kind == "mcp":
            if self.mode == "manual" and tool_name not in self.always_allowed:
                return "ask", ""
            return "allow", ""
        return "ask", f"unknown tool kind {kind}"
