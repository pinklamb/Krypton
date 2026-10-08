"""The PR-comment body (master §6.1 template, plus skips, markers and fragmentation)."""

from __future__ import annotations

from argon.engine.config import DEFAULT_CONFIG, EngineConfig
from argon.schemas import EngineResult, FileFinding

RULE_NAMES = {
    "concentration_rule": "Concentration Rule",
    "coupling_hotspot": "Coupling Hotspot",
    "semantic_path_logic": "Semantic Path Logic",
    "unparseable": "Unparseable",
}


def build_summary_markdown(
    result: EngineResult,
    cfg: EngineConfig = DEFAULT_CONFIG,
    worst_function: dict[str, str] | None = None,
) -> str:
    """worst_function maps a filename to the function whose rating flagged it."""
    worst_function = worst_function or {}
    lines = [f"### Argon Structural Review -- Purity Score: {result.purity_score}/100", ""]
    flagged = [finding for finding in result.findings if finding.flagged]
    if flagged:
        lines += ["| File | Issue | Rule |", "|---|---|---|"]
        for finding in flagged:
            issue = _issue(finding, cfg, worst_function.get(finding.filename, "<module>"))
            lines.append(f"| {finding.filename} | {issue} | {RULE_NAMES[finding.flag_reason]} |")
        lines.append("")
    lines.append(f"{result.flagged_file_count} of {len(result.findings)} changed files flagged.")
    if result.fragmentation_flagged:
        files = ", ".join(result.fragmentation_cluster)
        lines += ["", f"Fragmentation: these files import each other heavily: {files}."]
    if result.skipped_files:
        skipped = ", ".join(f"{skip.filename} ({skip.reason.replace('_', ' ')})" for skip in result.skipped_files)
        lines += ["", f"Skipped: {skipped}."]
    lines += _unchecked_section(result.errors)
    if result.ignored_generated_markers:
        marked = ", ".join(result.ignored_generated_markers)
        lines += ["", f"Generated markers ignored (path not in generated_path_globs): {marked}."]
    return "\n".join(lines) + "\n"


def _issue(finding: FileFinding, cfg: EngineConfig, function_name: str) -> str:
    """The Issue column text for a flagged file."""
    reason = finding.flag_reason
    if reason == "concentration_rule":
        return f"{finding.effective_lines} effective lines, {finding.async_boundary_count} async boundaries"
    if reason == "semantic_path_logic":
        rating = round(finding.logic_complexity_rating)
        return f"Logic complexity {rating} (threshold {cfg.active_threshold:g}) in `{function_name}()`"
    if reason == "coupling_hotspot":
        return f"Coupled to {finding.coupling_weight} other files (threshold {cfg.coupling_weight_threshold}); split its wiring"
    if reason == "unparseable":
        return f"{finding.error_coverage:.0%} of the file could not be parsed"
    return "not analyzed"


def _unchecked_section(errors: dict[str, str]) -> list[str]:
    """Each error code with its explanation; the score leaves those files out."""
    if not errors:
        return []
    return ["", "**Not checked** (the purity score above does not include these files):", *[f"- `{code}`: {text}" for code, text in errors.items()]]
