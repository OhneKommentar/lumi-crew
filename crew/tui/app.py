"""Textual TUI for crew."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Collapsible, Footer, Input, Markdown, Static, Tree

from ..agents.roles import ROLES
from ..config import MODES, Config
from ..events import Event, UI
from ..session import Session
from .widgets import ChoiceScreen, approval_screen

MODE_STYLE = {"manual": "black on yellow", "accept-edits": "black on cyan", "auto": "white on red",
              "plan": "white on blue"}
AGENT_STYLE = {"orchestrator": "bold magenta", "clarifier": "yellow", "planner": "cyan", "coder": "green",
               "reviewer": "dodger_blue1", "tester": "red", "researcher": "bright_cyan"}
ICON = {"orchestrator": "🎯", "clarifier": "❓", "planner": "🗺", "coder": "⌨", "reviewer": "🔍",
        "tester": "🧪", "researcher": "🌐"}

HELP = """**Befehle**
- `/mode [manual|accept-edits|auto|plan]`: Modus anzeigen/setzen (oder **Shift+Tab**)
- `/model <agent|all> <provider:model>`: Modell für einen Agenten setzen, z. B. `/model coder codex`
- `/agents`: welcher Agent welches Modell nutzt
- `/providers`: verfügbare Provider prüfen
- `/mcp`: geladene MCP-Tools
- `/clear`: Verlauf leeren · `/quit`: beenden

