"""
Provider setup: detect what is missing (CLI not installed, not logged in, no API key, server not running)
and the official commands that fix it. Nothing runs without the user's confirmation; the UI asks first.

API keys entered in Lumi Crew are stored in the macOS keychain (service "lumi-crew", one entry per env var),
elsewhere in ~/.config/lumi-crew/keys.json with mode 600. An environment variable always wins.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import state_path
from .providers import provider_config

KEYCHAIN_SERVICE = "lumi-crew"


@dataclass
class Step:
    """One fix: a command to run interactively (install / login / start) or a key to enter."""
    kind: str                       # install | login | start | key | manual
    title: str                      # what the user is asked, e.g. "codex installieren?"
    command: list[str] = field(default_factory=list)
    env: str = ""                   # for kind == "key": which environment variable
    help: str = ""                  # shown for kind == "manual"


@dataclass
class Status:
    ok: bool
    reason: str = ""
    step: Step | None = None


def _brew() -> str | None:
    return shutil.which("brew")


def _install(provider: str, cask: bool, token: str) -> Step:
    cmd = ["brew", "install"] + (["--cask"] if cask else []) + [token]
    if not _brew():
        return Step("manual", f"{provider} ist nicht installiert",
                    help=f"Homebrew fehlt. Installiere {provider} nach der offiziellen Anleitung.")
    return Step("install", f"{provider} ist nicht installiert. Jetzt installieren?", cmd)


async def _run(*args: str, timeout: float = 20) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode, out.decode(errors="replace")
    except (OSError, asyncio.TimeoutError) as e:
        return 1, str(e)


# ----------------------------------------------------------------------------- API keys
def _keys_file() -> Path:
    return state_path().parent / "keys.json"


def stored_key(env: str) -> str | None:
    if sys.platform == "darwin" and shutil.which("security") and not os.environ.get("LUMI_CREW_STATE"):
        import subprocess
        r = subprocess.run(["security", "find-generic-password", "-a", env, "-s", KEYCHAIN_SERVICE, "-w"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return r.stdout.strip() or None
    try:
        return json.loads(_keys_file().read_text()).get(env) or None
    except (OSError, ValueError):
        return None


def store_key(env: str, value: str):
    """Save an API key (keychain on macOS, else a 600 file) and make it active for this process."""
    value = value.strip()
    if sys.platform == "darwin" and shutil.which("security") and not os.environ.get("LUMI_CREW_STATE"):
        import subprocess
        subprocess.run(["security", "add-generic-password", "-U", "-a", env, "-s", KEYCHAIN_SERVICE, "-w", value],
                       check=True, capture_output=True)
    else:
        path = _keys_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        data[env] = value
        path.write_text(json.dumps(data))
        path.chmod(0o600)
    os.environ[env] = value


def load_stored_keys(custom: dict | None = None):
    """Put stored keys into the environment for providers whose variable is not set yet."""
    from .providers import BUILTIN
    for name in set(BUILTIN) | set(custom or {}):
        env = provider_config(name, custom or {}).get("api_key_env")
        if env and not os.environ.get(env):
            key = stored_key(env)
            if key:
                os.environ[env] = key


# ----------------------------------------------------------------------------- status per provider
async def status(provider: str, custom: dict | None = None) -> Status:
    """Is `provider` ready to use? If not, which step would fix it?"""
    cfg = provider_config(provider, custom or {})
    t = cfg["type"]

    if t == "claude_cli":
        if not shutil.which("claude"):
            return Status(False, "nicht installiert", _install("Claude Code", True, "claude-code"))
        code, out = await _run("claude", "auth", "status")
        try:
            logged_in = json.loads(out).get("loggedIn", False)
        except ValueError:
            logged_in = code == 0
        if not logged_in:
            return Status(False, "nicht eingeloggt", Step("login", "Bei Claude Code anmelden?", ["claude", "auth", "login"]))
        return Status(True, "eingeloggt")

    if t == "codex_cli":
        if not shutil.which("codex"):
            return Status(False, "nicht installiert", _install("Codex", True, "codex"))
        code, out = await _run("codex", "login", "status")
        if code != 0 or "not logged in" in out.lower():
            return Status(False, "nicht eingeloggt", Step("login", "Bei Codex anmelden?", ["codex", "login"]))
        return Status(True, "eingeloggt")

    if t == "agy":
        if not shutil.which("agy"):
            return Status(False, "nicht installiert", Step(
                "manual", "Antigravity-CLI (agy) ist nicht installiert",
                help="Antigravity installieren (https://antigravity.google, oder `brew install --cask antigravity`), "
                     "die App einmal öffnen und dort die Kommandozeilen-Tools aktivieren. Danach `agy` im "
                     "Terminal starten und mit Google anmelden."))
        accounts = Path.home() / ".gemini" / "google_accounts.json"
        try:
            logged_in = bool(json.loads(accounts.read_text()).get("active"))
        except (OSError, ValueError):
            logged_in = False
        if not logged_in:
            return Status(False, "nicht eingeloggt", Step("login", "Bei Antigravity anmelden? (agy startet, danach /quit)",
                                                         ["agy"]))
        return Status(True, "eingeloggt")

    if t == "openai_compat" and cfg.get("base_url", "").startswith(("http://localhost", "http://127.0.0.1")):
        from .providers import availability
        ok, msg = await asyncio.to_thread(availability, provider, custom or {})
        if ok:
            return Status(True, msg)
        if provider == "ollama":
            if not shutil.which("ollama"):
                return Status(False, "nicht installiert", _install("Ollama", False, "ollama"))
            if _brew():
                return Status(False, "Server läuft nicht", Step(
                    "start", "Ollama-Server starten? (läuft dann im Hintergrund weiter)",
                    ["brew", "services", "start", "ollama"]))
            return Status(False, "Server läuft nicht", Step("manual", "Ollama-Server läuft nicht",
                                                            help="Starte ihn mit `ollama serve`."))
        if provider == "lmstudio":
            if not Path("/Applications/LM Studio.app").exists() and not shutil.which("lms"):
                return Status(False, "nicht installiert", _install("LM Studio", True, "lm-studio"))
            lms = shutil.which("lms") or str(Path.home() / ".lmstudio" / "bin" / "lms")
            if Path(lms).exists():
                return Status(False, "Server läuft nicht", Step("start", "LM-Studio-Server starten?",
                                                                [lms, "server", "start"]))
            return Status(False, "Server läuft nicht", Step(
                "manual", "LM-Studio-Server läuft nicht",
                help="LM Studio öffnen, ein Modell laden und unter Developer den lokalen Server starten."))
        return Status(False, msg, Step("manual", f"{provider} nicht erreichbar", help=msg))

    env = cfg.get("api_key_env")
    if env:
        if os.environ.get(env) or stored_key(env):
            return Status(True, "API-Key vorhanden")
        if t == "anthropic":
            code, _ = await _run("ant", "auth", "status") if shutil.which("ant") else (1, "")
            if code == 0:
                return Status(True, "ant-Login vorhanden")
        label = {"anthropic": "Anthropic (Claude)", "gemini": "Google Gemini", "openai_compat": "OpenAI"}.get(t, provider)
        return Status(False, "kein API-Key", Step("key", f"API-Key für {label} eingeben ({env})", env=env))
    return Status(True, "konfiguriert")
