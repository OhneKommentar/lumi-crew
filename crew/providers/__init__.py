"""Provider registry: turns a spec like "agy:gemini-3.8-flash-medium" into a Provider."""
from __future__ import annotations

import os
import shutil

from .base import Provider, ProviderError

# Built-in provider names. More can be added in crew.toml under [providers.<name>].
BUILTIN: dict[str, dict] = {
    "agy":      {"type": "agy"},
    "claude":   {"type": "claude_cli"},
    "codex":    {"type": "codex_cli"},
    "anthropic": {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
    "gemini":   {"type": "gemini", "api_key_env": "GEMINI_API_KEY"},
    "openai":   {"type": "openai_compat", "api_key_env": "OPENAI_API_KEY"},
    "lmstudio": {"type": "openai_compat", "base_url": "http://localhost:1234/v1"},
    "ollama":   {"type": "openai_compat", "base_url": "http://localhost:11434/v1"},
}

CLI_BINARIES = {"agy": "agy", "claude_cli": "claude", "codex_cli": "codex"}


def parse_spec(spec: str) -> tuple[str, str | None]:
    name, _, model = spec.partition(":")
    return name.strip(), (model.strip() or None) if model.strip() not in ("", "default") else None


def provider_config(name: str, custom: dict) -> dict:
    cfg = dict(BUILTIN.get(name, {}))
    cfg.update(custom.get(name, {}))
    if "type" not in cfg:
        raise ProviderError(f"unknown provider `{name}` (known: {', '.join(sorted(set(BUILTIN) | set(custom)))})")
    return cfg


def create_provider(spec: str, custom: dict | None = None) -> Provider:
    from .api_providers import AnthropicProvider, GeminiProvider, OpenAICompatProvider
    from .cli_providers import AgyProvider, ClaudeCliProvider, CodexCliProvider

    name, model = parse_spec(spec)
    cfg = provider_config(name, custom or {})
    model = model or cfg.get("model")
    api_key = os.environ.get(cfg["api_key_env"]) if cfg.get("api_key_env") else None
    t = cfg["type"]
    if t == "agy":
        return AgyProvider(name, model)
    if t == "claude_cli":
        return ClaudeCliProvider(name, model)
    if t == "codex_cli":
        return CodexCliProvider(name, model)
    if t == "anthropic":
        return AnthropicProvider(name, model, api_key=api_key, effort=cfg.get("effort"))
    if t == "gemini":
        return GeminiProvider(name, model, api_key=api_key)
    if t == "openai_compat":
        return OpenAICompatProvider(name, model, base_url=cfg.get("base_url"), api_key=api_key)
    raise ProviderError(f"unknown provider type `{t}`")


def availability(name: str, custom: dict | None = None) -> tuple[bool, str]:
    """Cheap check whether a provider can be used right now."""
    try:
        cfg = provider_config(name, custom or {})
    except ProviderError as e:
        return False, str(e)
    t = cfg["type"]
    if t in CLI_BINARIES:
        path = shutil.which(CLI_BINARIES[t])
        return (True, f"CLI found: {path}") if path else (False, f"`{CLI_BINARIES[t]}` not installed")
    if cfg.get("base_url") and ("localhost" in cfg["base_url"] or "127.0.0.1" in cfg["base_url"]):
        import httpx
        try:
            httpx.get(cfg["base_url"].rstrip("/") + "/models", timeout=1).raise_for_status()
            return True, f"server reachable at {cfg['base_url']}"
        except Exception:
            return False, f"no server at {cfg['base_url']}"
    env = cfg.get("api_key_env")
    if env and not os.environ.get(env):
        hint = " (or `ant auth login`)" if t == "anthropic" else ""
        return False, f"${env} not set{hint}"
    return True, f"${env} set" if env else "configured"
