"""Analyze a connected visitor's repository: fetch its source into memory, score it in a child process.

Nothing is written to disk and nothing from the repository is run. Visitors are limited to one analysis
every few seconds, and the server runs only a couple at once.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import auth
from repo_fetch import Limits, ScanError, fetch_sources

WORKER = Path(__file__).with_name("scan_worker.py")
WORKER_SECONDS = 60
PASSED_ENV = ("PATH", "SYSTEMROOT", "LANG", "LC_ALL")


def worker_env() -> dict[str, str]:
    """A minimal environment for the child; ARGON_SRC (a source checkout) is only for running without an install."""
    env = {name: os.environ[name] for name in PASSED_ENV if name in os.environ}
    if os.environ.get("ARGON_SRC"):
        env["PYTHONPATH"] = os.environ["ARGON_SRC"]
    return env


def run_worker(sources: dict[str, str], seconds: int = WORKER_SECONDS) -> dict:
    """Score the sources in a child process started in an empty folder; its JSON summary."""
    with tempfile.TemporaryDirectory() as empty:
        try:
            done = subprocess.run([sys.executable, str(WORKER)], input=json.dumps(sources).encode(), capture_output=True, timeout=seconds, cwd=empty, env=worker_env())
        except subprocess.TimeoutExpired as error:
            raise ScanError(504, "This repository took too long to analyze.") from error
    if done.returncode != 0:
        print(f"scan worker exited with {done.returncode}", flush=True)
        missing = b"No module named 'argon'" in done.stderr
        raise ScanError(503 if missing else 500, "The analysis engine is not installed on this server." if missing else "The analysis failed.")
    return json.loads(done.stdout)


def analyze(cfg: auth.Config, token: str, full_name: str, limits: Limits = Limits()) -> dict:
    """Fetch and score a repository; the summary numbers, or ScanError."""
    sources = fetch_sources(cfg, token, full_name, limits)
    if not sources:
        raise ScanError(422, "No Python, JavaScript or TypeScript source files were found in that repository.")
    return {"repo": full_name, **run_worker(sources)}


class Gate:
    """Allows a few analyses at once and one per visitor every `cooldown` seconds."""

    def __init__(self, parallel: int = 2, cooldown: float = 15.0) -> None:
        self.slots, self.cooldown, self._last, self._lock = threading.BoundedSemaphore(parallel), cooldown, {}, threading.Lock()

    def start(self, who: str) -> None:
        """Reserve a slot for `who` or raise ScanError(429)."""
        with self._lock:
            wait = self._last.get(who, 0) + self.cooldown - time.monotonic()
            if wait > 0:
                raise ScanError(429, f"Please wait {int(wait) + 1} seconds before analyzing again.")
            if not self.slots.acquire(blocking=False):
                raise ScanError(429, "The server is busy with other analyses. Try again in a minute.")
            self._last = {k: v for k, v in self._last.items() if v + self.cooldown > time.monotonic()}
            self._last[who] = time.monotonic()

    def finish(self) -> None:
        """Release the slot taken by start()."""
        self.slots.release()
