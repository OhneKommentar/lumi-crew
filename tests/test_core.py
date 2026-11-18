import asyncio
from pathlib import Path

import pytest

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

    async def ask_user(self, agent, q):
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
