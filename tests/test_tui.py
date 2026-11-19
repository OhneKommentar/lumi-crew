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


async def test_history_up_down_keeps_draft(tmp_path):
    app = app_with(tmp_path, {})
    async with app.run_test() as pilot:
        await pilot.press(*"/mode auto", "enter")
        await pilot.press(*"/mode plan", "enter")
        await pilot.press(*"entwurf")
        await pilot.press("up")
        assert app.prompt.value == "/mode plan"
        await pilot.press("up")
        assert app.prompt.value == "/mode auto"
        await pilot.press("up")                                         # stays at the oldest
        assert app.prompt.value == "/mode auto"
        await pilot.press("down", "down")
        assert app.prompt.value == "entwurf"                            # draft is back
        assert not app.query_one("#suggest").display                    # recalled /commands don't pop the list


class Gate(Provider):
    """Orchestrator that blocks until released, so messages can be queued meanwhile."""

    def __init__(self):
        super().__init__("fake", "fake")
        self.release = None
        self.seen = []

    async def complete(self, system, messages, tools):
        import asyncio
        if self.release is None:
            self.release = asyncio.Event()
        self.seen.append(messages[-1].content)
        await self.release.wait()
        self.release.clear()
        return Reply(f"done: {messages[-1].content}")


async def test_queue_while_busy_runs_afterwards(tmp_path):
    app = CrewApp(tmp_path, Config(), "auto")
    gate = Gate()
    app.session.provider_for = lambda role: gate
    async with app.run_test() as pilot:
        await pilot.press(*"erste", "enter")
        assert await wait_for(pilot, lambda: gate.release is not None)
        await pilot.press(*"zweite", "enter")
        assert app.queue == ["zweite"] and app.query_one("#queued").display
        gate.release.set()
        assert await wait_for(pilot, lambda: gate.seen == ["erste", "zweite"])
        assert not app.queue
        gate.release.set()
        assert await wait_for(pilot, lambda: app.started_at is None)


async def test_up_recalls_queued_message_for_editing(tmp_path):
    app = CrewApp(tmp_path, Config(), "auto")
    gate = Gate()
    app.session.provider_for = lambda role: gate
    async with app.run_test() as pilot:
        await pilot.press(*"erste", "enter")
        assert await wait_for(pilot, lambda: gate.release is not None)
        await pilot.press(*"zweite", "enter")
        await pilot.press("up")
        assert app.prompt.value == "zweite" and app.queue == []
        assert not app.query_one("#queued").display
        await pilot.press("backspace", *"!", "enter")                  # edit and re-queue
        assert app.queue == ["zweit!"]
        gate.release.set()
        assert await wait_for(pilot, lambda: gate.seen == ["erste", "zweit!"])
        gate.release.set()
        assert await wait_for(pilot, lambda: app.started_at is None)


async def test_question_with_options_is_a_picker(tmp_path):
    script = {"orchestrator": [Reply("", [ToolCall("1", "ask_user", {"question": "Sprache?",
                                                                     "options": ["Python", "Go"]})]), Reply("ok")]}
    app = app_with(tmp_path, script)
    async with app.run_test() as pilot:
        await pilot.press(*"bau was", "enter")
        assert await wait_for(pilot, lambda: app.picker.active)
        assert app.picker.query_one("#picker-list").option_count == 3          # 2 options + own answer
        await pilot.press("2")
        assert await wait_for(pilot, lambda: app.started_at is None)
        assert [m.content for m in app.session.orchestrator.messages if m.role == "tool"] == ["User answered: Go"]


async def test_question_custom_answer_via_input(tmp_path):
    script = {"orchestrator": [Reply("", [ToolCall("1", "ask_user", {"question": "Sprache?",
                                                                     "options": ["Python", "Go"]})]), Reply("ok")]}
    app = app_with(tmp_path, script)
    async with app.run_test() as pilot:
        await pilot.press(*"bau was", "enter")
        assert await wait_for(pilot, lambda: app.picker.active)
        await pilot.press("3")                                                 # ✎ own answer
        assert await wait_for(pilot, lambda: app.pending_answer is not None)
        await pilot.press(*"Rust", "enter")
        assert await wait_for(pilot, lambda: app.started_at is None)
        assert [m.content for m in app.session.orchestrator.messages if m.role == "tool"] == ["User answered: Rust"]


async def test_delegation_opens_agent_windows(tmp_path):
    import asyncio as aio
    gate = aio.Event()

    class Slow(Provider):
        def __init__(self):
            super().__init__("fake", "fake")
            self.orch = [Reply("", [ToolCall("1", "delegate", {"agent": "planner", "task": "Plane X"}),
                                    ToolCall("2", "delegate", {"agent": "researcher", "task": "Suche Y"})]),
                         Reply("end")]

        async def complete(self, system, messages, tools):
            role = system.split("**")[1].lower()
            if role == "orchestrator":
                return self.orch.pop(0)
            await gate.wait()
            return Reply(f"{role} ok")

    app = CrewApp(tmp_path, Config(), "auto")
    slow = Slow()
    app.session.provider_for = lambda role: slow
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press(*"go", "enter")
        assert await wait_for(pilot, lambda: len(app.panes) == 2)
        assert app.query_one("#panes").display
        assert {p.task_text for p in app.panes.values()} == {"Plane X", "Suche Y"}
        gate.set()
        assert await wait_for(pilot, lambda: app.started_at is None)
        assert all(p.has_class("done") for p in app.panes.values())


async def test_limit_fallback_picker(tmp_path, monkeypatch):
    from crew.providers.base import LimitError

    async def fake_suggestions(current, custom=None):
        return [("claude:haiku", "Claude Code · Haiku"), ("codex", "codex · Standard")]
    monkeypatch.setattr(tui_app, "fallback_suggestions", fake_suggestions)

    class Limited(Provider):
        def __init__(self):
            super().__init__("agy", "m")
            self.n = 0

        async def complete(self, system, messages, tools):
            self.n += 1
            if self.n == 1:
                raise LimitError("usage limit reached")
            return Reply("weiter")

    app = CrewApp(tmp_path, Config(), "auto")
    lim = Limited()
    app.session.provider_for = lambda role: lim
    async with app.run_test() as pilot:
        await pilot.press(*"go", "enter")
        assert await wait_for(pilot, lambda: app.picker.active)
        await pilot.press("2")                                                 # codex
        assert await wait_for(pilot, lambda: app.started_at is None)
        assert app.session.spec_for("orchestrator") == "codex"


async def test_usage_picker(tmp_path, monkeypatch):
    from crew import usage

    async def fake_account(name, ptype):
        return [f"Konto von {name}"]
    monkeypatch.setattr(usage, "account_info", fake_account)
    usage.USAGE.clear()
    usage.record("claude", "haiku", input_tokens=10, output_tokens=5)
    app = app_with(tmp_path, {})
    async with app.run_test() as pilot:
        await pilot.press(*"/usage", "enter")
        assert await wait_for(pilot, lambda: app.picker.active)
        await pilot.press("3")                                                 # claude
        assert await wait_for(pilot, lambda: not app.picker.display)
        md = [w for w in app.query("Markdown") if "Usage · claude" in w.source]
        assert md and "Konto von claude" in md[0].source and "Aufrufe | 1" in md[0].source
