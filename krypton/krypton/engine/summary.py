"""The PR-comment body (master §6.1 template, plus skips, markers and fragmentation)."""

from __future__ import annotations

from itertools import chain

from krypton.engine.config import DEFAULT_CONFIG, EngineConfig
from krypton.schemas import EngineResult, FileFinding

NEWLINE = chr(10)
MAX_PR_HINTS = 5
MAX_EXEMPT_SHOWN = 8
RULE_NAMES = {
    "concentration_rule": "Concentration Rule",
    "coupling_hotspot": "Coupling Hotspot",
    "function_overload": "Function Overload",
    "file_overload": "File Overload",
    "semantic_path_logic": "Semantic Path Logic",
    "unparseable": "Unparseable",
}


def build_summary_markdown(
    result: EngineResult,
    cfg: EngineConfig = DEFAULT_CONFIG,
    worst_function: dict[str, str] | None = None,
) -> str:
    """worst_function maps a filename to the function whose rating flagged it."""
    lines = [f"### Krypton Structural Review -- Purity Score: {result.purity_score}/100", ""]
    lines += _flagged_table(result, cfg, worst_function or {})
    lines.append(f"{result.flagged_file_count} of {len(result.findings)} changed files flagged.")
    lines.append(_distance_line(result))
    lines += _hints_section(result.hints)
    lines += _notices(result)
    lines += _unchecked_section(result.errors)
    return NEWLINE.join(lines) + NEWLINE


def _flagged_table(result: EngineResult, cfg: EngineConfig, worst_function: dict[str, str]) -> list[str]:
    """The File / Issue / Rule table of flagged files, or nothing."""
    flagged = [finding for finding in result.findings if finding.flagged]
    if not flagged:
        return []
    rows = [
        f"| {finding.filename} | {_issue(finding, cfg, worst_function.get(finding.filename, '<module>'))} | {RULE_NAMES[finding.flag_reason]} |"
        for finding in flagged
    ]
    return ["| File | Issue | Rule |", "|---|---|---|", *rows, ""]


def _notices(result: EngineResult) -> list[str]:
    """Fragmentation, skipped files and ignored generated markers, each as a short paragraph."""
    notes = [_fragmentation_note(result), _skipped_note(result), _exempt_note(result), _markers_note(result)]
    return list(chain.from_iterable(("", note) for note in notes if note))


def _fragmentation_note(result: EngineResult) -> str | None:
    """The files in the fragmentation group, if the rule fired."""
    if not result.fragmentation_flagged:
        return None
    return f"Fragmentation: these files import each other heavily: {', '.join(result.fragmentation_cluster)}."


def _skipped_note(result: EngineResult) -> str | None:
    """Skipped files with their reasons."""
    if not result.skipped_files:
        return None
    return "Skipped: " + ", ".join(f"{skip.filename} ({skip.reason.replace('_', ' ')})" for skip in result.skipped_files) + "."


def _exempt_note(result: EngineResult) -> str | None:
    """Functions the gate was told to ignore, with the reason each one gives."""
    rows = [f"{finding.filename}:{function.start_line} {function.name} ({function.exempt_reason})" for finding in result.findings for function in finding.functions if function.exempt_reason]
    if not rows:
        return None
    shown = ", ".join(rows[:MAX_EXEMPT_SHOWN])
    return f"Exempt from the gate: {shown}{f' and {len(rows) - MAX_EXEMPT_SHOWN} more' if len(rows) > MAX_EXEMPT_SHOWN else ''}."


def _markers_note(result: EngineResult) -> str | None:
    """Generated markers that were seen but not honored."""
    if not result.ignored_generated_markers:
        return None
    return f"Generated markers ignored (path not in generated_path_globs): {', '.join(result.ignored_generated_markers)}."


def _issue(finding: FileFinding, cfg: EngineConfig, function_name: str) -> str:
    """The Issue column text for a flagged file."""
    reason = finding.flag_reason
    if reason == "concentration_rule":
        return f"{finding.effective_lines} effective lines, {finding.async_boundary_count} async boundaries"
    if reason == "semantic_path_logic":
        rating = round(finding.logic_complexity_rating)
        return f"Logic complexity {rating} (threshold {cfg.active_threshold:g}) in `{function_name}()`"
    if reason == "function_overload":
        return f"A function has {cfg.max_decisions_per_function}+ decisions or a nesting score of {cfg.max_function_score}+; split it by job"
    if reason == "file_overload":
        return f"Total nesting score {finding.nesting_score} (budget {cfg.max_file_weight}); split the file by responsibility"
    if reason == "coupling_hotspot":
        return f"Coupled to {finding.coupling_weight} other files (threshold {cfg.coupling_weight_threshold}); split its wiring"
    if reason == "unparseable":
        return f"{finding.error_coverage:.0%} of the file could not be parsed"
    return "not analyzed"


def _distance_line(result: EngineResult) -> str:
    """How many functions are over the flag threshold, and how far over the worst one is."""
    ratings = _function_ratings(result) + _module_ratings(result)
    over = [rating for rating in ratings if rating > result.threshold > 0]
    if not over:
        return f"All {len(ratings)} functions are under the threshold ({result.threshold:g})."
    return f"{len(over)} of {len(ratings)} functions are over the threshold ({result.threshold:g}); the worst is {max(over) / result.threshold:.1f}x over."


def _function_ratings(result: EngineResult) -> list[float]:
    """Every named function's rating in the active mode, leaving out the ones the gate was told to ignore."""
    functions = [function for finding in result.findings for function in finding.functions if function.exempt_reason is None]
    return [function.rating_nesting if result.rating_mode == "nesting" else function.rating_master for function in functions]


def _module_ratings(result: EngineResult) -> list[float]:
    """Top-level code is not a named function; count it when it is the heaviest part of its file."""
    heavier = [finding for finding in result.findings if finding.logic_complexity_rating > _heaviest_function(finding, result.rating_mode)]
    return [finding.logic_complexity_rating for finding in heavier]


def _heaviest_function(finding: FileFinding, mode: str) -> float:
    """The largest rating among a file's named functions in the active rating mode."""
    ratings = [function.rating_nesting if mode == "nesting" else function.rating_master for function in finding.functions if function.exempt_reason is None]
    return max(ratings, default=0.0)


def _hints_section(hints: dict[str, str]) -> list[str]:
    """The first few refactoring hints, heaviest first."""
    if not hints:
        return []
    return ["", "**Refactoring hints:**", *[f"- `{key}`: {text}" for key, text in list(hints.items())[:MAX_PR_HINTS]]]


def _unchecked_section(errors: dict[str, str]) -> list[str]:
    """Each error code with its explanation; the score leaves those files out."""
    if not errors:
        return []
    return ["", "**Not checked** (the purity score above does not include these files):", *[f"- `{code}`: {text}" for code, text in errors.items()]]
