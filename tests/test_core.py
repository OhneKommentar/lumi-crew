import asyncio
from pathlib import Path

import pytest

from crew.agents.roles import ROLES
from crew.config import Config
from crew.events import UI
from crew.permissions import PathError, Permissions, resolve_in_workspace
from crew.providers.base import Message, Provider, Reply, ToolCall
from crew.providers.text_tools import OMITTED, parse_reply, render_transcript
from crew.session import Session


# --------------------------------------------------------------------------- helpers
class FakeProvider(Provider):
    """Replies are scripted per agent name via a shared dict of queues."""

    def __init__(self, script):
        super().__init__("fake", "fake")
        self.script = script

    async def complete(self, system, messages, tools):
        role = system.split("**")[1].lower()       # "You are the **Coder**" -> coder
        item = self.script[role].pop(0)
        if isinstance(item, Reply):
            return item
        return Reply(text=item)


class RecUI(UI):
    def __init__(self, approve="yes", answers=()):
        self.events, self._approve, self.answers, self.approvals = [], approve, list(answers), []

    def emit(self, e):
        self.events.append(e)

    async def ask_user(self, agent, q, options=None):
        return self.answers.pop(0) if self.answers else ""

    async def approve(self, agent, title, preview):
        self.approvals.append((title, preview))
        return self._approve


def call(name, **args):
    return Reply("", [ToolCall("id-" + name, name, args)])


def make_session(tmp_path, script, mode="auto", ui=None, **cfg):
    s = Session(tmp_path, Config(**cfg), ui or RecUI(), mode=mode)
    fake = FakeProvider(script)
    s.provider_for = lambda role: fake
    return s


# --------------------------------------------------------------------------- text protocol
def test_parse_tool_call_in_fence():
    p = parse_reply('Sure.\n```json\n{"tool": "read_file", "args": {"path": "a.py"}}\n```')
    assert p.call.name == "read_file" and p.call.args == {"path": "a.py"}


def test_parse_bare_json_and_final():
    assert parse_reply('{"final": "done"}').final == "done"
    assert parse_reply('blah {"tool": "x", "args": {}} blah').call.name == "x"


def test_parse_plain_text_is_final():
    assert parse_reply("Just an answer.").final == "Just an answer."


def test_parse_broken_tool_json_is_error():
    assert parse_reply('```json\n{"tool": "x", "args": {\n```').error


def test_parse_literal_newlines_in_strings():
    p = parse_reply('{"tool": "write_file", "args": {"path": "a", "content": "line1\nline2"}}')
    assert p.call.args["content"] == "line1\nline2"


def test_transcript_truncates_but_keeps_task():
    msgs = [Message("user", "THE TASK")] + [Message("tool", "x" * 1000, name="t") for _ in range(50)]
    out = render_transcript(msgs, budget=5000)
    assert "THE TASK" in out and OMITTED in out and len(out) < 7000


# --------------------------------------------------------------------------- permissions
@pytest.mark.parametrize("mode,kind,expected", [
    ("manual", "read", "allow"), ("manual", "write", "ask"), ("manual", "shell", "ask"),
    ("accept-edits", "write", "allow"), ("accept-edits", "shell", "ask"),
    ("auto", "write", "allow"), ("auto", "shell", "allow"),
    ("plan", "read", "allow"), ("plan", "write", "deny"), ("plan", "shell", "deny"),
])
def test_permission_matrix(mode, kind, expected):
    assert Permissions(mode).decide(kind, "t", {"command": "ls"})[0] == expected


def test_blocklist_in_auto_and_warning_in_manual():
    assert Permissions("auto").decide("shell", "run_shell", {"command": "sudo rm x"})[0] == "deny"
    d, reason = Permissions("manual").decide("shell", "run_shell", {"command": "git push"})
    assert d == "ask" and "blocklist" in reason


def test_cycle_modes():
    p = Permissions("manual")
    assert [p.cycle() for _ in range(4)] == ["accept-edits", "auto", "plan", "manual"]


