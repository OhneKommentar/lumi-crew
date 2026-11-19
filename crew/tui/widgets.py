"""Widgets for the crew TUI: inline picker (also used for approvals) and the prompt input with autocomplete."""
from __future__ import annotations

import asyncio

from rich.syntax import Syntax
from rich.text import Text
from textual.actions import SkipAction
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.theme import Theme
from textual.widgets import Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option

# ----------------------------------------------------------------------------- palette
BG = "#1f232b"        # blue-grey background
PANEL = "#2a2f39"     # highlighted rows
LINE = "#c8cdd6"      # thin white-ish lines
FG = "#e3e6eb"
DIM = "#8a919d"
ACCENT = "#9fb6d6"    # soft blue
USER = "#e9c46a"      # own messages
OK = "#9cc49c"
ERR = "#e08c8c"
WARN = "#e0c48c"

CREW_THEME = Theme(
    name="crew", dark=True,
    primary=ACCENT, secondary=DIM, accent=ACCENT, foreground=FG,
    background=BG, surface=BG, panel=PANEL, boost=PANEL,
    success=OK, error=ERR, warning=WARN,
)

COMMANDS = [
    ("/agent", "Modell für einen Agenten wählen"),
    ("/mode", "Modus wählen (auch Shift+Tab)"),
    ("/usage", "Verbrauch und Limits einer CLI anzeigen"),
    ("/agents", "Übersicht: Agenten und ihre Modelle"),
    ("/providers", "verfügbare Provider prüfen"),
    ("/mcp", "geladene MCP-Tools"),
    ("/resume", "frühere Chats anzeigen und fortsetzen"),
    ("/new", "neuen Chat beginnen"),
    ("/help", "Hilfe"),
    ("/quit", "crew beenden"),
]


def row(label: str, detail: str = "", width: int = 14, number: int | None = None, current: bool = False) -> Text:
    t = Text()
    if number is not None:
        t.append(f"{number}. ", style=DIM)
    t.append(label.ljust(width), style=f"bold {FG}" if current else FG)
    if detail:
        t.append("  " + detail, style=DIM)
    if current:
        t.append("  ✔", style=ACCENT)
    return t


# ----------------------------------------------------------------------------- picker
class Picker(Vertical):
    """Inline selection list above the prompt. `await picker.choose(...)` returns the chosen value or None."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    DEFAULT_CSS = f"""
    Picker {{ display: none; height: auto; border: round {LINE}; padding: 0 1; margin: 0 1; background: {BG}; }}
    Picker #picker-title {{ color: {FG}; text-style: bold; }}
    Picker #picker-body {{ height: auto; max-height: 22; margin: 1 0; background: {BG}; }}
    Picker OptionList {{ height: auto; max-height: 14; border: none; background: {BG}; padding: 0; margin-top: 1; }}
    Picker OptionList:focus {{ border: none; background-tint: {BG} 0%; }}
    Picker #picker-hint {{ color: {DIM}; margin-top: 1; }}
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self._fut: asyncio.Future | None = None
        self._values: list = []
        self._keys: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        yield Static(id="picker-title")
        with VerticalScroll(id="picker-body"):
            yield Static(id="picker-body-text")
        yield OptionList(id="picker-list")
        yield Static(id="picker-hint")

    @property
    def active(self) -> bool:
        return self._fut is not None and not self._fut.done()

    def show_loading(self, title: str):
        self.query_one("#picker-title", Static).update(title)
        self.query_one("#picker-body").display = False
        lst = self.query_one(OptionList)
        lst.clear_options()
        lst.add_option(Option(Text("lädt …", style=DIM), disabled=True))
        self.query_one("#picker-hint", Static).update("esc abbrechen")
        self.display = True

    async def choose(self, title: str, options: list[tuple[object, Text | str]], *, body: str | None = None,
                     lexer: str | None = None, keys: dict[str, object] | None = None, highlight: int = 0,
                     hint: str = "↑↓ auswählen · enter bestätigen · esc zurück"):
        if self.active:
            self._fut.set_result(None)
        self._fut = asyncio.get_running_loop().create_future()
        self._values = [v for v, _ in options]
        self._keys = dict(keys or {})
        for i, v in enumerate(self._values[:9], start=1):
            self._keys.setdefault(str(i), v)
        self.query_one("#picker-title", Static).update(title)
        body_box = self.query_one("#picker-body")
        body_box.display = body is not None
        if body is not None:
            content = Syntax(body, lexer, theme="nord", background_color=BG, word_wrap=True) if lexer else body
            self.query_one("#picker-body-text", Static).update(content)
            body_box.scroll_home(animate=False)
        lst = self.query_one(OptionList)
        lst.clear_options()
        lst.add_options([Option(label, id=str(i)) for i, (_, label) in enumerate(options)])
        lst.highlighted = min(highlight, len(options) - 1)
        self.query_one("#picker-hint", Static).update(hint)
        self.display = True
        lst.focus()
        return await self._fut

    def _resolve(self, value):
        if self._fut and not self._fut.done():
            self._fut.set_result(value)
        self.display = False
        self.post_message(self.Closed())

    class Closed(Message):
        """Posted when the picker hides, so the app can refocus the prompt."""

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        event.stop()
        self._resolve(self._values[int(event.option.id)])

    def on_key(self, event):
        if self.active and event.key in self._keys:
            event.stop()
            self._resolve(self._keys[event.key])

    def action_cancel(self):
        if self.active:
            self._resolve(None)
        else:
            raise SkipAction()


