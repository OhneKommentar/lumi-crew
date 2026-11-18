"""TUI smoke tests with Textual's Pilot and a scripted provider."""
from crew.config import Config
from crew.providers.base import Provider, Reply, ToolCall
from crew.tui.app import CrewApp
from crew.tui.widgets import ChoiceScreen


class Scripted(Provider):
    def __init__(self, script):
        super().__init__("fake", "fake")
        self.script = script

    async def complete(self, system, messages, tools):
        role = system.split("**")[1].lower()
        return self.script[role].pop(0)


def app_with(tmp_path, script, mode="manual"):
    app = CrewApp(tmp_path, Config(), mode)
    fake = Scripted(script)
    app.session.provider_for = lambda role: fake
    return app


async def test_shift_tab_cycles_mode(tmp_path):
    app = app_with(tmp_path, {})
    async with app.run_test() as pilot:
        await pilot.press("shift+tab")
        assert app.session.permissions.mode == "accept-edits"
        await pilot.press("shift+tab", "shift+tab")
        assert app.session.permissions.mode == "plan"


async def test_manual_mode_shows_approval_and_writes(tmp_path):
    script = {
        "orchestrator": [Reply("", [ToolCall("1", "delegate", {"agent": "coder", "task": "t"})]), Reply("fertig")],
        "coder": [Reply("", [ToolCall("2", "write_file", {"path": "a.txt", "content": "hi\n"})]), Reply("done")],
    }
    app = app_with(tmp_path, script)
    async with app.run_test() as pilot:
        await pilot.click("#prompt")
        await pilot.press(*"go", "enter")
        for _ in range(50):
            await pilot.pause(0.05)
            if isinstance(app.screen, ChoiceScreen):
                break
        assert isinstance(app.screen, ChoiceScreen)
        await pilot.press("y")
        for _ in range(50):
            await pilot.pause(0.05)
            if app.started_at is None:
                break
        assert (tmp_path / "a.txt").read_text() == "hi\n"


async def test_clarifier_question_answered_via_input(tmp_path):
    script = {
        "orchestrator": [Reply("", [ToolCall("1", "ask_user", {"question": "Welche Sprache?"})]), Reply("ok")],
    }
    app = app_with(tmp_path, script)
    async with app.run_test() as pilot:
        await pilot.press(*"bau was", "enter")
        for _ in range(50):
            await pilot.pause(0.05)
            if app.pending_answer:
                break
        assert app.pending_answer is not None
        await pilot.press(*"Python", "enter")
        for _ in range(50):
            await pilot.pause(0.05)
            if app.started_at is None:
                break
        tool_msgs = [m.content for m in app.session.orchestrator.messages if m.role == "tool"]
        assert tool_msgs == ["User answered: Python"]


async def test_slash_commands(tmp_path):
    app = app_with(tmp_path, {})
    async with app.run_test() as pilot:
        await pilot.press(*"/mode auto", "enter")
        assert app.session.permissions.mode == "auto"
        await pilot.press(*"/model coder claude:haiku", "enter")
        assert app.session.spec_for("coder") == "claude:haiku"
