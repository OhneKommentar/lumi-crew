"""TUI tests with Textual's Pilot and a scripted provider."""
import crew.tui.app as tui_app
from crew.config import Config
from crew.providers.base import Provider, Reply, ToolCall
from crew.tui.app import CrewApp


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


async def wait_for(pilot, cond, steps=100):
    for _ in range(steps):
        await pilot.pause(0.05)
        if cond():
            return True
    return False


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
        await pilot.press(*"go", "enter")
        assert await wait_for(pilot, lambda: app.picker.active)
        await pilot.press("y")
        assert await wait_for(pilot, lambda: app.started_at is None)
        assert (tmp_path / "a.txt").read_text() == "hi\n"


async def test_approval_escape_means_no(tmp_path):
    script = {
        "orchestrator": [Reply("", [ToolCall("1", "delegate", {"agent": "coder", "task": "t"})]), Reply("ok")],
        "coder": [Reply("", [ToolCall("2", "write_file", {"path": "a.txt", "content": "x"})]), Reply("denied")],
    }
    app = app_with(tmp_path, script)
    async with app.run_test() as pilot:
        await pilot.press(*"go", "enter")
        assert await wait_for(pilot, lambda: app.picker.active)
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: app.started_at is None)
        assert not (tmp_path / "a.txt").exists()


async def test_clarifier_question_answered_via_input(tmp_path):
    script = {
        "orchestrator": [Reply("", [ToolCall("1", "ask_user", {"question": "Welche Sprache?"})]), Reply("ok")],
    }
    app = app_with(tmp_path, script)
    async with app.run_test() as pilot:
        await pilot.press(*"bau was", "enter")
        assert await wait_for(pilot, lambda: app.pending_answer is not None)
        await pilot.press(*"Python", "enter")
        assert await wait_for(pilot, lambda: app.started_at is None)
        tool_msgs = [m.content for m in app.session.orchestrator.messages if m.role == "tool"]
        assert tool_msgs == ["User answered: Python"]


async def test_slash_autocomplete(tmp_path):
    app = app_with(tmp_path, {})
    async with app.run_test() as pilot:
        await pilot.press("/", "m")
        suggest = app.query_one("#suggest")
        assert suggest.display and suggest.option_count == 2           # /mode, /mcp
        await pilot.press("tab")
        assert app.prompt.value == "/mode "
        await pilot.press(*"auto", "enter")
        assert app.session.permissions.mode == "auto"


async def test_enter_on_suggestion_runs_it(tmp_path):
    app = app_with(tmp_path, {})
    async with app.run_test() as pilot:
        await pilot.press("/", "m", "o", "enter")                      # /mode picker
        assert await wait_for(pilot, lambda: app.picker.active)
        await pilot.press("4")                                         # plan
        assert app.session.permissions.mode == "plan"


async def test_agent_picker_flow(tmp_path, monkeypatch):
    async def fake_models(provider, custom=None, refresh=False):
        return [("model-a", "A"), ("model-b", "B")]
    monkeypatch.setattr(tui_app, "list_models", fake_models)
    app = app_with(tmp_path, {})
    async with app.run_test() as pilot:
        await pilot.press(*"/agent", "enter")
        assert await wait_for(pilot, lambda: app.picker.active)
        await pilot.press("4")                                         # coder
        assert await wait_for(pilot, lambda: app.picker.active and "Provider" in str(
            app.picker.query_one("#picker-title").render()))
        await pilot.press("2")                                         # codex
        assert await wait_for(pilot, lambda: app.picker.query_one("#picker-list").option_count == 3)
        await pilot.press("3")                                         # model-b
        assert await wait_for(pilot, lambda: not app.picker.active)
        assert app.session.spec_for("coder") == "codex:model-b"
        assert app.session.spec_for("planner") == app.session.config.default


async def test_slash_commands(tmp_path):
    app = app_with(tmp_path, {})
    async with app.run_test() as pilot:
        await pilot.press(*"/mode auto", "enter")
        assert app.session.permissions.mode == "auto"
        await pilot.press(*"/model coder claude:haiku", "enter")
        assert app.session.spec_for("coder") == "claude:haiku"
