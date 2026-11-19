"""Configuration: lumi-crew.toml (project folder, then ~/.config/lumi-crew/), plus saved state."""
from __future__ import annotations

import json
import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path.home() / ".config" / "lumi-crew"
LEGACY_DIR = Path.home() / ".config" / "crew"          # before the rename to Lumi Crew
CONFIG_NAMES = ("lumi-crew.toml", "crew.toml")        # crew.toml still works

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
    candidates = [d / n for d in (workspace, Path.cwd()) for n in CONFIG_NAMES]
    candidates += [APP_DIR / "lumi-crew.toml", LEGACY_DIR / "crew.toml"]
    for p in candidates:
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
        raise ValueError(f"{path.name}: mode must be one of {MODES}")
    unknown = set(cfg.agents) - set(ROLES)
    if unknown:
        raise ValueError(f"{path.name}: unknown agents {sorted(unknown)} (known: {ROLES})")
    return cfg


# ----------------------------------------------------------------------------- saved choices
# Model choices made in the TUI (/agent, /model) survive restarts. They live outside the project
# (~/.config/lumi-crew/state.json, override with $LUMI_CREW_STATE) and take precedence over
# lumi-crew.toml [agents]. Chats are stored next to it.
def state_path() -> Path:
    override = os.environ.get("LUMI_CREW_STATE") or os.environ.get("CREW_STATE")
    if override:
        return Path(override)
    migrate_legacy()
    return APP_DIR / "state.json"


def migrate_legacy():
    """One-time copy of saved models + chats from ~/.config/crew (pre-rename) to ~/.config/lumi-crew."""
    if APP_DIR.exists() or not LEGACY_DIR.is_dir():
        return
    try:
        shutil.copytree(LEGACY_DIR, APP_DIR)
    except OSError:
        pass


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
