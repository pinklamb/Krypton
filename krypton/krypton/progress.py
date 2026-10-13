"""Compare this run with the logged ones: did the score improve, and if not, why not.

Pure functions over the run log and the current snapshot. Every sentence is a fact the log
can back: ratings, whether the function's text changed, whether the hinted block is still there.
"""

from __future__ import annotations

from krypton.engine.config import EngineConfig
from krypton.runlog import Snapshot

TREND_RUNS = 6
INTACT = 0.8  # share of the hinted block's lines still present to call it untouched
MOVED = 0.6  # share that must appear in another function to call the block moved


def compare(snapshot: Snapshot, runs: list[dict], number: int, cfg: EngineConfig) -> dict[str, str]:
    """{key: sentence}: "purity" first, then each heavy function's movement since the last run."""
    now = snapshot.record
    previous = runs[-1] if runs else None
    out = {"purity": _purity_line(now, runs, number, previous)}
    if previous is None:
        return out
    for ident, current in now["functions"].items():
        before = previous["functions"].get(ident)
        out[ident] = _new_line(current) if before is None else _movement(ident, current, before, snapshot, runs, cfg)
    for ident in previous["functions"].keys() - now["functions"].keys():
        out[ident] = f"no longer heavy (was {previous['functions'][ident]['rating']:.0f})"
    return out


def _purity_line(now: dict, runs: list[dict], number: int, previous: dict | None) -> str:
    """Run number, the purity trend over the last runs and the flagged-file change."""
    trend = " -> ".join(str(run["purity"]) for run in [*runs, now][-TREND_RUNS:])
    if previous is None:
        return f"run {number}: purity {now['purity']}, history started"
    return f"run {number}: purity {trend}; flagged files {previous['flagged']} -> {now['flagged']}"


def _new_line(current: dict) -> str:
    """A function that became heavy since the last run."""
    return f"newly heavy (rating {current['rating']:.0f})"


def _movement(ident: str, current: dict, before: dict, snapshot: Snapshot, runs: list[dict], cfg: EngineConfig) -> str:
    """Improved, or unchanged / worse with the reason; stalled once it has not improved for stall_runs runs."""
    old, new = before["rating"], current["rating"]
    if new <= old * cfg.improvement_ratio:
        return f"improved {old:.0f} -> {new:.0f} ({1 - new / old:.0%} lower)"
    still = _runs_without_improvement(ident, runs, cfg) + 1
    state = "worse" if new > old / cfg.improvement_ratio else "unchanged"
    head = f"STALLED for {still} runs at {new:.0f}" if still >= cfg.stall_runs else f"{state} {old:.0f} -> {new:.0f}"
    return f"{head}: {_diagnosis(ident, current, before, snapshot, runs[-1])}"


def _runs_without_improvement(ident: str, runs: list[dict], cfg: EngineConfig) -> int:
    """How many runs in a row, ending at the last logged one, left this function from improving."""
    count = 0
    for newer, older in zip(reversed(runs), reversed(runs[:-1])):
        a, b = newer["functions"].get(ident), older["functions"].get(ident)
        if a is None or b is None or a["rating"] <= b["rating"] * cfg.improvement_ratio:
            break
        count += 1
    return count


def _diagnosis(ident: str, current: dict, before: dict, snapshot: Snapshot, previous: dict) -> str:
    """Why it did not improve, from what changed in the function and in the block the hint named."""
    if current["body"] == before["body"]:
        return "the function was not edited since the last run"
    region = previous.get("regions", {}).get(ident)
    if region is None:
        return "it was edited, but the last run named no block to extract"
    lines = f"{region['lines'][0]}-{region['lines'][1]}"
    kept = _share_present(region["hashes"], snapshot.body_hashes.get(ident, set()))
    if kept >= INTACT:
        return f"the hinted block (lines {lines} last run) is still intact; the edits were elsewhere in the function"
    mover = _function_holding(region["hashes"], ident, snapshot)
    if mover:
        return f"the hinted block (lines {lines}) moved into {mover}, so the weight moved instead of shrinking"
    return f"the hinted block (lines {lines}) was changed but the rating did not drop; use the new hint"


def _share_present(hashes: list[str], body: set[str]) -> float:
    """The share of the block's line hashes that still appear in a function body."""
    return sum(h in body for h in hashes) / len(hashes) if hashes else 0.0


def _function_holding(hashes: list[str], ident: str, snapshot: Snapshot) -> str | None:
    """Another heavy function in the same file that now holds most of the block's lines."""
    file = ident.rsplit(":", 1)[0]
    for other, body in snapshot.body_hashes.items():
        if other != ident and other.rsplit(":", 1)[0] == file and _share_present(hashes, body) >= MOVED:
            return other.split(":", 1)[1]
    return None