def test_path_jail(tmp_path):
    assert resolve_in_workspace(tmp_path, "a/b.py") == (tmp_path / "a/b.py").resolve()
    for bad in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(PathError):
            resolve_in_workspace(tmp_path, bad)
    (tmp_path / "link").symlink_to("/tmp")
    with pytest.raises(PathError):
        resolve_in_workspace(tmp_path, "link/x")


# --------------------------------------------------------------------------- agent loop + delegation
async def test_coder_writes_file_via_delegation(tmp_path):
    script = {
        "orchestrator": [call("delegate", agent="coder", task="create hello.py"), "All done."],
        "coder": [call("write_file", path="hello.py", content="print('hi')\n"), "Created hello.py"],
    }
    s = make_session(tmp_path, script)
    assert await s.send("make hello") == "All done."
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"
    kinds = [(e.kind, e.agent) for e in s.ui.events]
    assert ("agent_start", "coder") in kinds and ("agent_end", "coder") in kinds


async def test_manual_mode_denied_write_is_reported(tmp_path):
    ui = RecUI(approve="no")
    script = {"orchestrator": [call("delegate", agent="coder", task="x"), "ok"],
              "coder": [call("write_file", path="a.txt", content="x"), "could not write"]}
    s = make_session(tmp_path, script, mode="manual", ui=ui)
    await s.send("go")
    assert not (tmp_path / "a.txt").exists()
    assert ui.approvals and "+x" in ui.approvals[0][1]     # diff preview was shown


async def test_plan_mode_blocks_writes(tmp_path):
    script = {"orchestrator": [call("delegate", agent="coder", task="x"), "ok"],
              "coder": [call("write_file", path="a.txt", content="x"), "denied"]}
    s = make_session(tmp_path, script, mode="plan")
    await s.send("go")
    assert not (tmp_path / "a.txt").exists()
    results = [e.text for e in s.ui.events if e.kind == "tool_result"]
    assert any("plan mode" in r for r in results)


async def test_review_round_limit(tmp_path):
    rounds = 2
    script = {"orchestrator": [call("delegate", agent="reviewer", task="r")] * (rounds + 1) + ["end"],
              "reviewer": ["CHANGES_REQUESTED 1. bug"] * rounds}
    s = make_session(tmp_path, script, max_review_rounds=rounds)
    await s.send("go")
    o = s.orchestrator.messages
    assert sum(1 for m in o if m.role == "tool" and m.content.startswith("LIMIT")) == 1


async def test_clarifier_asks_user(tmp_path):
    ui = RecUI(answers=["Python, CLI"])
    script = {"orchestrator": [call("delegate", agent="clarifier", task="clarify"), "spec received"],
              "clarifier": [call("ask_user", question="Which language?"), "Goal: Python CLI"]}
    s = make_session(tmp_path, script, ui=ui)
    await s.send("make me a game")
    clar_results = [e.text for e in ui.events if e.kind == "tool_result" and e.agent == "clarifier"]
    assert "Python, CLI" in clar_results[0]


async def test_unknown_tool_and_bad_args(tmp_path):
    script = {"orchestrator": [call("nope"), call("read_file"), "fine"]}
    s = make_session(tmp_path, script)
    await s.send("x")
    results = [m.content for m in s.orchestrator.messages if m.role == "tool"]
    assert results[0].startswith("ERROR: unknown tool") and "missing argument" in results[1]


async def test_shell_tool_runs_in_workspace(tmp_path):
    script = {"orchestrator": [call("delegate", agent="tester", task="t"), "ok"],
              "tester": [call("run_shell", command="pwd && echo hi"), "PASS"]}
    s = make_session(tmp_path, script)
    await s.send("x")
    out = [e.text for e in s.ui.events if e.kind == "tool_result" and e.agent == "tester"][0]
    assert "exit code 0" in out and str(tmp_path.resolve()) in out and "hi" in out


