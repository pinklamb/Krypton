"""run_local: skip -> parse -> analyze -> per-file rules -> fragmentation -> score (spec §10).

Pure: takes {POSIX path: bytes} and returns an EngineResult. No disk, git or network access,
so the Phase 1 worker can call it unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from krypton.engine.ast_counters import AstMetrics, FunctionStats, TooWide, analyze_tree
from krypton.engine.config import DEFAULT_CONFIG, EngineConfig, matches_any
from krypton.engine.coupling import coupling_links, coupling_weights, namespace_edges, partners
from krypton.engine.hints import Hint, file_hints, function_hints
from krypton.engine.imports import resolve_imports
from krypton.engine.languages import decode_source, language_for, parse_source
from krypton.engine.lines import LineWeights, non_blank_weights, physical_lines
from krypton.engine.rules import (
    check_concentration_rule,
    check_coupling_rule,
    check_file_overload,
    check_function_overload,
    check_fragmentation_rule,
    check_semantic_path_rule,
    compute_purity_score,
    determine_check_conclusion,
    overhead_points,
    rating_master,
    rating_nesting,
)
from krypton.engine.summary import build_summary_markdown
from krypton.engine.unchecked import FILE_TOO_LARGE, LINE_TOO_LONG, NODE_TOO_WIDE, Unchecked, build_errors
from krypton.engine.vendored import looks_vendored
from krypton.schemas import FLAG_PRIORITY, EngineResult, FileFinding, FunctionFinding, SkippedFile

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
    hints: tuple[Hint, ...] = ()  # for heavy functions


def run_local(
    files: dict[str, bytes],
    altered: dict[str, set[int]] | None = None,
    cfg: EngineConfig = DEFAULT_CONFIG,
    change_frequency: Mapping[str, float] | None = None,
) -> EngineResult:
    """altered maps a path to its altered line numbers; None means every line is altered.

    change_frequency (path -> 0.0..1.0, see engine/volatility.py) is recorded on each finding
    and never affects a score.
    """
    skipped, markers, unchecked, analyzed = [], [], [], {}
    for filename in sorted(files):
        skip = should_skip_file(filename, cfg)
        if skip is not None:
            skipped.append(skip)
            continue
        text = decode_source(files[filename])
        reason = looks_vendored(filename, text) if cfg.detect_vendored_content else None
        if reason is not None:
            skipped.append(SkippedFile(filename=filename, reason=reason))
            continue
        if _has_generated_marker(text, cfg):
            markers.append(filename)
        lines = None if altered is None else frozenset(altered.get(filename, ()))
        item = _analyze_file(filename, files[filename], text, lines, cfg)
        if isinstance(item, list):
            unchecked.extend(item)
        else:
            analyzed[filename] = item

    findings, edges, partners = _link_files(analyzed, change_frequency, cfg)
    fragmentation = check_fragmentation_rule(findings, edges, cfg)
    score = compute_purity_score(findings, fragmentation.triggered, cfg)
    result = EngineResult(
        purity_score=score,
        threshold=cfg.active_threshold,
        check_conclusion=determine_check_conclusion(score, cfg),
        rating_mode=cfg.rating_mode,
        findings=findings,
        flagged_file_count=sum(finding.flagged for finding in findings),
        skipped_file_count=len(skipped),
        skipped_files=skipped,
        errors=build_errors(unchecked, len(analyzed), cfg),
        **_ranked_hints(analyzed, findings, partners, cfg),
        ignored_generated_markers=markers,
        fragmentation_flagged=fragmentation.triggered,
        fragmentation_cluster=fragmentation.cluster,
        summary_markdown="",
    )
    worst = {filename: item.worst_function for filename, item in analyzed.items()}
    return result.model_copy(update={"summary_markdown": build_summary_markdown(result, cfg, worst)})


def _ranked_hints(analyzed: dict[str, _Analyzed], findings: list[FileFinding], partners: dict[str, set[str]], cfg: EngineConfig) -> dict:
    """The `hints` and `hint_regions` maps, heaviest hint first."""
    found = [hint for item in analyzed.values() for hint in item.hints] + file_hints(findings, partners, cfg)
    ranked = sorted(found, key=lambda hint: -hint.rating)
    return {
        "hints": {hint.key: hint.text for hint in ranked},
        "hint_regions": {hint.key: list(hint.region) for hint in ranked if hint.region},
    }


def _link_files(
    analyzed: dict[str, _Analyzed], change_frequency: Mapping[str, float] | None, cfg: EngineConfig
) -> tuple[list[FileFinding], set[tuple[str, str]], dict[str, set[str]]]:
    """Cross-file facts: import counts, change frequency, coupling weights; the import edges and each file's link partners."""
    scanned = frozenset(analyzed)
    edges: set[tuple[str, str]] = set()
    findings = []
    for filename, item in analyzed.items():
        targets = _imports_of(filename, item.metrics, scanned, cfg)
        edges.update((filename, target) for target in targets)
        update = {"relative_import_count": len(targets)}
        if change_frequency is not None:
            update["change_frequency"] = change_frequency.get(filename)
        findings.append(item.finding.model_copy(update=update))
    findings, partners = _with_coupling(findings, edges, analyzed, cfg)
    return findings, edges, partners


