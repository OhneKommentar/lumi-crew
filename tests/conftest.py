import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Never touch the real ~/.config/lumi-crew (state, chats, keys) in tests."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("LUMI_CREW_STATE", str(path))
    return path


@pytest.fixture(autouse=True)
def providers_ready(monkeypatch):
    """CI machines have no agy/codex/claude: pickers treat every provider as set up unless a test says otherwise."""
    from lumi_crew.onboarding import Status
    import lumi_crew.tui.app as tui_app

    async def ready(provider, custom=None):
        return Status(True, "ok")
    monkeypatch.setattr(tui_app, "onboarding_status", ready)
