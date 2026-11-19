"""Textual TUI for crew: a clean, terminal-like chat with the agent team.

Left: the conversation. Right: one live window per running sub-agent (task + activity).
Bottom: spinner line, picker (selections, approvals, questions), waiting messages, prompt, status line.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Collapsible, Input, Markdown, OptionList, Static

from .. import usage
from ..agents.roles import ROLES
from ..config import MODES, Config
from ..events import Event, UI
from ..models import PICKER_PROVIDERS, fallback_suggestions, list_models
from ..providers import BUILTIN, availability, provider_config
from ..providers.base import ProviderError
from ..session import Session
from .widgets import (ACCENT, BG, CREW_THEME, DIM, ERR, FG, LINE, OK, PANEL, USER, WARN, AgentPane, Picker,
                      PromptInput, row)

SPINNER = "✻✳✶✷✸✹✺✹✸✷✶✳"
CUSTOM = "__custom__"
MORE = "__more__"
PANE_LINGER = 8        # seconds a finished agent window stays visible

MODE_INFO = {
    "manual": ("manual", "fragt vor jeder Änderung und jedem Befehl"),
    "accept-edits": ("⏵⏵ accept edits", "Dateien automatisch, Befehle auf Nachfrage"),
    "auto": ("⏵⏵ auto", "alles automatisch, gefährliche Befehle geblockt"),
    "plan": ("⏸ plan mode", "nur lesen und planen"),
}

PROVIDER_KIND = {"agy": "CLI · Antigravity", "codex": "CLI · OpenAI Codex", "claude": "CLI · Claude Code",
                 "ollama": "lokal · Ollama", "lmstudio": "lokal · LM Studio"}

HELP = """**Befehle**: `/` tippen zeigt alle Befehle, ↑↓ und Tab vervollständigen.

| Befehl | |
|---|---|
| `/agent` | Agent wählen → Provider (agy, codex, claude, ollama, lmstudio) → Modell |
| `/usage` | CLI wählen → Verbrauch dieser Session, Abo und Limits |
| `/mode` | Modus wählen: manual · accept-edits · auto · plan |
| `/agents` | welcher Agent welches Modell nutzt |
| `/providers` | welche Provider gerade erreichbar sind |
| `/mcp` | geladene MCP-Tools |
| `/model <agent\\|all> <provider:model>` | Modell direkt setzen |
| `/model reset` | gespeicherte Modellwahl löschen |
| `/clear` · `/quit` | Verlauf leeren · beenden |