# --------------------------------------------------------------------------- parallel, options, limits, usage
def test_parse_multiple_tool_calls():
    p = parse_reply('{"tools": [{"tool": "delegate", "args": {"agent": "planner", "task": "a"}},'
                    ' {"tool": "delegate", "args": {"agent": "researcher", "task": "b"}}]}')
    assert [c.args["agent"] for c in p.calls] == ["planner", "researcher"]


async def test_parallel_delegations_run_concurrently(tmp_path):
    import asyncio as aio
    started, both = [], aio.Event()

    class Par(Provider):
        def __init__(self):
            super().__init__("fake", "fake")
            self.orch = [Reply("", [ToolCall("1", "delegate", {"agent": "planner", "task": "p"}),
                                    ToolCall("2", "delegate", {"agent": "researcher", "task": "r"})]), Reply("end")]

        async def complete(self, system, messages, tools):
            role = system.split("**")[1].lower()
            if role == "orchestrator":
                return self.orch.pop(0)
            started.append(role)
            if len(started) == 2:
                both.set()
            await aio.wait_for(both.wait(), 2)        # deadlocks unless both run at the same time
            return Reply(f"{role} done")

    s = Session(tmp_path, Config(), RecUI(), mode="auto")
    par = Par()
    s.provider_for = lambda role: par
    assert await s.send("go") == "end"
    assert sorted(started) == ["planner", "researcher"]


async def test_same_role_in_parallel_gets_unique_names(tmp_path):
    import asyncio as aio
    both = aio.Event()
    seen = []

    class Two(Provider):
        def __init__(self):
            super().__init__("fake", "fake")
            self.orch = [Reply("", [ToolCall("1", "delegate", {"agent": "coder", "task": "a"}),
                                    ToolCall("2", "delegate", {"agent": "coder", "task": "b"})]), Reply("end")]

        async def complete(self, system, messages, tools):
            if "**Orchestrator**" in system:
                return self.orch.pop(0)
            seen.append(1)
            if len(seen) == 2:
                both.set()
            await aio.wait_for(both.wait(), 2)
            return Reply("done")

    s = Session(tmp_path, Config(), RecUI(), mode="auto")
    two = Two()
    s.provider_for = lambda role: two
    await s.send("go")
    names = {e.agent for e in s.ui.events if e.kind == "agent_start"}
    assert {"coder", "coder#2"} <= names
    assert not s.active                                   # instance names are released again


async def test_ask_user_passes_options(tmp_path):
    class OptUI(RecUI):
        async def ask_user(self, agent, q, options=None):
            self.got = options
            return options[0]
    ui = OptUI()
    script = {"orchestrator": [call("ask_user", question="Sprache?", options=["Python (empfohlen)", "Go"]), "ok"]}
    s = make_session(tmp_path, script, ui=ui)
    await s.send("x")
    assert ui.got == ["Python (empfohlen)", "Go"]
    assert [m.content for m in s.orchestrator.messages if m.role == "tool"] == ["User answered: Python (empfohlen)"]


async def test_limit_error_asks_for_fallback_and_continues(tmp_path):
    from crew.providers.base import LimitError

    class Limited(Provider):
        def __init__(self):
            super().__init__("agy", "m")
            self.calls = 0

        async def complete(self, system, messages, tools):
            self.calls += 1
            if self.calls == 1:
                raise LimitError("You have exhausted your quota")
            return Reply("done after switch")

    class FallbackUI(RecUI):
        async def choose_fallback(self, agent, role, current, error):
            self.asked = (agent, role, current)
            return "claude:haiku"

    ui = FallbackUI()
    s = Session(tmp_path, Config(), ui, mode="auto")
    lim = Limited()
    s.provider_for = lambda role: lim
    assert await s.send("x") == "done after switch"
    assert ui.asked == ("orchestrator", "orchestrator", "agy:m")
    assert s.spec_for("orchestrator") == "claude:haiku"


