"""The run log behind --history: one JSON line per run, and the snapshot taken from a result.

I/O lives here and in cli.py. A snapshot keeps only numbers, names and short hashes of
normalized source lines, never source text, so the log is safe to commit or cache.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from krypton.schemas import EngineResult

RUNS_KEPT = 200
HASH_LENGTH = 8
MAX_REGION_HASHES = 400


@dataclass
class Snapshot:
    """What this run adds to the log, plus the per-function line hashes used for comparison only."""

    record: dict
    body_hashes: dict[str, set[str]] = field(default_factory=dict)  # function id -> hashes of its lines now


def function_id(file: str, name: str) -> str:
    """A function's identity across runs: its file and qualified name, not its line (lines move)."""
    return f"{file}:{name}"


def _line_hash(line: str) -> str:
    """A short hash of a line with its surrounding whitespace removed."""
    return hashlib.sha1(line.strip().encode("utf-8", errors="replace")).hexdigest()[:HASH_LENGTH]


def _hashes(lines: list[str]) -> list[str]:
    """Hashes of the non-blank lines, in order."""
    return [_line_hash(line) for line in lines if line.strip()]


def take_snapshot(result: EngineResult, sources: dict[str, str], scope: str) -> Snapshot:
    """The run's purity, flags, and each hinted function's rating, body hash and hinted block."""
    functions, regions, bodies = {}, {}, {}
    for finding, function, key in _hinted_functions(result):
        text_lines = sources.get(finding.filename, "").splitlines()
        ident = function_id(finding.filename, function.name)
        body = _hashes(text_lines[function.start_line - 1 : function.end_line])
        bodies[ident] = set(body)
        functions[ident] = {"rating": round(_rating(function, result), 1), "body": hashlib.sha1("".join(body).encode()).hexdigest()[:HASH_LENGTH]}
        if key in result.hint_regions:
            first, last = result.hint_regions[key]
            regions[ident] = {"lines": [first, last], "hashes": _hashes(text_lines[first - 1 : last])[:MAX_REGION_HASHES]}
    record = {
        "scope": scope,
        "purity": result.purity_score,
        "flagged": result.flagged_file_count,
        "files": len(result.findings),
        "functions": functions,
        "regions": regions,
    }
    return Snapshot(record, bodies)


def _hinted_functions(result: EngineResult) -> list:
    """(file finding, function finding, hint key) for every function that has a hint."""
    return [hit for finding in result.findings for hit in _hinted_in(finding, result.hints)]


def _hinted_in(finding, hints: dict[str, str]) -> list:
    """The hinted functions of one file, with their hint keys."""
    pairs = [(function, f"{finding.filename}:{function.start_line} {function.name}") for function in finding.functions]
    return [(finding, function, key) for function, key in pairs if key in hints]


def _rating(function, result: EngineResult) -> float:
    """The function's rating in the run's active rating mode."""
    return function.rating_nesting if result.rating_mode == "nesting" else function.rating_master


def read_runs(path: Path) -> list[dict]:
    """Every run in the log, oldest first; unreadable lines are skipped, a missing file is empty."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    runs = []
    for line in lines:
        try:
            runs.append(json.loads(line))
        except ValueError:
            continue
    return runs


def next_number(runs: list[dict]) -> int:
    """The number the next run will get."""
    return (runs[-1].get("run", len(runs)) if runs else 0) + 1


def append_run(path: Path, runs: list[dict], record: dict) -> None:
    """Write the log with this run added; the last RUNS_KEPT runs are kept."""
    kept = [*runs, {"run": next_number(runs), **record}][-RUNS_KEPT:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(run, separators=(",", ":")) + chr(10) for run in kept), encoding="utf-8")
