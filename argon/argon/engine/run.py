"""run_local: skip -> parse -> analyze -> per-file rules -> fragmentation -> score (spec §10).

Pure: takes {POSIX path: bytes} and returns an EngineResult. No disk, git or network access,
so the Phase 1 worker can call it unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from argon.engine.ast_counters import AstMetrics, FunctionStats, TooWide, analyze_tree
from argon.engine.config import DEFAULT_CONFIG, EngineConfig, matches_any
from argon.engine.imports import resolve_imports
from argon.engine.languages import decode_source, language_for, parse_source
from argon.engine.lines import LineWeights, non_blank_weights, physical_lines
from argon.engine.rules import (
    check_concentration_rule,
    check_fragmentation_rule,
    check_semantic_path_rule,
    compute_purity_score,
    determine_check_conclusion,
    rating_master,
    rating_nesting,
)
from argon.engine.summary import build_summary_markdown
from argon.schemas import FLAG_PRIORITY, EngineResult, FileFinding, FunctionFinding, SkippedFile

MARKER_LINES = 5


def should_skip_file(filename: str, cfg: EngineConfig = DEFAULT_CONFIG) -> SkippedFile | None:
    """Skip by path only (A3): an in-file @generated marker never skips a file on its own."""
    if language_for(filename) is None:
        return SkippedFile(filename=filename, reason="unsupported_extension")
    if matches_any(filename, cfg.vendored_path_globs):
        return SkippedFile(filename=filename, reason="vendored_path")
    if matches_any(filename, cfg.generated_path_globs):
        return SkippedFile(filename=filename, reason="generated_path")
    return None


@dataclass(frozen=True)
class _Analyzed:
    """One file's finding, its metrics (None if fail-closed) and its worst function's name."""

    finding: FileFinding
    metrics: AstMetrics | None  # None for fail-closed files
    worst_function: str


def run_local(
    files: dict[str, bytes],
    altered: dict[str, set[int]] | None = None,
    cfg: EngineConfig = DEFAULT_CONFIG,
) -> EngineResult:
    """altered maps a path to its altered line numbers; None means every line is altered."""
    skipped, markers, analyzed = [], [], {}
    for filename in sorted(files):
        skip = should_skip_file(filename, cfg)
        if skip is not None:
            skipped.append(skip)
            continue
        source = files[filename]
        text = decode_source(source)
        if _has_generated_marker(text, cfg):
            markers.append(filename)
        lines = None if altered is None else frozenset(altered.get(filename, ()))
        analyzed[filename] = _analyze_file(filename, source, text, lines, cfg)

    scanned = frozenset(analyzed)
    edges = set()
    findings = []
    for filename, item in analyzed.items():
        targets = _imports_of(filename, item.metrics, scanned, cfg)
        edges.update((filename, target) for target in targets)
        findings.append(item.finding.model_copy(update={"relative_import_count": len(targets)}))

    fragmentation = check_fragmentation_rule(findings, edges, cfg)
    score = compute_purity_score(findings, fragmentation.triggered, cfg)
    result = EngineResult(
        purity_score=score,
        check_conclusion=determine_check_conclusion(score, cfg),
        rating_mode=cfg.rating_mode,
        findings=findings,
        flagged_file_count=sum(finding.flagged for finding in findings),
        skipped_file_count=len(skipped),
        skipped_files=skipped,
        ignored_generated_markers=markers,
        fragmentation_flagged=fragmentation.triggered,
        fragmentation_cluster=fragmentation.cluster,
        summary_markdown="",
    )
    worst = {filename: item.worst_function for filename, item in analyzed.items()}
    return result.model_copy(update={"summary_markdown": build_summary_markdown(result, cfg, worst)})


def _has_generated_marker(text: str, cfg: EngineConfig) -> bool:
    """A generated marker in the first 5 lines (reported, never a skip: A3)."""
    head = "\n".join(text.split("\n", MARKER_LINES)[:MARKER_LINES])
    return any(marker in head for marker in cfg.generated_markers)


def _imports_of(filename: str, metrics: AstMetrics | None, scanned: frozenset[str], cfg: EngineConfig) -> set[str]:
    """Scanned files this file imports; none for fail-closed files."""
    if metrics is None:
        return set()
    return resolve_imports(filename, metrics.js_specifiers, metrics.python_imports, scanned, cfg)


def _analyze_file(
    filename: str, source: bytes, text: str, altered: frozenset[int] | None, cfg: EngineConfig
) -> _Analyzed:
    """Fail closed if oversize / unparseable; else analyze, rate functions and set flags."""
    if len(source) > cfg.max_file_bytes or _longest_line(text) > cfg.max_line_chars:
        return _fail_closed(filename, text, altered, cfg, "oversize", error_coverage=0.0)
    parsed = parse_source(filename, source)
    if parsed.error_coverage > cfg.max_error_coverage:
        return _fail_closed(filename, text, altered, cfg, "unparseable", parsed.error_coverage)
    try:
        metrics = analyze_tree(parsed, cfg, filename)
    except TooWide:
        return _fail_closed(filename, text, altered, cfg, "oversize", error_coverage=0.0)
    weights = metrics.line_weights
    functions = [_function_finding(stats, weights, altered) for stats in metrics.functions]
    module = _function_finding(metrics.module, weights, altered)
    rated = [*functions, module]
    worst = max(rated, key=lambda function: _active_rating(function, cfg))
    finding = FileFinding(
        filename=filename,
        line_count=metrics.line_count,
        async_boundary_count=metrics.async_boundary_count,
        cyclomatic_branches=metrics.cyclomatic_branches,
        lines_altered=_physical_altered(metrics.line_count, altered),
        determinate_outputs=sum(function.outputs_effective for function in functions),
        relative_import_count=0,  # filled once every file is known
        logic_complexity_rating=_active_rating(worst, cfg),
        flagged=False,
        language=metrics.language,
        effective_lines=weights.total,
        effective_lines_altered=weights.altered_total(1, metrics.line_count, altered),
        nesting_score=metrics.nesting_score,
        max_depth=metrics.max_depth,
        parse_error=parsed.has_error,
        error_coverage=parsed.error_coverage,
        functions=functions,
    )
    return _Analyzed(_with_flags(finding, cfg), metrics, worst.name)


def _longest_line(text: str) -> int:
    """Characters in the longest line, one linear pass (the guard against minified input)."""
    return max(map(len, text.splitlines()), default=0)


def _function_finding(stats: FunctionStats, weights: LineWeights, altered: frozenset[int] | None) -> FunctionFinding:
    """FunctionStats plus effective lines and both ratings, as a FunctionFinding."""
    altered_lines = stats.effective_lines(weights, altered)
    return FunctionFinding(
        name=stats.name,
        start_line=stats.start_line,
        end_line=stats.end_line,
        score=stats.score,
        branches=stats.branches,
        max_depth=stats.max_depth,
        effective_lines=stats.effective_lines(weights),
        effective_lines_altered=altered_lines,
        determinate_outputs=stats.determinate_outputs,
        outputs_effective=stats.outputs_effective,
        rating_master=rating_master(stats.branches, altered_lines, stats.outputs_effective),
        rating_nesting=rating_nesting(stats.score, altered_lines, stats.outputs_effective),
        nested=stats.nested,
        saturated=stats.saturated,
    )


def _active_rating(function: FunctionFinding, cfg: EngineConfig) -> float:
    """The function's rating in the active rating_mode."""
    return function.rating_nesting if cfg.rating_mode == "nesting" else function.rating_master


