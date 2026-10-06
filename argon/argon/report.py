"""Plain-text sections of the CLI output (spec §11). Pure: strings in, strings out."""

from __future__ import annotations

from argon.schemas import EngineResult, FileFinding, FunctionFinding

RATING_HEADERS = "rating_m  rating_n"


def warnings(result: EngineResult) -> list[str]:
    """Parse errors under the limit, saturated scores, ignored generated markers."""
    lines = [
        f"WARN {finding.filename}: syntax errors ({finding.error_coverage:.1%} of the file), scored anyway"
        for finding in result.findings
        if finding.parse_error and finding.flag_reason != "unparseable"
    ]
    lines += [
        f"WARN {filename}:{function.start_line} {function.name}: score saturated at the cap"
        for filename, function in _all_functions(result)
        if function.saturated
    ]
    lines += [
        f"WARN {filename}: generated marker ignored; add its path to generated_path_globs to skip it"
        for filename in result.ignored_generated_markers
    ]
    return lines


def _all_functions(result: EngineResult) -> list[tuple[str, FunctionFinding]]:
    """(filename, function) for every function row in the result."""
    return [(finding.filename, function) for finding in result.findings for function in finding.functions]


def file_table(result: EngineResult) -> str:
    """The file table, worst rating first."""
    rows = sorted(result.findings, key=lambda finding: (-finding.logic_complexity_rating, finding.filename))
    header = (
        f"{'eff':>6} {'altered':>7} {'branches':>8} {'nesting':>8} {'depth':>5} {'outputs':>7} "
        f"{'async':>5} {'imports':>7} {'rating':>10}  {'flag':<20} file"
    )
    return "\n".join([header, *(_file_row(finding) for finding in rows)])


def _file_row(finding: FileFinding) -> str:
    """One file-table row."""
    return (
        f"{finding.effective_lines:>6} {finding.effective_lines_altered:>7} {finding.cyclomatic_branches:>8} "
        f"{finding.nesting_score:>8} {finding.max_depth:>5} {finding.determinate_outputs:>7} "
        f"{finding.async_boundary_count:>5} {finding.relative_import_count:>7} "
        f"{finding.logic_complexity_rating:>10.1f}  {finding.flag_reason or '-':<20} {finding.filename}"
    )


def function_ranking(result: EngineResult, top: int) -> str:
    """§8.6: score desc, then lines desc, then filename and start line, so runs are identical."""
    rows = _all_functions(result)
    rows.sort(key=lambda row: (-row[1].score, -row[1].effective_lines, row[0], row[1].start_line))
    rows = [row for row in rows if row[1].score > 0][:top]
    if not rows:
        return "No functions with branches found."
    header = f"{'score':>7} {'branches':>8} {'depth':>5} {'lines':>6} {'rating_m':>10} {'rating_n':>10}  {'flags':<17} location / function"
    lines = [f"Top {len(rows)} functions by nesting-weighted score:", header]
    lines.extend(_function_row(filename, function) for filename, function in rows)
    if any(function.nested for _, function in rows):
        lines.append("  nested: a decision 3+ levels deep (effective); flatten with early returns or helpers")
    return "\n".join(lines)


def _function_row(filename: str, function: FunctionFinding) -> str:
    """One ranking row, with its flags."""
    flags = ",".join(name for name, on in (("nested", function.nested), ("saturated", function.saturated)) if on)
    return (
        f"{function.score:>7} {function.branches:>8} {function.max_depth:>5} {function.effective_lines:>6} "
        f"{function.rating_master:>10.1f} {function.rating_nesting:>10.1f}  {flags or '-':<17} "
        f"{filename}:{function.start_line}  {function.name}"
    )


def skipped_and_fragmentation(result: EngineResult) -> str:
    """Skipped files with reasons, and the fragmentation group if it fired."""
    lines = [f"Skipped {skip.filename}: {skip.reason.replace('_', ' ')}" for skip in result.skipped_files]
    if result.fragmentation_flagged:
        lines.append("Fragmentation group: " + ", ".join(result.fragmentation_cluster))
    return "\n".join(lines)
