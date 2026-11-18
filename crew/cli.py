"""
crew: multi-agent coding CLI.

  crew                         interactive TUI in the current directory
  crew -w ./proj --mode auto   choose workspace and start mode
  crew -p "build X"            headless: run one request, print the result
  crew providers               show which providers are usable
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from .agents.roles import ROLES
from .config import MODES, load_config
from .events import Event, UI
from .providers import BUILTIN, availability

console = Console()

COLORS = {"orchestrator": "bold magenta", "clarifier": "yellow", "planner": "cyan", "coder": "green",
          "reviewer": "blue", "tester": "bright_red", "researcher": "bright_cyan"}


class ConsoleUI(UI):
    """Plain terminal UI for headless runs (`crew -p`)."""

    def __init__(self, interactive: bool):
        self.interactive = interactive

    def emit(self, e: Event):
        c = COLORS.get(e.agent, "white")
        tag = f"[{c}]{e.agent:>12}[/]"
        if e.kind == "agent_start":
            console.print(f"{tag} ▶ [dim]{e.data.get('model', '')}[/]  {e.text[:160].replace(chr(10), ' ')}")
        elif e.kind == "agent_end" and e.agent != "orchestrator":
            console.print(f"{tag} ✔ {e.text[:200].replace(chr(10), ' ')}")
        elif e.kind == "tool_call":
            console.print(f"{tag}   [dim]⚙ {escape(e.text)}[/]")
        elif e.kind == "tool_result" and not e.data.get("ok", True):
            console.print(f"{tag}   [red]✗ {escape(e.text[:200])}[/]")
        elif e.kind == "text":
            console.print(f"{tag}   [italic]{escape(e.text[:300])}[/]")
        elif e.kind in ("warn", "error"):
            console.print(f"{tag} [{'yellow' if e.kind == 'warn' else 'red'}]{escape(e.text)}[/]")
        elif e.kind == "info":
            console.print(f"{tag} [dim]{escape(e.text)}[/]")

    async def ask_user(self, agent, question):
        console.print(Panel(question, title=f"{agent} fragt", border_style="yellow"))
        if not self.interactive:
            console.print("[dim](no terminal input: agent continues with defaults)[/]")
            return ""
        return await asyncio.to_thread(input, "› ")

    async def approve(self, agent, title, preview):
        lexer = "diff" if preview.lstrip().startswith(("---", "⚠")) or "\n+" in preview else "bash"
        console.print(Panel(Syntax(preview[:6000], lexer, word_wrap=True), title=title, border_style="yellow"))
        if not self.interactive:
            console.print("[red]denied (no terminal for approval, use --mode auto)[/]")
            return "no"
        ans = (await asyncio.to_thread(input, "Erlauben? [y]es / [n]o / [a]lways › ")).strip().lower()
        return {"y": "yes", "yes": "yes", "j": "yes", "a": "always", "always": "always"}.get(ans, "no")


def escape(text: str) -> str:
    from rich.markup import escape as e
    return e(text)


def cmd_providers(cfg):
    from rich.table import Table
    t = Table(title="crew providers")
    t.add_column("provider"), t.add_column("type"), t.add_column("status")
    for name in sorted(set(BUILTIN) | set(cfg.providers)):
        ok, msg = availability(name, cfg.providers)
        typ = {**BUILTIN.get(name, {}), **cfg.providers.get(name, {})}.get("type", "?")
        t.add_row(name, typ, f"[green]✔ {msg}[/]" if ok else f"[red]✗ {msg}[/]")
    console.print(t)
    t2 = Table(title="agents → model")
    t2.add_column("agent"), t2.add_column("provider:model")
    for role in ROLES:
        t2.add_row(role, cfg.spec_for(role))
    console.print(t2)


async def run_headless(workspace, cfg, mode, prompt):
    from .session import Session
    ui = ConsoleUI(interactive=sys.stdin.isatty())
    session = Session(workspace, cfg, ui, mode=mode)
    console.print(f"[dim]workspace {session.workspace} · mode {session.permissions.mode}[/]")
    result = await session.send(prompt)
    console.print(Panel(Markdown(result or "(no answer)"), title="crew", border_style="magenta"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="crew", description="Multi-agent coding CLI")
    ap.add_argument("command", nargs="?", choices=["providers"], help="`providers`: list usable providers")
    ap.add_argument("-w", "--workspace", default=".", help="project directory (default: current dir)")
    ap.add_argument("-p", "--print", dest="prompt", help="run one request headless and print the result")
    ap.add_argument("--mode", choices=MODES, help="permission mode at start")
    ap.add_argument("--model", help='use this "provider:model" for all agents, e.g. claude:sonnet')
    ap.add_argument("-c", "--config", help="path to crew.toml")
    args = ap.parse_args(argv)

    workspace = Path(args.workspace).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        cfg = load_config(workspace.resolve(), args.config)
    except (ValueError, OSError) as e:
        console.print(f"[red]config error: {e}[/]")
        sys.exit(2)
    if args.model:
        cfg.default, cfg.agents = args.model, {}

    if args.command == "providers":
        return cmd_providers(cfg)
    if args.prompt:
        try:
            asyncio.run(run_headless(workspace, cfg, args.mode, args.prompt))
        except KeyboardInterrupt:
            console.print("\n[red]abgebrochen[/]")
        return
    from .tui.app import CrewApp
    CrewApp(workspace, cfg, args.mode).run()


if __name__ == "__main__":
    main()
