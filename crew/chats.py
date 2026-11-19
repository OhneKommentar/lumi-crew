"""
Saved chats: every conversation is stored as JSON in ~/.config/crew/chats/<id>.json
(next to state.json, so $CREW_STATE also moves it). A chat holds
  - `entries`:  what the transcript showed (to re-render it on resume)
  - `messages`: the orchestrator's conversation (to continue where it stopped)
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import state_path
from .providers.base import Message, ToolCall


def chats_dir() -> Path:
    return state_path().parent / "chats"


@dataclass
class Chat:
    id: str
    workspace: str
    title: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    entries: list[dict] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)

    @property
    def user_turns(self) -> int:
        return sum(1 for e in self.entries if e.get("kind") == "user")


def new_chat(workspace: Path | str) -> Chat:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Chat(id=f"{stamp}-{secrets.token_hex(2)}", workspace=str(Path(workspace).resolve()))


def message_to_dict(m: Message) -> dict:
    return {"role": m.role, "content": m.content, "name": m.name, "tool_call_id": m.tool_call_id,
            "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in m.tool_calls]}


def message_from_dict(d: dict) -> Message:
    # provider-native `raw` blocks are not stored; every provider can continue from the plain form
    return Message(role=d["role"], content=d.get("content", ""), name=d.get("name"),
                   tool_call_id=d.get("tool_call_id"),
                   tool_calls=[ToolCall(c["id"], c["name"], c.get("args") or {}) for c in d.get("tool_calls", [])])


def save(chat: Chat, messages: list[Message] | None = None):
    if messages is not None:
        chat.messages = [message_to_dict(m) for m in messages]
    if not chat.title:
        first = next((e.get("text", "") for e in chat.entries if e.get("kind") == "user"), "")
        chat.title = " ".join(first.split())[:80]
    chat.updated = time.time()
    d = chats_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{chat.id}.tmp"
    tmp.write_text(json.dumps(asdict(chat), ensure_ascii=False, indent=1))
    tmp.replace(d / f"{chat.id}.json")


def load(chat_id: str) -> Chat:
    data = json.loads((chats_dir() / f"{chat_id}.json").read_text())
    return Chat(**{k: data[k] for k in Chat.__dataclass_fields__ if k in data})


def list_chats(workspace: Path | str | None = None) -> list[Chat]:
    """All saved chats, newest first; only those of `workspace` if given."""
    ws = str(Path(workspace).resolve()) if workspace else None
    out = []
    for f in chats_dir().glob("*.json"):
        try:
            c = load(f.stem)
        except (OSError, ValueError, TypeError, KeyError):
            continue
        if c.entries and (ws is None or c.workspace == ws):
            out.append(c)
    return sorted(out, key=lambda c: c.updated, reverse=True)


def ago(ts: float) -> str:
    d = time.time() - ts
    if d < 60:
        return "gerade eben"
    if d < 3600:
        return f"vor {int(d // 60)} min"
    if d < 86400:
        return f"vor {int(d // 3600)} h"
    if d < 7 * 86400:
        return f"vor {int(d // 86400)} Tag{'en' if d >= 2 * 86400 else ''}"
    return time.strftime("%d.%m.%Y", time.localtime(ts))
