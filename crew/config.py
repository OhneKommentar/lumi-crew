"""Loads crew.toml (./crew.toml, then ~/.config/crew/crew.toml)."""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROLES = ["orchestrator", "clarifier", "planner", "coder", "reviewer", "tester", "researcher"]
MODES = ["manual", "accept-edits", "auto", "plan"]


@dataclass
class Config:
    default: str = "agy:gemini-3.8-flash-medium"
    mode: str = "manual"
    agents: dict[str, str] = field(default_factory=dict)        # role -> "provider:model"
    providers: dict[str, dict] = field(default_factory=dict)    # custom provider definitions
    mcp: dict[str, dict] = field(default_factory=dict)          # name -> {url = ...}
    max_steps: int = 25
    max_delegations: int = 15
    max_review_rounds: int = 3
    shell_timeout: int = 120
    path: Path | None = None

    def spec_for(self, role: str) -> str:
        return self.agents.get(role, self.default)


def find_config(workspace: Path, explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    for p in (workspace / "crew.toml", Path.cwd() / "crew.toml", Path.home() / ".config/crew/crew.toml"):
        if p.is_file():
            return p
    return None


def load_config(workspace: Path, explicit: str | None = None) -> Config:
    cfg = Config()
    path = find_config(workspace, explicit)
    if not path:
        return cfg
    data = tomllib.loads(path.read_text())
    cfg.path = path
    cfg.default = data.get("default", cfg.default)
    cfg.mode = data.get("mode", cfg.mode)
    cfg.agents = dict(data.get("agents", {}))
    cfg.providers = dict(data.get("providers", {}))
    cfg.mcp = dict(data.get("mcp", {}))
    limits = data.get("limits", {})
    for key in ("max_steps", "max_delegations", "max_review_rounds", "shell_timeout"):
        if key in limits:
            setattr(cfg, key, int(limits[key]))
    if cfg.mode not in MODES:
        raise ValueError(f"crew.toml: mode must be one of {MODES}")
    unknown = set(cfg.agents) - set(ROLES)
    if unknown:
        raise ValueError(f"crew.toml: unknown agents {sorted(unknown)} (known: {ROLES})")
    return cfg


# ----------------------------------------------------------------------------- saved choices
# Model choices made in the TUI (/agent, /model) survive restarts. They live outside the project
# (~/.config/crew/state.json, override with $CREW_STATE) and take precedence over crew.toml [agents].
def state_path() -> Path:
    return Path(os.environ.get("CREW_STATE") or Path.home() / ".config/crew/state.json")


def load_saved_models() -> dict[str, str]:
    try:
        data = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}
    agents = data.get("agents", {}) if isinstance(data, dict) else {}
    return {r: s for r, s in agents.items() if r in ROLES and isinstance(s, str) and s}


def save_models(models: dict[str, str]):
    path = state_path()
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["agents"] = {r: models[r] for r in ROLES if r in models}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)