**Tasten:** Shift+Tab Modus wechseln · Esc laufende Agenten abbrechen · Ctrl+C beenden
"""


class TuiUI(UI):
    def __init__(self, app: "CrewApp"):
        self.app = app

    def emit(self, event: Event):
        self.app.on_crew_event(event)

    async def ask_user(self, agent: str, question: str) -> str:
        fut = asyncio.get_running_loop().create_future()
        self.app.ask(agent, question, fut)
        return await fut

    async def approve(self, agent: str, title: str, preview: str) -> str:
        fut = asyncio.get_running_loop().create_future()

        def done(value):
            if not fut.done():
                fut.set_result(value or "no")
        self.app.push_screen(approval_screen(title, preview), callback=done)
        return await fut


class CrewApp(App):
    TITLE = "crew"
    CSS = """
    #main { height: 1fr; }
    #chat { width: 2fr; border: round $primary; padding: 0 1; }
    #agents { width: 1fr; min-width: 30; border: round $secondary; }
    #status { height: 1; padding: 0 1; background: $boost; }
    #prompt { margin: 0 0; }
    .user { margin-top: 1; color: $accent; text-style: bold; }
    .answer { margin: 1 0; border-left: thick $success; padding-left: 1; }
    .line { color: $text-muted; }
    .question { margin-top: 1; border: round $warning; padding: 0 1; }
    Collapsible { margin: 0; padding: 0; border: none; }
    """
    BINDINGS = [
        Binding("shift+tab", "cycle_mode", "Modus", priority=True),
        Binding("escape", "cancel", "Abbrechen"),
        Binding("ctrl+c", "quit", "Beenden", priority=True),
    ]

    def __init__(self, workspace: Path, config: Config, mode: str | None = None):
        super().__init__()
        self.session = Session(workspace, config, TuiUI(self), mode=mode)
        self.pending_answer: asyncio.Future | None = None
        self.started_at: float | None = None
        self.node_stack: list = []
        self.last_leaf = None

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield VerticalScroll(id="chat")
            yield Tree("Agenten", id="agents")
        yield Input(placeholder="Was soll die Crew bauen? (/help für Befehle)", id="prompt")
        yield Static(id="status")
        yield Footer()

    def on_mount(self):
        self.chat_log = self.query_one("#chat", VerticalScroll)
        self.agent_tree = self.query_one("#agents", Tree)
        self.agent_tree.root.expand()
        self.prompt_input = self.query_one("#prompt", Input)
        self.prompt_input.focus()
        self.set_interval(1, self.update_status)
        self.update_status()
        s = self.session
        cfg = f"Config: `{s.config.path}`" if s.config.path else "Keine crew.toml gefunden, Standardwerte."
        self.add_markdown(f"### 👋 crew\nWorkspace: `{s.workspace}` · {cfg}\n\n"
                          f"Alle Agenten nutzen standardmäßig `{s.config.default}`. `/agents` zeigt Details, `/help` alle Befehle.")
        self.run_worker(self.session.start(), group="setup")

    # ------------------------------------------------------------------ chat helpers
    def add(self, widget):
        self.chat_log.mount(widget)
        self.chat_log.scroll_end(animate=False)

    def add_line(self, text: str, classes: str = "line"):
        self.add(Static(text, classes=classes))

    def add_markdown(self, text: str, classes: str = "answer"):
        self.add(Markdown(text, classes=classes))

    def tag(self, agent: str) -> str:
        return f"[{AGENT_STYLE.get(agent, 'white')}]{ICON.get(agent, '•')} {agent}[/]"

    # ------------------------------------------------------------------ status bar
    def update_status(self):
        s = self.session
        mode = s.permissions.mode
        busy = f" │ ⏳ läuft {int(time.time() - self.started_at)}s (Esc = abbrechen)" if self.started_at else ""
        self.query_one("#status", Static).update(
            f"[{MODE_STYLE[mode]}] {mode} [/] ⇧⇥ │ 📁 {escape(str(s.workspace))} │ 🎯 {escape(s.spec_for('orchestrator'))}{busy}")

    # ------------------------------------------------------------------ events from agents
    def on_crew_event(self, e: Event):
        tag = self.tag(e.agent)
        if e.kind == "agent_start":
            label = f"{ICON.get(e.agent, '•')} {e.agent} [dim]{escape(e.data.get('model', ''))}[/]"
            if e.agent == "orchestrator" or not self.node_stack:
                node = self.agent_tree.root.add(label, expand=True)
                self.node_stack = [node]
            else:
                node = self.node_stack[-1].add(label, expand=True)
                self.node_stack.append(node)
                first = e.text.strip().splitlines()[0] if e.text.strip() else ""
                self.add_line(f"{tag} ▶ {escape(first[:150])}", classes="")
        elif e.kind == "agent_end":
            if self.node_stack:
                node = self.node_stack.pop()
                node.set_label(f"{node.label} [green]✔[/]")
                node.collapse()
            if e.agent != "orchestrator":
                c = Collapsible(Markdown(e.text or "(leer)"), title=f"{ICON.get(e.agent, '')} {e.agent}: Ergebnis",
                                collapsed=True)
                self.add(c)
        elif e.kind == "tool_call":
            if self.node_stack:
                self.last_leaf = self.node_stack[-1].add_leaf(f"⚙ {escape(e.text[:60])}")
            self.add_line(f"   {tag} [dim]⚙ {escape(e.text)}[/]")
        elif e.kind == "tool_result":
            ok = e.data.get("ok", True)
            if self.last_leaf is not None:
                self.last_leaf.set_label(f"{self.last_leaf.label} {'[green]✓[/]' if ok else '[red]✗[/]'}")
            if not ok:
                self.add_line(f"   {tag} [red]✗ {escape(e.text[:200])}[/]")
        elif e.kind == "text":
            self.add_line(f"   {tag} [italic]{escape(e.text[:400])}[/]")
        elif e.kind == "warn":
            self.add_line(f"{tag} [yellow]⚠ {escape(e.text)}[/]")
        elif e.kind == "error":
            self.add_line(f"{tag} [red]✗ {escape(e.text)}[/]")
        elif e.kind == "info":
            self.add_line(f"{tag} [dim]{escape(e.text)}[/]")

    def ask(self, agent: str, question: str, fut: asyncio.Future):
        self.pending_answer = fut
        self.add(Static(f"{self.tag(agent)} fragt:\n{escape(question)}", classes="question"))
        self.prompt_input.placeholder = f"Antwort an {agent} … (Enter)"
        self.prompt_input.focus()

    # ------------------------------------------------------------------ input
    async def on_input_submitted(self, event: Input.Submitted):
        text = event.value.strip()
        self.prompt_input.value = ""
        if self.pending_answer is not None and not self.pending_answer.done():
            self.add_line(f"› {escape(text) or '(keine Antwort)'}", classes="user")
            fut, self.pending_answer = self.pending_answer, None
            self.prompt_input.placeholder = "Was soll die Crew bauen? (/help für Befehle)"
            fut.set_result(text)
            return
        if not text:
            return
        if text.startswith("/"):
            self.command(text)
            return
        if self.started_at:
            self.notify("Die Crew arbeitet noch. Esc bricht ab.", severity="warning")
            return
        self.add_line(f"› {escape(text)}", classes="user")
        self.run_worker(self.process(text), group="agent", exclusive=True)

    async def process(self, text: str):
        self.started_at = time.time()
        self.update_status()
        try:
            result = await self.session.send(text)
            self.add_markdown(result or "(keine Antwort)")
        except asyncio.CancelledError:
            self.add_line("[red]⏹ abgebrochen[/]")
            raise
        except Exception as e:
            self.add_line(f"[red]Fehler: {escape(f'{type(e).__name__}: {e}')}[/]")
        finally:
            took = int(time.time() - self.started_at)
            self.started_at = None
            self.update_status()
            self.notify(f"Fertig nach {took}s")
        if self.session.permissions.mode == "plan":
            self.push_screen(ChoiceScreen(
                "Plan umsetzen?", "Die Crew war im Plan-Modus (nur lesen). In welchem Modus soll sie den Plan umsetzen?",
                [("accept-edits", "Accept-Edits", "e", "primary"), ("auto", "Auto", "a", "warning"),
                 ("manual", "Manuell", "m", "default"), ("no", "Noch nicht", "n", "error")]),
                callback=self.after_plan)

    def after_plan(self, choice: str | None):
        if choice in MODES:
            self.session.permissions.set_mode(choice)
            self.update_status()
            self.add_line(f"Modus → [{MODE_STYLE[choice]}] {choice} [/]", classes="")
            self.run_worker(self.process("The plan is approved. Implement it now as planned."),
                            group="agent", exclusive=True)

    # ------------------------------------------------------------------ actions
    def action_cycle_mode(self):
        mode = self.session.permissions.cycle()
        self.update_status()
        self.notify(f"Modus: {mode}")

    def action_cancel(self):
        if self.started_at:
            self.workers.cancel_group(self, "agent")
            if self.pending_answer and not self.pending_answer.done():
                self.pending_answer.cancel()
            self.pending_answer = None
            self.prompt_input.placeholder = "Was soll die Crew bauen? (/help für Befehle)"
            self.node_stack = []

    # ------------------------------------------------------------------ slash commands
    def command(self, text: str):
        cmd, *args = text.split()
        s = self.session
        try:
            if cmd == "/help":
                self.add_markdown(HELP)
            elif cmd == "/mode":
                if args:
                    s.permissions.set_mode(args[0])
                self.update_status()
                self.add_line(f"Modus: [{MODE_STYLE[s.permissions.mode]}] {s.permissions.mode} [/]", classes="")
            elif cmd == "/model":
                if len(args) != 2:
                    raise ValueError("Nutzung: /model <agent|all> <provider:model>")
                s.set_model(args[0], args[1])
                self.update_status()
                self.add_line(f"{args[0]} nutzt jetzt [bold]{escape(args[1])}[/]", classes="")
            elif cmd == "/agents":
                rows = "\n".join(f"| {ICON[r]} {r} | `{s.spec_for(r)}` | {ROLES[r].summary} |" for r in ROLES)
                self.add_markdown(f"| Agent | Modell | Aufgabe |\n|---|---|---|\n{rows}")
            elif cmd == "/providers":
                from ..providers import BUILTIN, availability
                names = sorted(set(BUILTIN) | set(s.config.providers))
                rows = "\n".join(f"| {n} | {'✔' if ok else '✗'} {msg} |"
                                 for n in names for ok, msg in [availability(n, s.config.providers)])
                self.add_markdown(f"| Provider | Status |\n|---|---|\n{rows}")
            elif cmd == "/mcp":
                tools = s.mcp_tools
                self.add_markdown("\n".join(f"- `{t.name}`: {t.description}" for t in tools)
                                  or "Keine MCP-Tools geladen (in crew.toml unter `[mcp.<name>]` konfigurieren).")
            elif cmd == "/clear":
                s.reset()
                self.chat_log.remove_children()
                self.agent_tree.root.remove_children()
            elif cmd in ("/quit", "/exit"):
                self.exit()
            else:
                raise ValueError(f"Unbekannter Befehl {cmd}, siehe /help")
        except Exception as e:
            self.add_line(f"[red]{escape(str(e))}[/]")
