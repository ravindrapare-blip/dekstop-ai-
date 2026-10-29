import pytest
from fastapi.testclient import TestClient

from backend.config import load_config
from backend.main import app, check_host


def test_health():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "phase": 8}


def test_config_loads_and_is_loopback_only():
    cfg = load_config()
    for section in ("backend", "browser", "automation"):
        assert cfg[section]["host"] == "127.0.0.1"
    assert check_host(cfg["backend"]["host"]) == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", ""])
def test_non_loopback_host_is_refused(host):
    with pytest.raises(SystemExit):
        check_host(host)


def test_ports_are_distinct():
    cfg = load_config()
    ports = [cfg[s]["port"] for s in ("backend", "browser", "automation")]
    assert len(set(ports)) == len(ports)