def _has_generated_marker(text: str, cfg: EngineConfig) -> bool:
    """A generated marker in the first 5 lines (reported, never a skip: A3)."""
    head = "\n".join(text.split("\n", MARKER_LINES)[:MARKER_LINES])
    return any(marker in head for marker in cfg.generated_markers)


def _with_coupling(
    findings: list[FileFinding], import_edges: set[tuple[str, str]], analyzed: dict[str, _Analyzed], cfg: EngineConfig
) -> tuple[list[FileFinding], dict[str, set[str]]]:
    """Record each file's coupling weight and flag hot spots that no other rule already flagged."""
    live = {name: item.metrics for name, item in analyzed.items() if item.metrics is not None}
    shared = namespace_edges(
        {name: m.namespace_defs for name, m in live.items()},
        {name: m.namespace_refs for name, m in live.items()},
        cfg.namespace_min_files,
    )
    names = [finding.filename for finding in findings]
    weights = coupling_weights(names, import_edges, shared)
    flagged = [_flag_coupling(finding.model_copy(update={"coupling_weight": weights[finding.filename]}), cfg) for finding in findings]
    return flagged, partners(coupling_links(set(names), [import_edges, shared]))


def _flag_coupling(finding: FileFinding, cfg: EngineConfig) -> FileFinding:
    """Coupling hot spot is the lowest-priority reason: it never replaces another flag."""
    if finding.flagged or not check_coupling_rule(finding, cfg):
        return finding
    return finding.model_copy(update={"flagged": True, "flag_reason": "coupling_hotspot"})


def _imports_of(filename: str, metrics: AstMetrics | None, scanned: frozenset[str], cfg: EngineConfig) -> set[str]:
    """Scanned files this file imports; none for fail-closed files."""
    if metrics is None:
        return set()
    return resolve_imports(filename, metrics.js_specifiers, metrics.python_imports, scanned, cfg)


def _analyze_file(
    filename: str, source: bytes, text: str, altered: frozenset[int] | None, cfg: EngineConfig
) -> _Analyzed | list[Unchecked]:
    """Not checked if too big (a list of refusals); fail closed if unparseable; else analyze and flag."""
    longest = _longest_line(text)
    refused = _size_refusals(filename, len(source), longest, cfg)
    if refused:
        return refused
    parsed = parse_source(filename, source)
    if parsed.error_coverage > cfg.max_error_coverage:
        return _fail_closed(filename, text, altered, cfg, "unparseable", parsed.error_coverage)
    try:
        metrics = analyze_tree(parsed, cfg, filename)
    except TooWide:
        return [Unchecked(filename, NODE_TOO_WIDE, len(source), longest)]
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
        **_wrapper_fields(functions, cfg),
    )
    hints = tuple(function_hints(filename, metrics, rated, cfg))
    return _Analyzed(_with_flags(finding, cfg, rated), metrics, worst.name, hints)


def _wrapper_fields(functions: list[FunctionFinding], cfg: EngineConfig) -> dict:
    """trivial_wrapper_count and overhead_tax for a file's named functions."""
    trivial = sum(f.branches <= cfg.wrapper_max_branches and f.effective_lines <= cfg.wrapper_max_lines for f in functions)
    return {"trivial_wrapper_count": trivial, "overhead_tax": overhead_points(trivial, len(functions), cfg)}


def _size_refusals(filename: str, size: int, longest: int, cfg: EngineConfig) -> list[Unchecked]:
    """Every size limit the file passes, so one config fix covers all of them."""
    codes = [FILE_TOO_LARGE] * (size > cfg.max_file_bytes) + [LINE_TOO_LONG] * (longest > cfg.max_line_chars)
    return [Unchecked(filename, code, size, longest) for code in codes]


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


def _with_flags(finding: FileFinding, cfg: EngineConfig, rows: list[FunctionFinding]) -> FileFinding:
    """Apply the per-file rules; keep the highest-priority reason."""
    checks = {
        "concentration_rule": check_concentration_rule(finding, cfg),
        "semantic_path_logic": check_semantic_path_rule(finding, cfg),
        "function_overload": check_function_overload(rows, cfg),
        "file_overload": check_file_overload(finding, cfg),
    }
    reasons = {name for name, hit in checks.items() if hit}
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
    """C2: not scored, counted as flagged, weighted by non-blank lines."""
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
