"""Every test starts from the plain local-only setup, whatever the developer has exported or put in .env."""
import pytest

from backend.settings import ENV_FILE_VAR, KNOWN


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch, tmp_path_factory):
    for name in KNOWN:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(ENV_FILE_VAR, str(tmp_path_factory.mktemp("no-env") / "absent.env"))
