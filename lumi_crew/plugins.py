"""
Local provider plugins.

Every `*.py` file in ~/.config/lumi-crew/plugins/ (next to state.json) is loaded at start-up and may register
additional providers. A plugin file defines

    def register(api):
        api.add_provider(name="myprov", type="myprov_cli", factory=lambda name, model: MyProvider(name, model),
                         label="CLI · My Provider", binary="mycli", list_models=..., status=..., ...)

`api` also exposes the building blocks a provider needs (TextToolProvider, run_cli, usage, Status, Step …).
Plugins are your own local code: they are not part of Lumi Crew and run with your user rights.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import state_path


@dataclass
class ProviderPlugin:
    name: str                                   # spec prefix, e.g. "myprov" in "myprov:model"
    type: str                                   # provider type key
    factory: Callable[[str, str | None], Any]   # (name, model) -> Provider
    label: str = "Plugin"                       # shown in the /agent picker
    binary: str | None = None                   # CLI binary; used for availability checks
    picker: bool = True                         # offer it in /agent, /usage and `lumi-crew setup`
    list_models: Callable[[], Awaitable[list[tuple[str, str]]]] | None = None
    status: Callable[[], Awaitable[Any]] | None = None          # -> onboarding.Status
    account_info: Callable[[], Awaitable[list[str]]] | None = None
    fallbacks: list[tuple[str, str]] = field(default_factory=list)  # suggestions when another model hits a limit
    source: str = ""


PLUGINS: dict[str, ProviderPlugin] = {}
ERRORS: list[str] = []
_loaded = False


class PluginAPI:
    """What a plugin's register(api) receives."""

    def __init__(self, source: str):
        self.source = source
        from . import usage
        from .onboarding import Status, Step
        from .providers.base import LimitError, Message, Provider, ProviderError, Reply, is_limit_message
        from .providers.cli_providers import run_cli
        from .providers.text_tools import TextToolProvider
        self.usage, self.Status, self.Step = usage, Status, Step
        self.Provider, self.TextToolProvider, self.Message, self.Reply = Provider, TextToolProvider, Message, Reply
        self.ProviderError, self.LimitError, self.is_limit_message, self.run_cli = (
            ProviderError, LimitError, is_limit_message, run_cli)

    def add_provider(self, **kwargs) -> ProviderPlugin:
        plugin = ProviderPlugin(source=self.source, **kwargs)
        PLUGINS[plugin.name] = plugin
        return plugin


def plugin_dir() -> Path:
    return state_path().parent / "plugins"


def load_plugins(force: bool = False) -> list[str]:
    """Load all plugin files once. Returns error messages (a broken plugin never stops Lumi Crew)."""
    global _loaded
    if _loaded and not force:
        return ERRORS
    _loaded = True
    PLUGINS.clear()
    ERRORS.clear()
    directory = plugin_dir()
    if not directory.is_dir():
        return ERRORS
    for path in sorted(directory.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(f"lumi_crew_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not hasattr(module, "register"):
                raise AttributeError("no register(api) function")
            module.register(PluginAPI(str(path)))
        except Exception as e:
            ERRORS.append(f"Plugin {path.name}: {type(e).__name__}: {e}")
    return ERRORS


def get(name: str) -> ProviderPlugin | None:
    load_plugins()
    return PLUGINS.get(name)


def by_type(ptype: str) -> ProviderPlugin | None:
    load_plugins()
    return next((p for p in PLUGINS.values() if p.type == ptype), None)
