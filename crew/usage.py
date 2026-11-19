"""
Usage tracking for /usage.

1. What crew itself used in this session, recorded by every provider call
   (calls, tokens, cost if the CLI reports it, time).
2. Account / limit information each CLI exposes locally:
   claude  `claude auth status` (plan, login expiry)
   codex   `codex login status` + rate limits from the newest local session log (~/.codex/sessions)
   agy     no usage/limit command; only crew's own counters
   ollama / lmstudio   local, no limits
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import shutil
import time
from dataclasses import dataclass, field


@dataclass
class Stats:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    errors: int = 0
    models: set[str] = field(default_factory=set)

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


USAGE: dict[str, Stats] = {}


def record(provider: str, model: str | None, *, input_tokens: int = 0, output_tokens: int = 0,
           cost_usd: float = 0.0, seconds: float = 0.0, error: bool = False):
    s = USAGE.setdefault(provider, Stats())
    s.calls += 1
    s.input_tokens += int(input_tokens or 0)
    s.output_tokens += int(output_tokens or 0)
    s.cost_usd += float(cost_usd or 0)
    s.seconds += seconds
    s.errors += int(error)
    if model:
        s.models.add(model)


def short_tokens(n: int) -> str:
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def summary(provider: str) -> str:
    s = USAGE.get(provider)
    if not s or not s.calls:
        return "in dieser Session noch nicht genutzt"
    text = f"{s.calls} Aufrufe · {short_tokens(s.tokens)} Tokens"
    if s.cost_usd:
        text += f" · ${s.cost_usd:.4f}" if s.cost_usd < 0.01 else f" · ${s.cost_usd:.2f}"
    return text


# ----------------------------------------------------------------------------- account info
async def _run(*args: str) -> str:
    if not shutil.which(args[0]):
        raise FileNotFoundError(args[0])
    proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), 20)
    return out.decode(errors="replace").strip()


def _when(ts: float) -> str:
    return time.strftime("%d.%m. %H:%M", time.localtime(ts))


def codex_rate_limits() -> dict | None:
    """Newest `rate_limits` entry in the local codex session logs, plus the log's timestamp."""
    files = sorted(glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/*.jsonl")), key=os.path.getmtime)
    for path in reversed(files[-5:]):
        found = None
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"rate_limits"' not in line:
                    continue
                try:
                    found = _find_key(json.loads(line), "rate_limits") or found
                except json.JSONDecodeError:
                    continue
        if found:
            return {"limits": found, "as_of": os.path.getmtime(path)}
    return None


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj and obj[key]:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r:
                return r
    return None


def _window(name: str, w: dict) -> str:
    minutes = w.get("window_minutes") or 0
    span = f"{minutes // 60} h" if minutes < 1440 else f"{minutes // 1440} Tage"
    resets = w.get("resets_at")
    if resets and resets < time.time():
        return f"| {name} ({span}) | inzwischen zurückgesetzt (war {w.get('used_percent', 0):.0f} %) |"
    reset = f", Reset {_when(resets)}" if resets else ""
    return f"| {name} ({span}) | {w.get('used_percent', 0):.0f} % verbraucht{reset} |"


async def account_info(provider: str, ptype: str) -> list[str]:
    """Markdown lines with what the CLI/server tells about the account and its limits."""
    lines: list[str] = []
    try:
        if ptype == "claude_cli":
            d = json.loads(await _run("claude", "auth", "status"))
            lines += ["| | |", "|---|---|",
                      f"| Login | {'✔ ' + d.get('authMethod', '') if d.get('loggedIn') else '✗ nicht eingeloggt'} |",
                      f"| Abo | {d.get('subscriptionType') or '–'} |"]
            if d.get("loginExpiresAt"):
                lines.append(f"| Login läuft ab | {d['loginExpiresAt'][:10]} |")
            lines.append("\nDie Claude-Code-CLI gibt ihre Abo-Limits nicht über die Kommandozeile aus. "
                         "Genauer geht es mit `/usage` direkt in Claude Code.")
        elif ptype == "codex_cli":
            status = await _run("codex", "login", "status")
            lines += ["| | |", "|---|---|", f"| Login | {status.splitlines()[-1] if status else '–'} |"]
            rl = codex_rate_limits()
            if rl:
                lim = rl["limits"]
                for key, name in (("primary", "Limit kurz"), ("secondary", "Limit lang")):
                    if isinstance(lim.get(key), dict):
                        lines.append(_window(name, lim[key]))
                credits = lim.get("credits") or {}
                if credits:
                    lines.append(f"| Credits | {'unbegrenzt' if credits.get('unlimited') else credits.get('balance', '–')} |")
                lines.append(f"\n_Stand: {_when(rl['as_of'])}, letzte lokal gespeicherte codex-Session "
                             "(crew-Aufrufe laufen ephemer und aktualisieren das nicht)._")
            else:
                lines.append("\nKeine Limit-Daten gefunden (noch keine lokale codex-Session).")
        elif ptype == "agy":
            lines.append("Antigravity (`agy`) bietet keinen Befehl für Kontingent oder Limits. "
                         "Unten steht, was crew in dieser Session über agy verbraucht hat.")
        elif ptype == "openai_compat":
            lines.append("Lokales bzw. eigenes Modell: keine Abo-Limits, nur die Zähler von crew.")
        else:
            lines.append("Keine Konto-Infos verfügbar.")
    except FileNotFoundError as e:
        lines.append(f"`{e}` ist nicht installiert.")
    except Exception as e:
        lines.append(f"Konto-Infos nicht abrufbar: {type(e).__name__}: {e}")
    return lines


def session_table(provider: str) -> list[str]:
    s = USAGE.get(provider)
    if not s or not s.calls:
        return ["_In dieser crew-Session noch nicht genutzt._"]
    rows = ["| crew-Session | |", "|---|---|",
            f"| Aufrufe | {s.calls}" + (f" ({s.errors} Fehler)" if s.errors else "") + " |",
            f"| Tokens | {short_tokens(s.input_tokens)} rein · {short_tokens(s.output_tokens)} raus |",
            f"| Zeit | {s.seconds:.0f} s |",
            f"| Modelle | {', '.join(sorted(s.models)) or '–'} |"]
    if s.cost_usd:
        rows.insert(4, f"| Kosten (API-Preise) | ${s.cost_usd:.4f} |")
    return rows
