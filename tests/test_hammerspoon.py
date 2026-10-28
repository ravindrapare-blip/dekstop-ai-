import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from automation.applescript import AppleScript, AppleScriptError
from automation.token import ensure_token, read_token
from backend.config import ROOT
from fakes import FakeRun

LUA = shutil.which("lua5.4") or shutil.which("lua")


# ---- the Lua side, run against a pretend Hammerspoon --------------------------------------------------------

def lua_can_run_the_tests() -> bool:
    if not LUA:
        return False
    return subprocess.run([LUA, "-e", "require('cjson')"], capture_output=True).returncode == 0


@pytest.mark.skipif(not lua_can_run_the_tests(), reason="needs lua 5.4 with lua-cjson (apt install lua5.4 lua-cjson)")
def test_the_hammerspoon_module_passes_its_lua_tests(tmp_path):
    (tmp_path / "data").mkdir()
    proc = subprocess.run(
        [LUA, str(ROOT / "tests" / "lua" / "test_hammerspoon.lua"), str(ROOT), str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip().endswith("ok")


@pytest.mark.skipif(not shutil.which("luac5.4") and not shutil.which("luac"), reason="needs luac")
def test_the_lua_files_have_valid_syntax():
    luac = shutil.which("luac5.4") or shutil.which("luac")
    for name in ("init.lua", "hotkeys.example.lua"):
        subprocess.run([luac, "-p", str(ROOT / "automation" / "hammerspoon" / name)], check=True)


def test_the_lua_server_is_restricted_to_local_use():
    source = (ROOT / "automation" / "hammerspoon" / "init.lua").read_text()
    assert 'setInterface("localhost")' in source           # hs.httpserver listens everywhere by default
    assert source.index("setInterface") < source.index("server:start()")
    for dangerous in ("os.execute", "io.popen", "hs.execute", "hs.task", "loadstring", "load("):
        assert dangerous not in source, dangerous          # no way to run commands or evaluate received text


# ---- the shared token -------------------------------------------------------------------------------------------------

def test_the_token_is_created_private_and_stable(tmp_path):
    path = tmp_path / "data" / ".token"
    first = ensure_token(path)
    assert len(first) >= 32 and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ensure_token(path) == first and read_token(path) == first


def test_a_short_or_damaged_token_is_replaced(tmp_path):
    path = tmp_path / ".token"
    path.write_text("short")
    assert read_token(path) is None
    token = ensure_token(path)
    assert len(token) >= 32 and read_token(path) == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ---- AppleScript -----------------------------------------------------------------------------------------------------------

def test_applescript_passes_the_app_as_an_argument():
    run = FakeRun()
    AppleScript(run=run).call("quit", "com.apple.Safari")
    (command,) = run.calls
    assert command[0] == "/usr/bin/osascript" and command[-1] == "com.apple.Safari"
    script = " ".join(command[1:-1])
    assert "item 1 of argv" in script and "com.apple.Safari" not in script


@pytest.mark.parametrize("bad", ['x" to quit\ntell application "Terminal', "a b", "", "-e", "../x", "x;y", "a" * 300])
def test_applescript_refuses_odd_identifiers(bad):
    run = FakeRun()
    with pytest.raises(AppleScriptError):
        AppleScript(run=run).call("quit", bad)
    assert run.calls == []


def test_applescript_only_runs_its_own_scripts():
    with pytest.raises(AppleScriptError):
        AppleScript(run=FakeRun()).call('do shell script "ls"', "com.apple.Safari")


def test_applescript_errors_are_wrapped():
    failing = lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="execution error: not allowed")  # noqa: E731
    with pytest.raises(AppleScriptError, match="not allowed"):
        AppleScript(run=failing).call("activate", "com.apple.Safari")

    def hang(*a, **k):
        raise subprocess.TimeoutExpired("osascript", 1)

    with pytest.raises(AppleScriptError):
        AppleScript(run=hang).call("activate", "com.apple.Safari")


# ---- the setup script ----------------------------------------------------------------------------------------------------------

def run_setup(hs_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HAMMERSPOON_DIR": str(hs_dir)}
    return subprocess.run([str(ROOT / "scripts" / "setup_hammerspoon.sh"), *args], capture_output=True, text=True, env=env)


def test_setup_adds_a_block_and_keeps_the_users_lines(tmp_path):
    hs = tmp_path / "hs"
    hs.mkdir()
    (hs / "init.lua").write_text('-- mine\nhs.alert("hello")\n')
    result = run_setup(hs)
    assert result.returncode == 0, result.stderr
    text = (hs / "init.lua").read_text()
    assert text.startswith('-- mine\nhs.alert("hello")\n')
    assert f'ASSISTANT_ROOT = "{ROOT}"' in text and "ASSISTANT_PORT = 8767" in text
    assert 'dofile(ASSISTANT_ROOT .. "/automation/hammerspoon/init.lua")' in text
    assert (hs / "init.lua.bak-assistant").read_text() == '-- mine\nhs.alert("hello")\n'


def test_setup_can_be_run_again_and_removed_cleanly(tmp_path):
    hs = tmp_path / "hs"
    hs.mkdir()
    original = '-- mine\nhs.alert("hello")\n'
    (hs / "init.lua").write_text(original)
    run_setup(hs)
    once = (hs / "init.lua").read_text()
    run_setup(hs)
    run_setup(hs)
    assert (hs / "init.lua").read_text() == once                 # nothing piles up
    assert once.count("BEGIN local-assistant") == 1
    assert run_setup(hs, "--remove").returncode == 0
    assert (hs / "init.lua").read_text() == original


def test_setup_works_when_hammerspoon_has_no_config_yet(tmp_path):
    hs = tmp_path / "fresh"
    assert run_setup(hs).returncode == 0
    assert "ASSISTANT_ROOT" in (hs / "init.lua").read_text()
    assert run_setup(hs, "--remove").returncode == 0
    assert "ASSISTANT" not in (hs / "init.lua").read_text()


def test_setup_quotes_awkward_paths(tmp_path):
    copy = tmp_path / 'my "project" \\ copy'
    (copy / "scripts").mkdir(parents=True)
    (copy / "automation" / "hammerspoon").mkdir(parents=True)
    (copy / "automation" / "hammerspoon" / "init.lua").write_text("return {}")
    shutil.copy(ROOT / "scripts" / "setup_hammerspoon.sh", copy / "scripts")
    shutil.copy(ROOT / "config.yaml", copy)
    hs = tmp_path / "hs"
    env = {**os.environ, "HAMMERSPOON_DIR": str(hs)}
    assert subprocess.run([str(copy / "scripts" / "setup_hammerspoon.sh")], env=env, capture_output=True).returncode == 0
    line = next(l for l in (hs / "init.lua").read_text().splitlines() if l.startswith("ASSISTANT_ROOT"))
    assert line == 'ASSISTANT_ROOT = "' + str(copy).replace("\\", "\\\\").replace('"', '\\"') + '"'
    if LUA:
        value = subprocess.run([LUA, "-e", line + "; io.write(ASSISTANT_ROOT)"], capture_output=True, text=True).stdout
        assert value == str(copy)                                 # Lua reads back exactly the original path


def test_setup_uses_the_port_from_config(tmp_path):
    copy = tmp_path / "copy"
    (copy / "scripts").mkdir(parents=True)
    (copy / "automation" / "hammerspoon").mkdir(parents=True)
    (copy / "automation" / "hammerspoon" / "init.lua").write_text("return {}")
    shutil.copy(ROOT / "scripts" / "setup_hammerspoon.sh", copy / "scripts")
    (copy / "config.yaml").write_text("backend:\n  port: 1\nautomation:\n  enabled: true\n  port: 9555\nfiles:\n  max_depth: 3\n")
    hs = tmp_path / "hs"
    subprocess.run([str(copy / "scripts" / "setup_hammerspoon.sh")], env={**os.environ, "HAMMERSPOON_DIR": str(hs)}, check=True, capture_output=True)
    assert "ASSISTANT_PORT = 9555" in (hs / "init.lua").read_text()


def test_the_configured_ports_agree():
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    assert cfg["automation"]["port"] == 8767
    ports = [cfg[s]["port"] for s in ("backend", "browser", "automation")]
    assert len(set(ports)) == 3