async def test_limit_error_without_fallback_reports_error(tmp_path):
    from crew.providers.base import LimitError

    class Limited(Provider):
        async def complete(self, system, messages, tools):
            raise LimitError("429 Too Many Requests")

    s = Session(tmp_path, Config(), RecUI(), mode="auto")
    s.provider_for = lambda role: Limited("agy", "m")
    assert (await s.send("x")).startswith("ERROR")


def test_limit_message_detection():
    from crew.providers.base import is_limit_message
    assert is_limit_message("Error: You've hit your usage limit. Try again in 3h")
    assert is_limit_message("RESOURCE_EXHAUSTED: quota exceeded")
    assert not is_limit_message("SyntaxError in line 3")


def test_usage_record_and_summary():
    from crew import usage
    usage.USAGE.clear()
    usage.record("claude", "haiku", input_tokens=1200, output_tokens=300, cost_usd=0.01, seconds=2)
    usage.record("claude", "haiku", input_tokens=800, output_tokens=200, seconds=1)
    assert usage.summary("claude") == "2 Aufrufe · 2.5k Tokens · $0.01"
    assert "noch nicht genutzt" in usage.summary("codex")


def test_model_choice_is_saved_and_loaded(tmp_path, isolated_state):
    s = Session(tmp_path, Config(), RecUI())
    s.set_model("coder", "codex:gpt-x", persist=True)
    s.set_model("tester", "claude:haiku")                      # not persisted
    s2 = Session(tmp_path, Config(), RecUI())                   # "next start"
    assert s2.spec_for("coder") == "codex:gpt-x"
    assert s2.spec_for("tester") == Config().default
    assert Session(tmp_path, Config(), RecUI(), use_saved=False).spec_for("coder") == Config().default


def test_saved_choice_beats_crew_toml_and_reset_forgets(tmp_path, isolated_state):
    cfg = Config(agents={"coder": "agy:from-toml"})
    s = Session(tmp_path, cfg, RecUI())
    assert s.spec_for("coder") == "agy:from-toml"
    s.set_model("all", "claude:sonnet", persist=True)
    s2 = Session(tmp_path, cfg, RecUI())
    assert all(s2.spec_for(r) == "claude:sonnet" for r in ROLES)
    s2.forget_models()
    assert Session(tmp_path, cfg, RecUI()).spec_for("coder") == "agy:from-toml"


def test_broken_state_file_is_ignored(tmp_path, isolated_state):
    isolated_state.write_text("{ not json")
    assert Session(tmp_path, Config(), RecUI()).overrides == {}


def test_chat_roundtrip(tmp_path, isolated_state):
    from crew import chats
    c = chats.new_chat(tmp_path)
    c.entries = [{"kind": "user", "text": "Bau mir   ein\nwc-Tool"}, {"kind": "agent", "agent": "orchestrator", "text": "ok"}]
    msgs = [Message("user", "Bau"), Message("assistant", "", [ToolCall("1", "delegate", {"agent": "coder"})], raw={"x": 1}),
            Message("tool", "done", tool_call_id="1", name="delegate")]
    chats.save(c, msgs)
    back = chats.load(c.id)
    assert back.title == "Bau mir ein wc-Tool" and back.user_turns == 1
    restored = [chats.message_from_dict(d) for d in back.messages]
    assert restored[1].tool_calls[0].args == {"agent": "coder"} and restored[1].raw is None
    assert restored[2].tool_call_id == "1"
    other = chats.new_chat(tmp_path / "elsewhere")
    other.entries = [{"kind": "user", "text": "x"}]
    chats.save(other)
    assert [x.id for x in chats.list_chats(tmp_path)] == [c.id]
    assert len(chats.list_chats()) == 2
    empty = chats.new_chat(tmp_path)
    chats.save(empty)                                      # chats without entries are not listed
    assert len(chats.list_chats(tmp_path)) == 1