# ----------------------------------------------------------------------------- prompt input
class PromptInput(Input):
    """Input with slash-command autocomplete. The suggestion list lives in the app (#suggest)."""

    BINDINGS = [
        Binding("down", "suggest(1)", show=False),
        Binding("up", "suggest(-1)", show=False),
        Binding("tab", "complete", show=False),
        Binding("escape", "close_suggest", show=False),
    ]

    @property
    def suggest_list(self) -> OptionList:
        return self.app.query_one("#suggest", OptionList)

    def matches(self, value: str) -> list[tuple[str, str]]:
        if not value.startswith("/") or " " in value:
            return []
        return [c for c in COMMANDS if c[0].startswith(value.lower())]

    def refresh_suggestions(self):
        lst = self.suggest_list
        found = self.matches(self.value)
        lst.clear_options()
        if not found:
            lst.display = False
            return
        lst.add_options([Option(row(cmd, desc, width=12), id=cmd) for cmd, desc in found])
        lst.highlighted = 0
        lst.display = True

    def on_input_changed(self, event: Input.Changed):
        self.refresh_suggestions()

    def selected_command(self) -> str | None:
        lst = self.suggest_list
        if lst.display and lst.option_count and lst.highlighted is not None:
            return lst.get_option_at_index(lst.highlighted).id
        return None

    def action_suggest(self, step: int):
        lst = self.suggest_list
        if not lst.display or not lst.option_count:
            # no suggestions open: ↑/↓ walk through queued + previous messages (handled by the app)
            if step < 0:
                self.app.history_prev()
            else:
                self.app.history_next()
            return
        lst.highlighted = ((lst.highlighted or 0) + step) % lst.option_count

    def set_text(self, text: str):
        """Replace the content without opening the suggestion list for recalled /commands."""
        with self.prevent(Input.Changed):
            self.value = text
        self.cursor_position = len(text)
        self.suggest_list.display = False

    def action_complete(self):
        cmd = self.selected_command()
        if cmd is None:
            raise SkipAction()
        self.value = cmd + " "
        self.cursor_position = len(self.value)
        self.suggest_list.display = False

    def action_close_suggest(self):
        if self.suggest_list.display:
            self.suggest_list.display = False
        else:
            raise SkipAction()


# ----------------------------------------------------------------------------- agent window (right side)
class AgentPane(Vertical):
    """Live window for one running sub-agent: the task it was given and what it is doing.

    Running windows share the right column equally (height 1fr each); finished windows shrink to a
    compact summary while they linger, so the running ones keep the space."""

    DEFAULT_CSS = f"""
    AgentPane {{ height: 1fr; min-height: 8; border: round {LINE}; border-title-color: {FG};
                 border-subtitle-color: {DIM}; padding: 0 1; margin: 0 0 1 0; background: {BG}; }}
    AgentPane.done {{ height: 3; min-height: 3; border: round {DIM}; }}
    AgentPane.done .pane-label, AgentPane.done .pane-task {{ display: none; }}
    AgentPane.done .pane-logbox {{ height: 1; scrollbar-size-vertical: 0; }}
    AgentPane .pane-label {{ color: {DIM}; height: 1; }}
    AgentPane .pane-task {{ height: auto; max-height: 45%; background: {BG}; }}
    AgentPane .pane-task Markdown {{ padding: 0; margin: 0; }}
    AgentPane .pane-logbox {{ height: 1fr; background: {BG}; scrollbar-size-vertical: 1; scrollbar-color: {PANEL}; }}
    AgentPane .pane-log {{ height: auto; color: {DIM}; }}
    """

    MAX_LINES = 300

    def __init__(self, agent: str, model: str, task: str, **kw):
        super().__init__(**kw)
        self.agent, self.model, self.task_text = agent, model, task
        self.lines: list[Text] = []
        self.started = 0.0

    def compose(self) -> ComposeResult:
        yield Static("Auftrag", classes="pane-label")
        with VerticalScroll(classes="pane-task"):
            yield Markdown(self.task_text or "_(leer)_")
        yield Static("Aktivität", classes="pane-label")
        with VerticalScroll(classes="pane-logbox"):
            yield Static(Text("startet …", style=DIM), classes="pane-log")

    def on_mount(self):
        self.border_title = Text.assemble(("● ", FG), (self.agent, f"bold {ACCENT}"))
        self.border_subtitle = self.model
        self._render_log()

    def log_line(self, text: Text):
        self.lines.append(text)
        self.lines = self.lines[-self.MAX_LINES:]
        self._render_log()

    def _render_log(self):
        logs = self.query(".pane-log")           # may not be composed yet right after mount()
        if logs and self.lines:
            logs.first(Static).update(Text("\n").join(self.lines))
            box = self.query(".pane-logbox")
            if box:
                box.first().scroll_end(animate=False)   # newest activity stays visible

    def finish(self, label: str, ok: bool = True):
        self.add_class("done")
        self.border_title = Text.assemble(("✔ " if ok else "✗ ", OK if ok else ERR), (self.agent, f"bold {DIM}"))
        self.border_subtitle = label