def _with_flags(finding: FileFinding, cfg: EngineConfig) -> FileFinding:
    """Apply the concentration and semantic-path rules; keep the highest-priority reason."""
    reasons = set()
    if check_concentration_rule(finding, cfg):
        reasons.add("concentration_rule")
    if check_semantic_path_rule(finding, cfg):
        reasons.add("semantic_path_logic")
    reason = next((name for name in FLAG_PRIORITY if name in reasons), None)
    return finding.model_copy(update={"flagged": reason is not None, "flag_reason": reason})


def _physical_altered(line_count: int, altered: frozenset[int] | None) -> int:
    """Physical altered line count, for display (all lines when altered is None)."""
    if altered is None:
        return line_count
    return sum(1 for line in altered if 1 <= line <= line_count)


def _fail_closed(
    filename: str,
    text: str,
    altered: frozenset[int] | None,
    cfg: EngineConfig,
    reason: str,
    error_coverage: float,
) -> _Analyzed:
    """C2 / C4: not scored, counted as flagged, weighted by non-blank lines."""
    line_count = len(physical_lines(text))
    weights = non_blank_weights(text, cfg.effective_line_width)
    finding = FileFinding(
        filename=filename,
        line_count=line_count,
        async_boundary_count=0,
        cyclomatic_branches=0,
        lines_altered=_physical_altered(line_count, altered),
        determinate_outputs=0,
        relative_import_count=0,
        logic_complexity_rating=0.0,
        flagged=True,
        flag_reason=reason,
        language=language_for(filename),
        effective_lines=weights.total,
        effective_lines_altered=weights.altered_total(1, line_count, altered),
        nesting_score=0,
        max_depth=0,
        parse_error=reason == "unparseable",
        error_coverage=error_coverage,
        functions=[],
    )
    return _Analyzed(finding, None, "<module>")
