"""Ratings, flag rules, fragmentation, the purity score and the conclusion (spec §10.2-§10.5)."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor, log10
from typing import Literal

from argon.engine.config import EngineConfig
from argon.schemas import FileFinding

FAIL_CLOSED_REASONS = frozenset({"unparseable"})
MAX_OVERSHOOT_POINTS = 30
FLAG_RATIO_POINTS = 70


def rating_master(branches: int, effective_lines_altered: int, outputs_effective: int) -> float:
    """Master §4.2, per function: branches² × altered lines ÷ outputs."""
    return branches**2 * effective_lines_altered / max(outputs_effective, 1)


def rating_nesting(score: int, effective_lines_altered: int, outputs_effective: int) -> float:
    """§10.4: the nesting score already grows quadratically for a deep nest."""
    return score * effective_lines_altered / max(outputs_effective, 1)


def check_concentration_rule(finding: FileFinding, cfg: EngineConfig) -> bool:
    """Master §4.1: over line_threshold effective lines AND over async_boundary_threshold."""
    return (
        finding.effective_lines > cfg.line_threshold
        and finding.async_boundary_count > cfg.async_boundary_threshold
    )


def check_semantic_path_rule(finding: FileFinding, cfg: EngineConfig) -> bool:
    """Master §4.2: the file's rating is over the active threshold."""
    return finding.logic_complexity_rating > cfg.active_threshold


def overhead_points(trivial: int, total: int, cfg: EngineConfig) -> float:
    """Structural Overhead Tax: purity points for a file dense in trivial wrappers.

    Zero for files under wrapper_min_functions functions or at most wrapper_free_density
    wrappers; then linear up to wrapper_tax_max_points for a file made only of wrappers.
    """
    if total < cfg.wrapper_min_functions:
        return 0.0
    density = trivial / total
    if density <= cfg.wrapper_free_density:
        return 0.0
    return min(cfg.wrapper_tax_max_points, cfg.wrapper_tax_max_points * (density - cfg.wrapper_free_density) / (1 - cfg.wrapper_free_density))


def check_coupling_rule(finding: FileFinding, cfg: EngineConfig) -> bool:
    """A file wired to more than coupling_weight_threshold others and big enough to matter."""
    return finding.coupling_weight > cfg.coupling_weight_threshold and finding.effective_lines >= cfg.coupling_min_lines


@dataclass(frozen=True)
class Fragmentation:
    """Whether the fragmentation rule fired, and the files in the group that triggered it."""

    triggered: bool
    cluster: list[str]


def check_fragmentation_rule(
    findings: list[FileFinding], edges: set[tuple[str, str]], cfg: EngineConfig
) -> Fragmentation:
    """§10.2: density of the largest group of files that import each other."""
    nodes = {finding.filename for finding in findings}
    internal = {(source, target) for source, target in edges if source in nodes and target in nodes}
    groups = _connected_groups(nodes, internal)
    if not groups:
        return Fragmentation(False, [])
    largest = max(groups, key=lambda group: (len(group), _edge_count(group, internal), sorted(group)))
    edge_count = _edge_count(largest, internal)
    triggered = (
        len(largest) >= cfg.fragmentation_min_files
        and edge_count / len(largest) > cfg.fragmentation_avg_import_threshold
    )
    return Fragmentation(triggered, sorted(largest) if triggered else [])


def _edge_count(group: set[str], edges: set[tuple[str, str]]) -> int:
    """Import edges that start inside the group."""
    return sum(source in group for source, _ in edges)


def _connected_groups(nodes: set[str], edges: set[tuple[str, str]]) -> list[set[str]]:
    """Weakly connected components: import direction is ignored."""
    neighbours: dict[str, set[str]] = {node: set() for node in nodes}
    for source, target in edges:
        neighbours[source].add(target)
        neighbours[target].add(source)
    seen: set[str] = set()
    groups = []
    for start in sorted(nodes):
        if start not in seen:
            group = _reachable(start, neighbours)
            seen |= group
            groups.append(group)
    return groups


def _reachable(start: str, neighbours: dict[str, set[str]]) -> set[str]:
    """Every file connected to start through imports, in either direction."""
    group, frontier = {start}, [start]
    while frontier:
        fresh = neighbours[frontier.pop()] - group
        group |= fresh
        frontier.extend(fresh)
    return group


def overshoot_points(finding: FileFinding, threshold: float) -> float:
    """B2: 10 points per 10× over the threshold, capped at 30; fail-closed files take all 30."""
    if finding.flag_reason in FAIL_CLOSED_REASONS:
        return MAX_OVERSHOOT_POINTS
    rating = finding.logic_complexity_rating
    if rating <= threshold:
        return 0.0
    return min(MAX_OVERSHOOT_POINTS, 10 * log10(rating / threshold))


def compute_purity_score(findings: list[FileFinding], fragmentation: bool, cfg: EngineConfig) -> int:
    """§10.3: flag ratio and overshoot weighted by effective altered lines (A5)."""
    if not findings:
        return 100
    weights = [finding.effective_lines_altered for finding in findings]
    if not any(weights):
        weights = [1] * len(findings)
    total = sum(weights)
    flag_ratio = sum(w for w, finding in zip(weights, findings) if finding.flagged) / total
    threshold = cfg.active_threshold
    overshoot = _weighted_mean(findings, weights, lambda finding: overshoot_points(finding, threshold))
    overhead = _weighted_mean(findings, weights, lambda finding: finding.overhead_tax)
    score = 100 - FLAG_RATIO_POINTS * flag_ratio - overshoot - overhead
    if fragmentation:
        score -= cfg.fragmentation_flat_penalty
    return round_half_up(max(0.0, min(100.0, score)))


def _weighted_mean(findings: list[FileFinding], weights: list[int], points) -> float:
    """Mean of points(finding) weighted by effective altered lines."""
    return sum(w * points(finding) for w, finding in zip(weights, findings)) / sum(weights)


def round_half_up(value: float) -> int:
    """B6: Python's round() is banker's rounding (46.5 -> 46)."""
    return floor(value + 0.5)


def determine_check_conclusion(score: int, cfg: EngineConfig) -> Literal["success", "failure"]:
    """Master §4.5: failure below purity_score_fail_threshold, else success."""
    return "failure" if score < cfg.purity_score_fail_threshold else "success"
