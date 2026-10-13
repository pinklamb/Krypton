"""Where change counts come from: `git log` over a recent window, or a JSON file you supply.

I/O lives here and in cli.py, never in engine/. The counts feed engine/volatility.py.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path


class HistoryError(Exception):
    """The history could not be read; the message says what to do about it."""


def _git_lines(root: Path, *args: str) -> list[str] | None:
    """Output lines of a git command run in root; None if git or the repository is missing."""
    try:
        done = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=off", *args], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout.decode("utf-8", errors="replace").splitlines()


def commit_counts(root: Path, window: int) -> Counter[str]:
    """Commits touching each path (relative to root) among the last `window` commits."""
    lines = _git_lines(root, "log", f"-n{window}", "--name-only", "--pretty=format:", "--relative", "--", ".")
    if lines is None:
        raise HistoryError(f"cannot read git history in {root}; run inside a git repository or pass --volatility FILE.json")
    return Counter(line for line in lines if line)


def uncommitted(root: Path) -> set[str]:
    """Paths (relative to root) that are modified, staged or untracked right now."""
    changed = _git_lines(root, "diff", "--name-only", "--relative", "HEAD", "--", ".") or []
    untracked = _git_lines(root, "ls-files", "--others", "--exclude-standard") or []
    return {line for line in [*changed, *untracked] if line}


def load_counts(path: Path) -> dict[str, int]:
    """A JSON object mapping a relative path to its change count (any non-negative number)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HistoryError(f"cannot read volatility file {path}: {error}") from error
    if not isinstance(data, dict) or not all(isinstance(v, (int, float)) and v >= 0 for v in data.values()):
        raise HistoryError(f"{path}: expected a JSON object of path -> non-negative change count")
    return data
