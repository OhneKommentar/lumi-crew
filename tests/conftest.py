import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Never touch the real ~/.config/crew/state.json in tests."""
    path = tmp_path / "state.json"
    monkeypatch.setenv("CREW_STATE", str(path))
    return path
