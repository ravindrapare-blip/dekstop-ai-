"""Starts and stops the Node browser worker. It is started the first time a browser action needs it."""
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from browserctl.client import WorkerClient, WorkerUnavailable

log = logging.getLogger("assistant.browser")


class WorkerLauncher:
    def __init__(self, *, client: WorkerClient, node: str, script: Path, port: int, token_file: Path,
                 chrome_path: str, profile_dir: Path, log_file: Path, idle_minutes: int = 30,
                 extra_args: tuple[str, ...] = (), start_timeout: float = 20.0,
                 popen=subprocess.Popen, sleep=time.sleep, clock=time.monotonic) -> None:
        self.client = client
        self.node = node
        self.script = Path(script)
        self.port = port
        self.token_file = Path(token_file)
        self.chrome_path = chrome_path
        self.profile_dir = Path(profile_dir)
        self.log_file = Path(log_file)
        self.idle_minutes = idle_minutes
        self.extra_args = tuple(extra_args)
        self.start_timeout = start_timeout
        self._popen = popen
        self._sleep = sleep
        self._clock = clock
        self._proc = None
        self._lock = threading.Lock()

    def diagnose(self) -> str | None:
        """What is missing, in words the user can act on; None when everything needed is there."""
        if not (shutil.which(self.node) or (os.sep in self.node and os.path.exists(self.node))):
            return "Node.js wasn't found. Install Node 18 or newer from nodejs.org, or set browser.node in config.yaml."
        if not self.script.exists() or not (self.script.parent.parent / "node_modules" / "puppeteer-core").is_dir():
            return "The browser helper isn't installed yet. Run this once:  cd browser && npm install"
        if not Path(self.chrome_path).exists():
            return f"I can't find Google Chrome at {self.chrome_path}. Set browser.chrome_path in config.yaml."
        return None

    def ensure(self) -> None:
        """Returns when the worker answers. Raises WorkerUnavailable, with the fix, if it can't be started."""
        with self._lock:
            if self.client.ping():
                return
            problem = self.diagnose()
            if problem:
                raise WorkerUnavailable(problem)
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            self._wait()

    def _start(self) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            self.node, str(self.script),
            "--port", str(self.port),
            "--token-file", str(self.token_file),
            "--chrome", self.chrome_path,
            "--profile", str(self.profile_dir.resolve()),
            "--idle-minutes", str(self.idle_minutes),
            *self.extra_args,
        ]
        with self.log_file.open("ab") as out:
            try:
                self._proc = self._popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                         cwd=self.script.parent.parent, start_new_session=True)
            except OSError as e:
                log.error("could not start the browser helper: %s", e)
                raise WorkerUnavailable("I couldn't start the browser helper.") from None

    def _wait(self) -> None:
        deadline = self._clock() + self.start_timeout
        while self._clock() < deadline:
            if self._proc.poll() is not None:
                self._proc = None
                raise WorkerUnavailable(
                    f"The browser helper stopped right after starting. Port {self.port} may be in use; see {self.log_file}.")
            if self.client.ping():
                return
            self._sleep(0.2)
        self._stop_process()
        raise WorkerUnavailable("The browser helper didn't start in time.")

    def stop(self) -> None:
        with self._lock:
            self._stop_process()

    def _stop_process(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
