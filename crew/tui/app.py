"""Textual TUI for crew: a clean, terminal-like chat with the agent team."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Collapsible, Input, Markdown, OptionList, Static

from ..agents.roles import ROLES
from ..config import MODES, Config
from ..events import Event, UI
from ..models import PICKER_PROVIDERS, list_models
from ..providers import BUILTIN, availability
from ..providers.base import ProviderError
from ..session import Session
from .widgets import (ACCENT, BG, CREW_THEME, DIM, ERR, FG, LINE, PANEL, USER, WARN, Picker, PromptInput, row)

SPINNER = "✻✳✶✷✸✹✺✹✸✷✶✳"

MODE_INFO = {
    "manual": ("manual", "fragt vor jeder Änderung und jedem Befehl"),
    "accept-edits": ("⏵⏵ accept edits", "Dateien automatisch, Befehle auf Nachfrage"),
    "auto": ("⏵⏵ auto", "alles automatisch, gefährliche Befehle geblockt"),
    "plan": ("⏸ plan mode", "nur lesen und planen"),
}

HELP = """**Befehle**: `/` tippen zeigt alle Befehle, ↑↓ und Tab vervollständigen.

| Befehl | |
|---|---|
| `/agent` | Agent wählen → Provider (agy, codex, claude, ollama, lmstudio) → Modell |
| `/mode` | Modus wählen: manual · accept-edits · auto · plan |
| `/agents` | welcher Agent welches Modell nutzt |
| `/providers` | welche Provider gerade erreichbar sind |
| `/mcp` | geladene MCP-Tools |
| `/model <agent\\|all> <provider:model>` | Modell direkt setzen |
| `/clear` · `/quit` | Verlauf leeren · beenden |

