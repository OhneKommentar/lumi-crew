"""
Live model catalogs, fetched from the CLIs / servers every time (cached per session):

  agy       `agy models`
  codex     `codex debug models` (only models with visibility "list")
  claude    the CLI has no list command; its aliases always resolve to the newest model
  ollama, lmstudio, openai, custom openai_compat   GET <base_url>/models
  anthropic, gemini                                 SDK model listing (needs a key)
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil

from .providers import provider_config
from .providers.base import ProviderError

PICKER_PROVIDERS = ["agy", "codex", "anthropic", "ollama", "lmstudio"]   # built-in; plugins are added

PROVIDER_LABELS = {"agy": "CLI · Antigravity", "codex": "CLI · OpenAI Codex", "anthropic": "API · Claude per API-Key",
                   "ollama": "lokal · Ollama", "lmstudio": "lokal · LM Studio"}


def picker_providers(custom: dict | None = None) -> list[str]:
    """Providers offered in /agent, /usage and `lumi-crew setup`: built-ins, plugins, custom ones."""
    from .plugins import PLUGINS, load_plugins
    load_plugins()
    names = list(PICKER_PROVIDERS)
    names[2:2] = [n for n, p in PLUGINS.items() if p.picker and n not in names]
    names += [n for n in (custom or {}) if n not in names]
    return names


def provider_label(name: str) -> str:
    from .plugins import get
    p = get(name)
    return p.label if p else PROVIDER_LABELS.get(name, "eigener Provider")


_cache: dict[str, list[tuple[str, str]]] = {}


async def _run(*args: str, timeout: float = 30) -> str:
    if not shutil.which(args[0]):
        raise ProviderError(f"`{args[0]}` ist nicht installiert")
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise ProviderError(f"`{' '.join(args)}` fehlgeschlagen: {err.decode(errors='replace').strip()[-300:]}")
    return out.decode(errors="replace")


async def _agy() -> list[tuple[str, str]]:
    out = await _run("agy", "models")
    models = []
    for line in out.splitlines():
        if "\t" in line:
            model_id, label = line.split("\t", 1)
            models.append((model_id.strip(), label.strip()))
    return models


async def _codex() -> list[tuple[str, str]]:
    data = json.loads(await _run("codex", "debug", "models"))
    items = data["models"] if isinstance(data, dict) else data
    items = sorted((m for m in items if m.get("visibility", "list") == "list"), key=lambda m: m.get("priority", 99))
    return [(m["slug"], f"{m.get('display_name', m['slug'])} · {m.get('description', '')}".strip(" ·")) for m in items]


async def _openai_compat(cfg: dict) -> list[tuple[str, str]]:
    from openai import AsyncOpenAI
    key = os.environ.get(cfg["api_key_env"]) if cfg.get("api_key_env") else None
    client = AsyncOpenAI(base_url=cfg.get("base_url"), api_key=key or "not-needed", timeout=3, max_retries=0)
    try:
        page = await client.models.list()
    except Exception as e:
        where = cfg.get("base_url") or "api.openai.com"
        raise ProviderError(f"keine Verbindung zu {where} ({type(e).__name__})") from e
    return [(m.id, getattr(m, "owned_by", "") or "") for m in page.data]


async def _anthropic() -> list[tuple[str, str]]:
    import anthropic
    client = anthropic.AsyncAnthropic()
    try:
        return [(m.id, m.display_name) async for m in client.models.list()]
    except anthropic.AuthenticationError as e:
        raise ProviderError("kein Anthropic-Key (ANTHROPIC_API_KEY oder `ant auth login`)") from e
    except anthropic.APIConnectionError as e:
        raise ProviderError("keine Verbindung zur Anthropic API") from e


async def _gemini() -> list[tuple[str, str]]:
    from google import genai
    if not os.environ.get("GEMINI_API_KEY"):
        raise ProviderError("GEMINI_API_KEY nicht gesetzt")
    client = genai.Client()
    out = []
    async for m in await client.aio.models.list():
        if "generateContent" in (m.supported_actions or []):
            out.append((m.name.removeprefix("models/"), m.display_name or ""))
    return out


async def list_models(provider: str, custom: dict | None = None, refresh: bool = False) -> list[tuple[str, str]]:
    """Return [(model_id, description)] for a provider, fetched live from its CLI or server."""
    if provider in _cache and not refresh:
        return _cache[provider]
    cfg = provider_config(provider, custom or {})
    t = cfg["type"]
    from .plugins import by_type
    plugin = by_type(t)
    if plugin:
        if not plugin.list_models:
            raise ProviderError(f"{provider}: das Plugin liefert keine Modellliste")
        models = await plugin.list_models()
    elif t == "agy":
        models = await _agy()
    elif t == "codex_cli":
        models = await _codex()
    elif t == "openai_compat":
        models = await _openai_compat(cfg)
    elif t == "anthropic":
        models = await _anthropic()
    elif t == "gemini":
        models = await _gemini()
    else:
        raise ProviderError(f"unbekannter Provider-Typ {t}")
    if not models:
        raise ProviderError(f"{provider}: keine Modelle gefunden")
    _cache[provider] = models
    return models


async def fallback_suggestions(current: str, custom: dict | None = None) -> list[tuple[str, str]]:
    """Quick alternatives when `current` ("provider:model") hits a usage limit.
    Other providers first (a limit usually applies to the whole account), then other models of the same
    provider if their list is already known. Only reachable providers are suggested."""
    from .providers import availability, parse_spec
    cur_provider, cur_model = parse_spec(current)
    from .plugins import get as plugin
    names = [n for n in picker_providers(custom) if n != cur_provider]
    checks = await asyncio.gather(*(asyncio.to_thread(availability, n, custom or {}) for n in names))
    out: list[tuple[str, str]] = []
    for name, (ok, _) in zip(names, checks):
        if not ok:
            continue
        p = plugin(name)
        if p is not None:
            out += list(p.fallbacks)
        elif name == "anthropic":
            out.append(("anthropic", "Claude per API-Key · Standardmodell"))
        elif name in ("agy", "codex"):
            out.append((name, f"{name} · Standardmodell"))
        else:                                   # local servers: list is cheap
            try:
                models = await list_models(name, custom)
                out += [(f"{name}:{m}", f"lokal · {name}") for m, _ in models[:2]]
            except Exception:
                continue
    for m, desc in _cache.get(cur_provider, []):
        if m != cur_model and len([o for o in out if o[0].startswith(cur_provider + ":")]) < 2:
            out.append((f"{cur_provider}:{m}", f"gleicher Provider · {desc}"))
    return out
