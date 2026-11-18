"""Modal dialogs for the TUI."""
from __future__ import annotations

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class ChoiceScreen(ModalScreen[str]):
    """Dialog with a scrollable body and a row of buttons. Each choice: (value, label, key, variant)."""

    DEFAULT_CSS = """
    ChoiceScreen { align: center middle; }
    #dialog { width: 90%; max-width: 120; height: auto; max-height: 85%;
              border: thick $warning; background: $surface; padding: 1 2; }
    #dialog-title { text-style: bold; color: $warning; margin-bottom: 1; }
    #dialog-body { height: auto; max-height: 30; border: round $panel; }
    #dialog-buttons { height: auto; margin-top: 1; align-horizontal: right; }
    #dialog-buttons Button { margin-left: 1; }
    """

    def __init__(self, title: str, body: str, choices: list[tuple[str, str, str, str]], lexer: str | None = None):
        super().__init__()
        self.title_text, self.body, self.choices, self.lexer = title, body, choices, lexer
        self.keys = {key: value for value, _, key, _ in choices}

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.title_text, id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                body = Syntax(self.body, self.lexer, word_wrap=True, theme="ansi_dark") if self.lexer else self.body
                yield Static(body, markup=False)
            with Horizontal(id="dialog-buttons"):
                for value, label, key, variant in self.choices:
                    yield Button(f"{label} ({key})", id=f"choice-{value}", variant=variant)

    def on_mount(self):
        self.query_one(Button).focus()

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id.removeprefix("choice-"))

    def on_key(self, event):
        if event.key in self.keys:
            event.stop()
            self.dismiss(self.keys[event.key])
        elif event.key == "escape":
            event.stop()
            self.dismiss(self.choices[-1][0])   # last choice = cancel / no


def approval_screen(title: str, preview: str) -> ChoiceScreen:
    is_diff = preview.lstrip("⚠ ").startswith("---") or "\n@@" in preview
    lexer = "diff" if is_diff else ("bash" if preview.startswith("$") else "json")
    return ChoiceScreen(f"Erlauben? {title}", preview,
                        [("yes", "Ja", "y", "success"), ("always", "Immer (Session)", "a", "primary"),
                         ("no", "Nein", "n", "error")], lexer)