**Tasten:** ↑↓ Verlauf / wartende Nachricht bearbeiten · Shift+Tab Modus · Esc abbrechen · Ctrl+C beenden
"""

PLACEHOLDER = "Was soll die Crew bauen?  (/ für Befehle)"


class TuiUI(UI):
    def __init__(self, app: "CrewApp"):
        self.app = app

    def emit(self, event: Event):
        self.app.on_crew_event(event)

    async def ask_user(self, agent: str, question: str, options: list[str] | None = None) -> str:
        return await self.app.ask(agent, question, options or [])

    async def approve(self, agent: str, title: str, preview: str) -> str:
        return await self.app.approve(agent, title, preview)

    async def choose_fallback(self, agent: str, role: str, current: str, error: str) -> str | None:
        return await self.app.choose_fallback(agent, role, current, error)


class CrewApp(App):
    TITLE = "crew"
    CSS = f"""
    Screen {{ background: {BG}; color: {FG}; }}
    #main {{ height: 1fr; }}
    #transcript {{ width: 1fr; padding: 1 2 0 2; scrollbar-size-vertical: 1; scrollbar-color: {PANEL}; }}
    #panes {{ width: 46%; max-width: 90; display: none; padding: 1 1 0 0; scrollbar-size-vertical: 1;
              scrollbar-color: {PANEL}; }}
    #welcome {{ border: round {LINE}; padding: 0 1; width: auto; max-width: 100%; margin-bottom: 1; }}
    .msg {{ height: auto; margin-top: 1; }}
    .user {{ background: #2e2b22; color: {USER}; padding: 0 1; margin-top: 1; }}
    .who {{ height: auto; }}
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
    #queued {{ height: auto; padding: 0 2; display: none; }}
    #inputbox {{ height: 3; border: round {LINE}; margin: 0 1; padding: 0 1; background: {BG}; }}
    #caret {{ width: 2; color: {FG}; }}
    #prompt {{ border: none; height: 1; padding: 0; background: {BG}; }}
    #prompt:focus {{ border: none; background-tint: {BG} 0%; }}
    #prompt > .input--placeholder {{ color: {DIM}; }}
    #suggest {{ display: none; height: auto; max-height: 10; border: none; background: {BG}; margin: 0 2; padding: 0; }}
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

    def __init__(self, workspace: Path, config: Config, mode: str | None = None, use_saved: bool = True):
        super().__init__()
        self.session = Session(workspace, config, TuiUI(self), mode=mode, use_saved=use_saved)
        self.pending_answer: asyncio.Future | None = None
        self.started_at: float | None = None
        self.running: list[str] = []        # running sub-agent instance names
        self.panes: dict[str, AgentPane] = {}
        self.frame = 0
        self.history: list[str] = []        # submitted messages/commands, oldest first
        self.hist_idx: int | None = None    # position while browsing with ↑/↓
        self.draft = ""                     # what was typed before browsing started
        self.queue: list[str] = []          # messages sent while the crew was busy
        self.ui_lock = asyncio.Lock()       # one question / approval / picker at a time (parallel agents)

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield VerticalScroll(id="transcript")
            yield VerticalScroll(id="panes")
        with Vertical(id="bottom"):
            yield Static(id="activity")
            yield Picker(id="picker")
            yield Static(id="queued")
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
        self.pane_box = self.query_one("#panes", VerticalScroll)
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
        for key, desc in (("  /agent", " Modelle wählen · "), ("/usage", " Verbrauch · "), ("/help", " Befehle · "),
                          ("shift+tab", " Modus\n\n")):
            welcome.append(key, style=FG)
            welcome.append(desc, style=DIM)
        welcome.append(f"  workspace  {ws}\n  config     {cfg}\n  standard   {s.config.default}", style=DIM)
        if s.saved_models:
            saved = ", ".join(f"{r} → {spec}" for r, spec in s.saved_models.items())
            welcome.append(f"\n  gespeichert {saved}", style=DIM)
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
        now = time.time()
        for pane in self.panes.values():
            if pane.started and not pane.has_class("done"):
                pane.border_subtitle = f"{pane.model} · {int(now - pane.started)}s"
        if not self.started_at:
            self.activity.display = False
            return
        self.frame = (self.frame + 1) % len(SPINNER)
        who = ", ".join(self.running) if self.running else "orchestrator"
        waiting = self.pending_answer is not None or self.picker.active
        verb = "wartet auf dich" if waiting else ("arbeiten …" if len(self.running) > 1 else "arbeitet …")
        t = Text()
        t.append(f"{SPINNER[self.frame]} ", style=ACCENT)
        t.append(f"{who} {verb}", style=FG)
        t.append(f"  ({int(now - self.started_at)}s · esc zum Abbrechen)", style=DIM)
        self.activity.update(t)
        self.activity.display = True

    # ------------------------------------------------------------------ agent windows (right side)
    def open_pane(self, agent: str, model: str, task: str):
        old = self.panes.pop(agent, None)
        if old is not None:
            old.remove()
        pane = AgentPane(agent, model, task)
        pane.started = time.time()
        self.panes[agent] = pane
        self.pane_box.display = True
        self.pane_box.mount(pane)
        self.pane_box.scroll_end(animate=False)

    def close_pane(self, agent: str, label: str, ok: bool):
        pane = self.panes.get(agent)
        if pane is None:
            return
        pane.finish(label, ok)

        def drop():
            if self.panes.get(agent) is pane:
                del self.panes[agent]
                pane.remove()
            if not self.panes:
                self.pane_box.display = False
        self.set_timer(PANE_LINGER, drop)

    def pane_log(self, agent: str, text: Text) -> bool:
        pane = self.panes.get(agent)
        if pane is not None:
            pane.log_line(text)
            return True
        return False

    # ------------------------------------------------------------------ events from agents
    def on_crew_event(self, e: Event):
        sub = e.agent != "orchestrator"
        if e.kind == "agent_start":
            if not sub:
                return
            self.running.append(e.agent)
            model = e.data.get("model", "")
            self.add(Static(self.who(e.agent, model), classes="msg who"))
            first = next((ln for ln in e.text.strip().splitlines() if ln.strip()), "")
            self.add_line(Text(f"⎿  {first[:110]}", style=DIM), indent=2)
            self.open_pane(e.agent, model, e.text)
        elif e.kind == "agent_end":
            if not sub:
                return
            if e.agent in self.running:
                self.running.remove(e.agent)
            verdict = (e.text.strip().split() or [""])[0].strip("*:#")
            mark, ok = {"APPROVED": ("✔ approved", True), "CHANGES_REQUESTED": ("✗ Änderungen gewünscht", False),
                        "PASS": ("✔ pass", True), "FAIL": ("✗ fail", False)}.get(verdict, ("✔ fertig", True))
            if e.text.startswith("ERROR"):
                mark, ok = "✗ Fehler", False
            self.add(Collapsible(Markdown(e.text or "_(leer)_"), title=f"⎿  {e.agent}: {mark} · Antwort anzeigen",
                                 collapsed=True))
            self.close_pane(e.agent, mark, ok)
        elif e.kind == "tool_call":
            if e.data.get("tool") in ("delegate", "ask_user"):      # shown as agent window / question instead
                return
            line = Text(f"⎿  {e.text}", style=DIM)
            if not (sub and self.pane_log(e.agent, line)):
                self.add_line(line, indent=2)
        elif e.kind == "tool_result":
            if not e.data.get("ok", True):
                line = Text(f"   ✗ {e.text[:200]}", style=ERR)
                if not (sub and self.pane_log(e.agent, line)):
                    self.add_line(line, indent=2)
        elif e.kind == "text":
            line = Text(e.text[:300], style=f"italic {DIM}")
            if not (sub and self.pane_log(e.agent, line)):
                self.add_line(line, indent=2)
        elif e.kind in ("warn", "error", "info"):
            style = {"warn": WARN, "error": ERR, "info": DIM}[e.kind]
            icon = {"warn": "⚠ ", "error": "✗ ", "info": ""}[e.kind]
            if sub:
                self.pane_log(e.agent, Text(icon + e.text, style=style))
            prefix = f"{e.agent}: " if e.agent not in ("crew",) else ""
            self.add_line(Text(f"{icon}{prefix}{e.text}", style=style))

    # ------------------------------------------------------------------ questions, approvals, limits
    async def ask(self, agent: str, question: str, options: list[str]) -> str:
        async with self.ui_lock:
            box = Vertical(classes="msg")
            self.add(box)
            box.mount(Static(self.who(agent, "fragt"), classes="who"))
            box.mount(Markdown(question))
            if options:
                opts = [(o, row(o, width=0, number=i)) for i, o in enumerate(options, start=1)]
                opts.append((CUSTOM, row("✎ Eigene Antwort schreiben …", width=0, number=len(opts) + 1)))
                choice = await self.picker.choose(
                    Text.assemble((agent, f"bold {ACCENT}"), (" fragt: ", DIM), (question, f"bold {FG}")), opts,
                    hint="↑↓ auswählen · enter bestätigen · Ziffer wählt direkt · esc = keine Antwort")
                if choice is None:
                    self.add_user("(keine Antwort, Agent nimmt den Standard)")
                    return ""
                if choice != CUSTOM:
                    self.add_user(choice)
                    return choice
            fut = asyncio.get_running_loop().create_future()
            self.pending_answer = fut
            self.prompt.placeholder = f"Antwort an {agent} …"
            self.prompt.focus()
            return await fut

    async def approve(self, agent: str, title: str, preview: str) -> str:
        async with self.ui_lock:
            tool = title.split("→")[-1].strip()
            lexer = "diff" if ("\n@@" in preview or preview.lstrip("⚠ ").startswith("---")) else (
                "bash" if preview.startswith("$") else "json")
            choice = await self.picker.choose(
                Text.assemble(("Erlauben? ", f"bold {FG}"), (agent, f"bold {ACCENT}"),
                              (f" möchte {tool} ausführen", FG)),
                [("yes", row("Ja", width=4, number=1)),
                 ("always", row("Ja, und in dieser Session nicht mehr fragen", width=4, number=2)),
                 ("no", row("Nein, der Agent soll es anders machen", width=4, number=3))],
                body=preview[:8000], lexer=lexer, keys={"y": "yes", "a": "always", "n": "no"},
                hint="enter bestätigen · y / a / n · esc = nein")
            return choice or "no"

    async def choose_fallback(self, agent: str, role: str, current: str, error: str) -> str | None:
        async with self.ui_lock:
            self.picker.show_loading(Text.assemble(("⚠ ", WARN), (current, f"bold {FG}"),
                                                   (" ist im Usage-Limit. Suche Alternativen …", FG)))
            options = await fallback_suggestions(current, self.session.config.providers)
            width = max([len(s) for s, _ in options] + [10])
            opts = [(spec, row(spec, desc, width=min(width, 32), number=i)) for i, (spec, desc) in
                    enumerate(options, start=1)]
            opts.append((MORE, row("Anderes Modell auswählen …", width=0, number=len(opts) + 1)))
            opts.append((None, row("Nein, abbrechen", "der Agent bricht mit Fehler ab", width=0,
                                   number=len(opts) + 1)))
            while True:
                spec = await self.picker.choose(
                    Text.assemble(("⚠ ", WARN), (agent, f"bold {ACCENT}"), (": ", FG), (current, f"bold {FG}"),
                                  (" hat das Usage-Limit erreicht. Anderes Modell nehmen?", FG)),
                    opts, body=error[:600], hint="enter übernehmen · esc = abbrechen")
                if spec != MORE:
                    break
                spec = await self.pick_provider_model(role)
                if spec:
                    break
            if spec:
                self.add_line(Text.assemble(("✻ ", ACCENT), (agent, f"bold {ACCENT}"), (" wechselt wegen Limit zu ", DIM),
                                            (spec, FG)))
                self.update_status()
            return spec

    # ------------------------------------------------------------------ history + queue
    def update_queue_view(self):
        view = self.query_one("#queued", Static)
        if not self.queue:
            view.display = False
            return
        t = Text()
        for i, msg in enumerate(self.queue):
            t.append("⏸ wartet  ", style=DIM)
            t.append(msg if len(msg) < 90 else msg[:89] + "…", style=USER)
            if i == len(self.queue) - 1:
                t.append("   ↑ zum Bearbeiten", style=DIM)
            if i < len(self.queue) - 1:
                t.append("\n")
        view.update(t)
        view.display = True

    def history_prev(self):
        """↑: first pull back a queued message for editing, otherwise go back in the history."""
        if self.queue and self.hist_idx is None and not self.prompt.value.strip():
            self.prompt.set_text(self.queue.pop())
            self.update_queue_view()
            return
        if not self.history:
            return
        if self.hist_idx is None:
            self.draft = self.prompt.value
            self.hist_idx = len(self.history) - 1
        elif self.hist_idx > 0:
            self.hist_idx -= 1
        self.prompt.set_text(self.history[self.hist_idx])

    def history_next(self):
        """↓: forward in the history; past the newest entry the unsent draft comes back."""
        if self.hist_idx is None:
            return
        self.hist_idx += 1
        if self.hist_idx >= len(self.history):
            self.hist_idx = None
            self.prompt.set_text(self.draft)
        else:
            self.prompt.set_text(self.history[self.hist_idx])

    def remember(self, text: str):
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self.hist_idx, self.draft = None, ""

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
            self.prompt.placeholder = PLACEHOLDER if not self.started_at else self.busy_placeholder()
            fut.set_result(text)
            return
        if not text:
            return
        self.remember(text)
        if text.startswith("/"):
            await self.command(text)
            return
        if self.started_at:
            self.queue.append(text)               # runs automatically when the crew is done
            self.update_queue_view()
            return
        self.add_user(text)
        self.run_worker(self.run_queue(text), group="agent", exclusive=True)

    @staticmethod
    def busy_placeholder() -> str:
        return "Nächste Nachricht? Wird nach dem aktuellen Auftrag ausgeführt"

    async def run_queue(self, text: str):
        """Process `text`, then every message that was queued meanwhile, in order."""
        while text:
            await self.process(text)
            text = self.queue.pop(0) if self.queue else None
            self.update_queue_view()
            if text:
                self.add_user(text)

    async def process(self, text: str):
        self.started_at = time.time()
        self.prompt.placeholder = self.busy_placeholder()
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
            self.running = []
            self.prompt.placeholder = PLACEHOLDER
            self.tick()
        if self.session.permissions.mode == "plan":
            async with self.ui_lock:
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
            for name in list(self.panes):
                self.close_pane(name, "abgebrochen", False)
            self.running = []

    # ------------------------------------------------------------------ pickers
    async def provider_options(self, current: str | None) -> tuple[list, list[str]]:
        s = self.session
        names = PICKER_PROVIDERS + [n for n in s.config.providers if n not in PICKER_PROVIDERS]
        status = await asyncio.gather(*(asyncio.to_thread(availability, n, s.config.providers) for n in names))
        opts = []
        for i, (n, (ok, msg)) in enumerate(zip(names, status), start=1):
            detail = PROVIDER_KIND.get(n, "eigener Provider") + ("" if ok else f"  ✗ {msg}")
            opts.append((n, row(n, detail, width=10, number=i, current=n == current)))
        return opts, names

    async def pick_provider_model(self, role: str) -> str | None:
        """Provider → model steps for `role` ("all" = every agent). Returns a spec or None (esc)."""
        s = self.session
        target = "alle Agenten" if role == "all" else role
        current_spec = s.spec_for(role if role != "all" else "orchestrator")
        while True:
            opts, names = await self.provider_options(current_spec.split(":")[0])
            current = current_spec.split(":")[0]
            provider = await self.picker.choose(
                Text.assemble(("Provider für ", f"bold {FG}"), (target, f"bold {ACCENT}")), opts,
                highlight=names.index(current) if current in names else 0)
            if provider is None:
                return None
            self.picker.show_loading(Text.assemble(("Modelle von ", f"bold {FG}"), (provider, f"bold {ACCENT}"),
                                                   (" werden geladen …", f"bold {FG}")))
            try:
                models = await list_models(provider, s.config.providers)
            except (ProviderError, OSError, ValueError) as e:
                self.notify(str(e), title=f"{provider} nicht verfügbar", severity="error")
                continue
            width = min(max(len(m) for m, _ in models), 28)
            opts = [(provider, row("Standard", f"Standardmodell von {provider}", width=width,
                                   current=current_spec == provider))]
            opts += [(f"{provider}:{m}", row(m, d, width=width, current=current_spec == f"{provider}:{m}"))
                     for m, d in models]
            spec = await self.picker.choose(
                Text.assemble(("Modell für ", f"bold {FG}"), (target, f"bold {ACCENT}"), (f" · {provider}", DIM)),
                opts)
            if spec is not None:
                return spec

    async def pick_agent_model(self):
        s = self.session
        async with self.ui_lock:
            while True:
                opts = [(r, row(r, s.spec_for(r), width=14, number=i)) for i, r in enumerate(ROLES, start=1)]
                opts.append(("all", row("alle Agenten", "gleiches Modell für alle", width=14,
                                        number=len(opts) + 1)))
                role = await self.picker.choose(Text("Welcher Agent?", style=f"bold {FG}"), opts,
                                                hint="↑↓ auswählen · enter weiter · esc schließen")
                if role is None:
                    return
                spec = await self.pick_provider_model(role)
                if spec is None:
                    continue                        # esc on provider step → back to agent list
                s.set_model(role, spec, persist=True)
                self.update_status()
                target = "alle Agenten" if role == "all" else role
                self.add_line(Text.assemble(("✻ ", ACCENT), (target, f"bold {ACCENT}"), (" nutzt jetzt ", DIM),
                                            (spec, FG), ("  (gespeichert)", DIM)))
                return

    async def pick_mode(self):
        cur = self.session.permissions.mode
        async with self.ui_lock:
            mode = await self.picker.choose(
                Text("Modus wählen", style=f"bold {FG}"),
                [(m, row(MODE_INFO[m][0], MODE_INFO[m][1], width=18, number=i, current=m == cur))
                 for i, m in enumerate(MODES, start=1)], highlight=MODES.index(cur))
        if mode:
            self.session.permissions.set_mode(mode)
            self.update_status()

    async def pick_usage(self):
        s = self.session
        names = PICKER_PROVIDERS + [n for n in usage.USAGE if n not in PICKER_PROVIDERS]
        async with self.ui_lock:
            opts = [(n, row(n, f"{PROVIDER_KIND.get(n, 'Provider')} · {usage.summary(n)}", width=10, number=i))
                    for i, n in enumerate(names, start=1)]
            name = await self.picker.choose(Text("Usage: welche CLI?", style=f"bold {FG}"), opts,
                                            hint="↑↓ auswählen · enter anzeigen · esc schließen")
            if name is None:
                return
            self.picker.show_loading(Text.assemble(("Usage von ", f"bold {FG}"), (name, f"bold {ACCENT}"),
                                                   (" wird abgefragt …", f"bold {FG}")))
            try:
                ptype = provider_config(name, s.config.providers)["type"]
            except ProviderError:
                ptype = "?"
            account = await usage.account_info(name, ptype)
            self.picker.display = False
            self.prompt.focus()
        md = [f"**Usage · {name}**", "", *account, "", *usage.session_table(name)]
        self.add(Markdown("\n".join(md), classes="msg"))

    # ------------------------------------------------------------------ slash commands
    async def command(self, text: str):
        cmd, *args = text.split()
        s = self.session
        picker_cmds = {"/agent", "/usage", "/mode"} | ({"/model"} if not args else set())
        if cmd in picker_cmds and (self.ui_lock.locked() or self.picker.active):
            self.notify("Erst die offene Auswahl beantworten.", severity="warning")
            return
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
            elif cmd == "/usage":
                self.run_worker(self.pick_usage(), group="ui", exclusive=True)
            elif cmd == "/model" and args == ["reset"]:
                s.forget_models()
                self.update_status()
                self.add_line(Text.assemble(("✻ ", ACCENT), ("Gespeicherte Modellwahl gelöscht, ", DIM),
                                            (f"alle Agenten nutzen wieder crew.toml / {s.config.default}", FG)))
            elif cmd == "/model":
                if len(args) != 2:
                    raise ValueError("Nutzung: /model <agent|all> <provider:model>, oder /agent zum Durchklicken")
                s.set_model(args[0], args[1], persist=True)
                self.update_status()
                self.add_line(Text.assemble(("✻ ", ACCENT), (args[0], f"bold {ACCENT}"), (" nutzt jetzt ", DIM),
                                            (args[1], FG), ("  (gespeichert)", DIM)))
            elif cmd == "/agents":
                rows = "\n".join(f"| **{r}** | `{s.spec_for(r)}`{' 💾' if r in s.saved_models else ''} | "
                                 f"{ROLES[r].summary} |" for r in ROLES)
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
