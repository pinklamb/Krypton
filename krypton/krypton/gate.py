"""Gate flexibility: functions the gate lets through instead of sending an agent back to fix them again.

Two rules, both opt-in on the command line, both recorded on the finding so the report says what was waved through:
`--no-worse` passes a function that is over the limit but no heavier than it was at the base ref (old tangle an agent
did not create), and `--handoff-stalled` stops blocking on a function that has not improved for `stall_runs` runs in the
`--history` log, so a person looks at it instead of an agent retrying forever. Waiver comments are handled in the engine.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from krypton.engine.config import EngineConfig
from krypton.engine.exemptions import Exemptions
from krypton.engine.rules import rating_master, rating_nesting
from krypton.engine.run import run_local
from krypton.runlog import function_id
from krypton.schemas import EngineResult, FunctionFinding

STALLED = "STALLED"


def active_rating(function: FunctionFinding, cfg: EngineConfig) -> float:
    """The rating the flag threshold applies to (over the altered lines when scoring only a change)."""
    return function.rating_nesting if cfg.rating_mode == "nesting" else function.rating_master


def full_rating(function: FunctionFinding, cfg: EngineConfig) -> float:
    """The same rating over all of the function's lines, so it can be compared with a run that scored the whole file."""
    if cfg.rating_mode == "nesting":
        return rating_nesting(function.score, function.effective_lines, function.outputs_effective)
    return rating_master(function.branches, function.effective_lines, function.outputs_effective)


def base_ratings(read_base: Callable[[str], str | None], names: Iterable[str], cfg: EngineConfig) -> dict[tuple[str, str], float]:
    """{(file, function): rating over all lines} for the files as they were at the base ref; files new since then are absent."""
    before = {name: text.encode("utf-8") for name in names if (text := read_base(name)) is not None}
    result = run_local(before, None, cfg)
    return {(finding.filename, function.name): full_rating(function, cfg) for finding in result.findings for function in finding.functions}


def no_worse(result: EngineResult, base: str, ratings: dict[tuple[str, str], float], cfg: EngineConfig) -> Exemptions:
    """Over-the-limit functions that are no heavier than the same function at the base ref."""
    limit = cfg.active_threshold
    found = {}
    for finding in result.findings:
        for function in finding.functions:
            was = ratings.get((finding.filename, function.name))
            if was is not None and active_rating(function, cfg) > limit and full_rating(function, cfg) <= was:
                found[(finding.filename, function.name)] = f"no worse than {base}"
    return found


def stalled(result: EngineResult, cfg: EngineConfig) -> Exemptions:
    """Functions the --history comparison calls STALLED, handed to a person."""
    names = {key for key, text in result.progress.items() if text.startswith(STALLED)}
    reason = f"handed off: no 10% improvement in {cfg.stall_runs} runs"
    return {(finding.filename, function.name): reason for finding in result.findings for function in finding.functions if function_id(finding.filename, function.name) in names}
