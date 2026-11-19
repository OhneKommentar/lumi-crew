"""
Lumi Crew: multi-agent coding CLI (command: lumi-crew).

  lumi-crew                        interactive TUI in the current directory
  lumi-crew -w ./proj --mode auto   choose workspace and start mode
  lumi-crew -p "build X"            headless: run one request, print the result
  crew providers               show which providers are usable
  lumi-lumi-crew -c / --resume      continue the last chat / pick a saved chat
  crew chats [-a]              list saved chats (of this folder / all)
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

from . import __version__
from .agents.roles import ROLES
from .config import MODES, load_config, load_saved_models
from .events import Event, UI
from .providers import BUILTIN, availability

console = Console()

AGENT_COLOR = "#9fb6d6"


class ConsoleUI(UI):
    """Plain terminal UI for headless runs (`lumi-crew -p`)."""

    def __init__(self, interactive: bool, custom: dict | None = None):
        self.interactive = interactive
        self.custom = custom or {}
        self.lock = asyncio.Lock()          # parallel agents ask one after another

    def emit(self, e: Event):
        c = AGENT_COLOR
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

    async def ask_user(self, agent, question, options=None):
        async with self.lock:
            body = question + ("\n\n" + "\n".join(f"  {i}. {o}" for i, o in enumerate(options, 1)) if options else "")
            if options:
                body += f"\n  {len(options) + 1}. ✎ eigene Antwort schreiben"
            console.print(Panel(body, title=f"{agent} fragt", border_style=AGENT_COLOR))
            if not self.interactive:
                console.print("[dim](no terminal input: agent continues with defaults)[/]")
                return options[0] if options else ""
            ans = (await asyncio.to_thread(input, "› ")).strip()
            if options and ans.isdigit():
                n = int(ans)
                if 1 <= n <= len(options):
                    return options[n - 1]
                if n == len(options) + 1:
                    return (await asyncio.to_thread(input, "✎ › ")).strip()
            return ans

    async def approve(self, agent, title, preview):
        async with self.lock:
            lexer = "diff" if preview.lstrip().startswith(("---", "⚠")) or "\n+" in preview else "bash"
            console.print(Panel(Syntax(preview[:6000], lexer, word_wrap=True), title=title, border_style="yellow"))
            if not self.interactive:
                console.print("[red]denied (no terminal for approval, use --mode auto)[/]")
                return "no"
            ans = (await asyncio.to_thread(input, "Erlauben? [y]es / [n]o / [a]lways › ")).strip().lower()
            return {"y": "yes", "yes": "yes", "j": "yes", "a": "always", "always": "always"}.get(ans, "no")

    async def choose_fallback(self, agent, role, current, error):
        async with self.lock:
            from .models import fallback_suggestions
            options = await fallback_suggestions(current, self.custom)
            console.print(Panel(f"{current} hat ein Usage-Limit erreicht:\n[dim]{escape(error[:300])}[/]\n\n"
                                + "\n".join(f"  {i}. {spec}  [dim]{escape(desc)}[/]" for i, (spec, desc) in
                                            enumerate(options, 1)),
                                title=f"{agent}: anderes Modell nehmen?", border_style="yellow"))
            if not self.interactive or not options:
                return None
            ans = (await asyncio.to_thread(input, "Nummer oder provider:model (leer = abbrechen) › ")).strip()
            if ans.isdigit() and 1 <= int(ans) <= len(options):
                return options[int(ans) - 1][0]
            return ans or None


def escape(text: str) -> str:
    from rich.markup import escape as e
    return e(text)


def cmd_providers(cfg, saved: dict | None = None):
    from rich.table import Table
    t = Table(title="Lumi Crew · providers")
    t.add_column("provider"), t.add_column("type"), t.add_column("status")
    for name in sorted(set(BUILTIN) | set(cfg.providers)):
        ok, msg = availability(name, cfg.providers)
        typ = {**BUILTIN.get(name, {}), **cfg.providers.get(name, {})}.get("type", "?")
        t.add_row(name, typ, f"[green]✔ {msg}[/]" if ok else f"[red]✗ {msg}[/]")
    console.print(t)
    t2 = Table(title="agents → model")
    t2.add_column("agent"), t2.add_column("provider:model")
    for role in ROLES:
        saved = saved or {}
        t2.add_row(role, f"{saved[role]}  [dim](gespeichert)[/]" if role in saved else cfg.spec_for(role))
    console.print(t2)


async def run_headless(workspace, cfg, mode, prompt, use_saved=True, cont=False):
    from . import chats
    from .session import Session
    ui = ConsoleUI(interactive=sys.stdin.isatty(), custom=cfg.providers)
    session = Session(workspace, cfg, ui, mode=mode, use_saved=use_saved)
    await session.start()
    previous = chats.list_chats(session.workspace) if cont else []
    chat = previous[0] if previous else chats.new_chat(session.workspace)
    if previous:
        session.orchestrator.messages = [chats.message_from_dict(d) for d in chat.messages]
        console.print(f"[dim]↺ continuing chat “{chat.title}” ({chats.ago(chat.updated)})[/]")
    console.print(f"[dim]workspace {session.workspace} · mode {session.permissions.mode}[/]")
    chat.entries.append({"kind": "user", "text": prompt})
    result = await session.send(prompt)
    chat.entries.append({"kind": "agent", "agent": "orchestrator", "model": session.spec_for("orchestrator"),
                         "text": result})
    chats.save(chat, session.orchestrator.messages)
    console.print(Panel(Markdown(result or "(no answer)"), title="Lumi Crew", border_style=AGENT_COLOR))


def cmd_chats(workspace: Path, show_all: bool):
    from rich.table import Table
    from . import chats
    found = chats.list_chats(None if show_all else workspace)
    if not found:
        console.print("[dim]Keine gespeicherten Chats" + ("" if show_all else " in diesem Ordner (lumi-crew chats -a zeigt alle)")
                      + ".[/]")
        return
    t = Table(title="Lumi Crew · chats", show_lines=False)
    t.add_column("zuletzt"), t.add_column("Nachr.", justify="right"), t.add_column("Titel")
    if show_all:
        t.add_column("workspace")
    home = str(Path.home())
    for c in found:
        cells = [chats.ago(c.updated), str(c.user_turns), c.title or "(ohne Titel)"]
        if show_all:
            cells.append(c.workspace.replace(home, "~"))
        t.add_row(*cells)
    console.print(t)
    console.print("[dim]Fortsetzen: lumi-crew -c (letzter Chat) · lumi-crew --resume (Auswahl) · in der TUI /resume[/]")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lumi-crew", description="Lumi Crew: multi-agent coding CLI")
    ap.add_argument("command", nargs="?", choices=["providers", "chats"],
                    help="`providers`: list usable providers · `chats`: list saved chats")
    ap.add_argument("-w", "--workspace", default=".", help="project directory (default: current dir)")
    ap.add_argument("-p", "--print", dest="prompt", help="run one request headless and print the result")
    ap.add_argument("--mode", choices=MODES, help="permission mode at start")
    ap.add_argument("--model", help='use this "provider:model" for all agents, e.g. claude:sonnet')
    ap.add_argument("--config", help="path to lumi-crew.toml")
    ap.add_argument("-V", "--version", action="version", version=f"Lumi Crew {__version__}")
    ap.add_argument("-c", "--continue", dest="cont", action="store_true", help="continue the last chat of this folder")
    ap.add_argument("--resume", action="store_true", help="pick a saved chat to continue")
    ap.add_argument("-a", "--all", action="store_true", help="with `chats`: show chats of all folders")
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
        return cmd_providers(cfg, {} if args.model else load_saved_models())
    if args.command == "chats":
        return cmd_chats(workspace.resolve(), args.all)
    if args.prompt:
        try:
            asyncio.run(run_headless(workspace, cfg, args.mode, args.prompt, use_saved=not args.model,
                                     cont=args.cont))
        except KeyboardInterrupt:
            console.print("\n[red]abgebrochen[/]")
        return
    from .tui.app import CrewApp
    start = "continue" if args.cont else "resume" if args.resume else None
    CrewApp(workspace, cfg, args.mode, use_saved=not args.model, start=start).run()


if __name__ == "__main__":
    main()