**Tasten:** Shift+Tab Modus wechseln · Esc abbrechen · Ctrl+C beenden
"""

PLACEHOLDER = "Was soll die Crew bauen?  (/ für Befehle)"


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
        return await self.app.approve(agent, title, preview)


class CrewApp(App):
    TITLE = "crew"
    CSS = f"""
    Screen {{ background: {BG}; color: {FG}; }}
    #transcript {{ height: 1fr; padding: 1 2 0 2; scrollbar-size-vertical: 1; scrollbar-color: {PANEL}; }}
    #welcome {{ border: round {LINE}; padding: 0 1; width: auto; max-width: 100%; margin-bottom: 1; }}
    .msg {{ height: auto; margin-top: 1; }}
    .user {{ background: #2e2b22; color: {USER}; padding: 0 1; margin-top: 1; }}
    .who {{ height: auto; }}
    .body {{ padding-left: 2; margin: 0; background: {BG}; }}
    .line {{ height: auto; color: {DIM}; }}
    Markdown {{ background: {BG}; margin: 0; padding: 0 0 0 2; }}
    MarkdownBlock > .code_inline {{ color: {ACCENT}; background: {PANEL}; }}
    MarkdownBlock > .strong {{ color: {FG}; text-style: bold; }}
    Collapsible {{ border: none; background: {BG}; padding: 0; margin: 0; }}
    Collapsible > Contents {{ padding: 0 0 0 4; }}
    CollapsibleTitle {{ color: {DIM}; background: {BG}; padding: 0; }}
    CollapsibleTitle:hover, CollapsibleTitle:focus {{ background: {PANEL}; color: {FG}; }}
    #bottom {{ height: auto; dock: bottom; }}
    #activity {{ height: 1; padding: 0 2; display: none; }}
    #inputbox {{ height: 3; border: round {LINE}; margin: 0 1; padding: 0 1; background: {BG}; }}
    #caret {{ width: 2; color: {FG}; }}
    #prompt {{ border: none; height: 1; padding: 0; background: {BG}; }}
    #prompt:focus {{ border: none; background-tint: {BG} 0%; }}
    #prompt > .input--placeholder {{ color: {DIM}; }}
    #suggest {{ display: none; height: auto; max-height: 9; border: none; background: {BG}; margin: 0 2; padding: 0; }}
    #suggest:focus {{ border: none; }}
    OptionList > .option-list--option-highlighted {{ background: {PANEL}; }}
    OptionList:focus > .option-list--option-highlighted {{ background: {PANEL}; }}
    #statusbar {{ height: 1; padding: 0 2; }}
    #mode {{ width: 1fr; }}
    #model {{ width: auto; }}
    """
    BINDINGS = [
        Binding("shift+tab", "cycle_mode", show=False, priority=True),
        Binding("escape", "cancel", show=False),
        Binding("ctrl+c", "quit", show=False, priority=True),
    ]

    def __init__(self, workspace: Path, config: Config, mode: str | None = None):
        super().__init__()
        self.session = Session(workspace, config, TuiUI(self), mode=mode)
        self.pending_answer: asyncio.Future | None = None
        self.started_at: float | None = None
        self.stack: list[str] = []          # currently running agents (orchestrator → specialist)
        self.frame = 0

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="transcript")
        with Vertical(id="bottom"):
            yield Static(id="activity")
            yield Picker(id="picker")
            with Horizontal(id="inputbox"):
                yield Static("❯", id="caret")
                yield PromptInput(placeholder=PLACEHOLDER, id="prompt")
            yield OptionList(id="suggest")
            with Horizontal(id="statusbar"):
                yield Static(id="mode")
                yield Static(id="model")

    def on_mount(self):
        self.register_theme(CREW_THEME)
        self.theme = "crew"
        self.transcript = self.query_one("#transcript", VerticalScroll)
        self.prompt = self.query_one("#prompt", PromptInput)
        self.picker = self.query_one(Picker)
        self.activity = self.query_one("#activity", Static)
        self.prompt.focus()
        self.set_interval(0.15, self.tick)
        self.update_status()
        s = self.session
        home = str(Path.home())
        ws = str(s.workspace).replace(home, "~")
        cfg = str(s.config.path).replace(home, "~") if s.config.path else "keine crew.toml, Standardwerte"
        welcome = Text()
        welcome.append("✻ ", style=ACCENT)
        welcome.append("crew", style=f"bold {FG}")
        welcome.append("  ·  multi-agent coding cli\n\n", style=DIM)
        for key, desc in (("/agent", " Modelle wählen · "), ("/help", " Befehle · "), ("shift+tab", " Modus\n\n")):
            welcome.append(key if key != "/agent" else "  /agent", style=FG)
            welcome.append(desc, style=DIM)
        welcome.append(f"  workspace  {ws}\n  config     {cfg}\n  standard   {s.config.default}", style=DIM)
        self.transcript.mount(Static(welcome, id="welcome"))
        self.run_worker(self.session.start(), group="setup")

    # ------------------------------------------------------------------ transcript helpers
    def add(self, widget):
        self.transcript.mount(widget)
        self.transcript.scroll_end(animate=False)

    def add_line(self, text: Text, indent: int = 0):
        self.add(Static(Text(" " * indent) + text, classes="line"))

    def who(self, agent: str, extra: str = "", indent: int = 0) -> Text:
        t = Text(" " * indent)
        t.append("● ", style=FG)
        t.append(agent, style=f"bold {ACCENT}")
        if extra:
            t.append(f"  {extra}", style=DIM)
        return t

    def add_agent_message(self, agent: str, model: str, markdown: str):
        box = Vertical(classes="msg")
        self.add(box)
        box.mount(Static(self.who(agent, model), classes="who"))
        box.mount(Markdown(markdown or "_(keine Antwort)_"))
        self.transcript.scroll_end(animate=False)

    def add_user(self, text: str):
        self.add(Static(Text("❯ ", style=f"bold {USER}") + Text(text, style=USER), classes="user"))

    def depth(self) -> int:
        return max(len(self.stack) - 1, 0)

    # ------------------------------------------------------------------ status + spinner
    def update_status(self):
        s = self.session
        label, desc = MODE_INFO[s.permissions.mode]
        mode = Text()
        mode.append(label, style=f"bold {WARN if s.permissions.mode == 'auto' else ACCENT}")
        mode.append(f"  {desc}  ·  shift+tab", style=DIM)
        self.query_one("#mode", Static).update(mode)
        self.query_one("#model", Static).update(Text(f"orchestrator · {s.spec_for('orchestrator')}", style=DIM))

    def tick(self):
        if not self.started_at:
            self.activity.display = False
            return
        self.frame = (self.frame + 1) % len(SPINNER)
        who = self.stack[-1] if self.stack else "crew"
        waiting = self.pending_answer is not None or self.picker.active
        t = Text()
        t.append(f"{SPINNER[self.frame]} ", style=ACCENT)
        t.append(f"{who} " + ("wartet auf dich" if waiting else "arbeitet …"), style=FG)
        t.append(f"  ({int(time.time() - self.started_at)}s · esc zum Abbrechen)", style=DIM)
        self.activity.update(t)
        self.activity.display = True

    # ------------------------------------------------------------------ events from agents
    def on_crew_event(self, e: Event):
        if e.kind == "agent_start":
            self.stack.append(e.agent)
            if e.agent != "orchestrator":
                ind = 2 * (self.depth() - 1)
                self.add(Static(self.who(e.agent, e.data.get("model", ""), indent=ind), classes="msg who"))
                first = next((ln for ln in e.text.strip().splitlines() if ln.strip()), "")
                self.add_line(Text(f"⎿  {first[:110]}", style=DIM), indent=ind + 2)
        elif e.kind == "agent_end":
            if self.stack:
                self.stack.pop()
            if e.agent != "orchestrator":
                ind = 2 * self.depth()
                verdict = (e.text.strip().split() or [""])[0].strip("*:#")
                mark = {"APPROVED": "✔ approved", "CHANGES_REQUESTED": "✗ Änderungen gewünscht",
                        "PASS": "✔ pass", "FAIL": "✗ fail"}.get(verdict, "✔ fertig")
                c = Collapsible(Markdown(e.text or "_(leer)_"), title=f"⎿  {mark} · Antwort anzeigen", collapsed=True)
                c.styles.padding = (0, 0, 0, ind + 2)
                self.add(c)
        elif e.kind == "tool_call":
            if e.data.get("tool") != "delegate":
                self.add_line(Text(f"⎿  {e.text}", style=DIM), indent=2 * self.depth() + 2)
        elif e.kind == "tool_result":
            if not e.data.get("ok", True):
                self.add_line(Text(f"   ✗ {e.text[:200]}", style=ERR), indent=2 * self.depth() + 2)
        elif e.kind == "text":
            self.add_line(Text(e.text[:300], style=f"italic {DIM}"), indent=2 * self.depth() + 2)
        elif e.kind == "warn":
            self.add_line(Text(f"⚠ {e.text}", style=WARN))
        elif e.kind == "error":
            self.add_line(Text(f"✗ {e.agent}: {e.text}", style=ERR))
        elif e.kind == "info":
            self.add_line(Text(e.text, style=DIM))

    def ask(self, agent: str, question: str, fut: asyncio.Future):
        self.pending_answer = fut
        box = Vertical(classes="msg")
        self.add(box)
        box.mount(Static(self.who(agent, "fragt", indent=2 * self.depth()), classes="who"))
        box.mount(Markdown(question))
        self.prompt.placeholder = f"Antwort an {agent} …"
        self.prompt.focus()

    async def approve(self, agent: str, title: str, preview: str) -> str:
        tool = title.split("→")[-1].strip()
        lexer = "diff" if ("\n@@" in preview or preview.lstrip("⚠ ").startswith("---")) else (
            "bash" if preview.startswith("$") else "json")
        choice = await self.picker.choose(
            Text.assemble(("Erlauben? ", f"bold {FG}"), (agent, f"bold {ACCENT}"), (f" möchte {tool} ausführen", FG)),
            [("yes", row("Ja", width=4, number=1)),
             ("always", row("Ja, und in dieser Session nicht mehr fragen", width=4, number=2)),
             ("no", row("Nein, der Agent soll es anders machen", width=4, number=3))],
            body=preview[:8000], lexer=lexer, keys={"y": "yes", "a": "always", "n": "no"},
            hint="enter bestätigen · y / a / n · esc = nein")
        return choice or "no"

    # ------------------------------------------------------------------ input
    def on_picker_closed(self, _):
        # the next picker step may already be open (agent → provider → model); keep its focus then
        if not self.picker.active and not self.picker.display:
            self.prompt.focus()

    async def on_input_submitted(self, event: Input.Submitted):
        cmd = self.prompt.selected_command()
        text = event.value.strip()
        if cmd and text and not text.startswith(cmd):
            text = cmd                                    # enter on a highlighted suggestion
        self.prompt.value = ""
        self.query_one("#suggest").display = False
        if self.pending_answer is not None and not self.pending_answer.done():
            self.add_user(text or "(keine Antwort)")
            fut, self.pending_answer = self.pending_answer, None
            self.prompt.placeholder = PLACEHOLDER
            fut.set_result(text)
            return
        if not text:
            return
        if text.startswith("/"):
            await self.command(text)
            return
        if self.started_at:
            self.notify("Die Crew arbeitet noch. Esc bricht ab.", severity="warning")
            return
        self.add_user(text)
        self.run_worker(self.process(text), group="agent", exclusive=True)

    async def process(self, text: str):
        self.started_at = time.time()
        try:
            result = await self.session.send(text)
            self.add_agent_message("orchestrator", self.session.spec_for("orchestrator"), result)
            self.add_line(Text(f"✻ fertig nach {int(time.time() - self.started_at)}s", style=DIM))
        except asyncio.CancelledError:
            self.add_line(Text("⏹ abgebrochen", style=ERR))
            raise
        except Exception as e:
            self.add_line(Text(f"✗ Fehler: {type(e).__name__}: {e}", style=ERR))
        finally:
            self.started_at = None
            self.stack = []
            self.tick()
        if self.session.permissions.mode == "plan":
            choice = await self.picker.choose(
                Text("Plan umsetzen? In welchem Modus soll die Crew loslegen?", style=f"bold {FG}"),
                [(m, row(MODE_INFO[m][0], MODE_INFO[m][1], width=18, number=i))
                 for i, m in enumerate(["accept-edits", "auto", "manual"], start=1)]
                + [(None, row("Noch nicht", "im Plan-Modus bleiben", width=18, number=4))])
            if choice:
                self.session.permissions.set_mode(choice)
                self.update_status()
                self.add_user(f"Plan freigegeben ({choice})")
                await self.process("The plan is approved. Implement it now as planned.")

    # ------------------------------------------------------------------ actions
    def action_cycle_mode(self):
        if self.picker.active:
            return
        self.session.permissions.cycle()
        self.update_status()

    def action_cancel(self):
        if self.started_at:
            self.workers.cancel_group(self, "agent")
            if self.pending_answer and not self.pending_answer.done():
                self.pending_answer.cancel()
            self.pending_answer = None
            self.prompt.placeholder = PLACEHOLDER

    # ------------------------------------------------------------------ pickers
    async def pick_agent_model(self):
        s = self.session
        step, role, provider = "agent", None, None
        while True:
            target = "alle Agenten" if role == "all" else role
            if step == "agent":
                opts = [(r, row(r, s.spec_for(r), width=14, number=i)) for i, r in enumerate(ROLES, start=1)]
                opts.append(("all", row("alle Agenten", "gleiches Modell für alle", width=14, number=len(opts) + 1)))
                role = await self.picker.choose(Text("Welcher Agent?", style=f"bold {FG}"), opts,
                                                hint="↑↓ auswählen · enter weiter · esc schließen")
                if role is None:
                    return
                step = "provider"
            elif step == "provider":
                current = s.spec_for(role if role != "all" else "orchestrator").split(":")[0]
                names = PICKER_PROVIDERS + [n for n in s.config.providers if n not in PICKER_PROVIDERS]
                status = await asyncio.gather(*(asyncio.to_thread(availability, n, s.config.providers) for n in names))
                kinds = {"agy": "CLI · Antigravity", "codex": "CLI · OpenAI Codex", "claude": "CLI · Claude Code",
                         "ollama": "lokal · Ollama", "lmstudio": "lokal · LM Studio"}
                opts = []
                for i, (n, (ok, msg)) in enumerate(zip(names, status), start=1):
                    detail = kinds.get(n, "eigener Provider") + ("" if ok else f"  ✗ {msg}")
                    opts.append((n, row(n, detail, width=10, number=i, current=n == current)))
                provider = await self.picker.choose(
                    Text.assemble(("Provider für ", f"bold {FG}"), (target, f"bold {ACCENT}")), opts,
                    highlight=names.index(current) if current in names else 0)
                step = "model" if provider else "agent"
            elif step == "model":
                self.picker.show_loading(Text.assemble(("Modelle von ", f"bold {FG}"), (provider, f"bold {ACCENT}"),
                                                       (" werden geladen …", f"bold {FG}")))
                try:
                    models = await list_models(provider, s.config.providers)
                except (ProviderError, OSError, ValueError) as e:
                    self.notify(str(e), title=f"{provider} nicht verfügbar", severity="error")
                    step = "provider"
                    continue
                current = s.spec_for(role if role != "all" else "orchestrator")
                width = min(max(len(m) for m, _ in models), 28)
                opts = [(provider, row("Standard", f"Standardmodell von {provider}", width=width,
                                       current=current == provider))]
                opts += [(f"{provider}:{m}", row(m, d, width=width, current=current == f"{provider}:{m}"))
                         for m, d in models]
                spec = await self.picker.choose(
                    Text.assemble(("Modell für ", f"bold {FG}"), (target, f"bold {ACCENT}"), (f" · {provider}", DIM)),
                    opts)
                if spec is None:
                    step = "provider"
                    continue
                s.set_model(role, spec)
                self.update_status()
                self.add_line(Text.assemble(("✻ ", ACCENT), (target, f"bold {ACCENT}"), (" nutzt jetzt ", DIM),
                                            (spec, FG)))
                return

    async def pick_mode(self):
        cur = self.session.permissions.mode
        mode = await self.picker.choose(
            Text("Modus wählen", style=f"bold {FG}"),
            [(m, row(MODE_INFO[m][0], MODE_INFO[m][1], width=18, number=i, current=m == cur))
             for i, m in enumerate(MODES, start=1)], highlight=MODES.index(cur))
        if mode:
            self.session.permissions.set_mode(mode)
            self.update_status()

    # ------------------------------------------------------------------ slash commands
    async def command(self, text: str):
        cmd, *args = text.split()
        s = self.session
        if self.started_at and cmd in {"/agent", "/model", "/clear"}:
            self.notify("Während die Crew arbeitet nicht möglich.", severity="warning")
            return
        try:
            if cmd == "/help":
                self.add(Markdown(HELP, classes="msg"))
            elif cmd == "/mode":
                if args:
                    s.permissions.set_mode(args[0])
                    self.update_status()
                else:
                    self.run_worker(self.pick_mode(), group="ui", exclusive=True)
            elif cmd == "/agent" or (cmd == "/model" and not args):
                self.run_worker(self.pick_agent_model(), group="ui", exclusive=True)
            elif cmd == "/model":
                if len(args) != 2:
                    raise ValueError("Nutzung: /model <agent|all> <provider:model>, oder /agent zum Durchklicken")
                s.set_model(args[0], args[1])
                self.update_status()
                self.add_line(Text.assemble(("✻ ", ACCENT), (args[0], f"bold {ACCENT}"), (" nutzt jetzt ", DIM),
                                            (args[1], FG)))
            elif cmd == "/agents":
                rows = "\n".join(f"| **{r}** | `{s.spec_for(r)}` | {ROLES[r].summary} |" for r in ROLES)
                self.add(Markdown(f"| Agent | Modell | Aufgabe |\n|---|---|---|\n{rows}", classes="msg"))
            elif cmd == "/providers":
                names = sorted(set(BUILTIN) | set(s.config.providers))
                status = await asyncio.gather(*(asyncio.to_thread(availability, n, s.config.providers) for n in names))
                rows = "\n".join(f"| {n} | {'✔' if ok else '✗'} {msg} |" for n, (ok, msg) in zip(names, status))
                self.add(Markdown(f"| Provider | Status |\n|---|---|\n{rows}", classes="msg"))
            elif cmd == "/mcp":
                tools = s.mcp_tools
                self.add(Markdown("\n".join(f"- `{t.name}`: {t.description}" for t in tools)
                                  or "Keine MCP-Tools geladen (in `crew.toml` unter `[mcp.<name>]` konfigurieren).",
                                  classes="msg"))
            elif cmd == "/clear":
                s.reset()
                await self.transcript.remove_children()
            elif cmd in ("/quit", "/exit"):
                self.exit()
            else:
                raise ValueError(f"Unbekannter Befehl {cmd}. / zeigt alle Befehle.")
        except Exception as e:
            self.add_line(Text(str(e), style=ERR))
