"""What the ratings would be if a hinted block became its own helper function.

An estimate, not a promise: it re-uses the rating formulas on numbers the walk already has.
The helper restarts nesting at depth 0 and its decisions keep their weights relative to the
block; the parent loses the block's decisions and lines and gains one call line. The parent keeps
its outputs; the helper is assumed to have one output, the worst case for the rating.
"""

from __future__ import annotations

from argon.engine.branches import BranchNode
from argon.engine.config import EngineConfig
from argon.engine.lines import LineWeights
from argon.engine.rules import rating_master, rating_nesting
from argon.schemas import FunctionFinding


def predict_extraction(
    row: FunctionFinding,
    inside: list[BranchNode],
    region: BranchNode,
    weights: LineWeights,
    cfg: EngineConfig,
) -> str:
    """One sentence: both ratings after extracting the block, and whether that clears the flag."""
    block_lines = weights.range_total(region.line, region.end_line)
    parent_lines = max(row.effective_lines_altered - block_lines, 0) + 1
    parent, helper = _ratings(row, inside, region, parent_lines, block_lines + 1, cfg)
    verdict = "clears the flag" if max(parent, helper) <= cfg.active_threshold else "still over the threshold"
    return f"predicted after extracting: this function {parent:.0f}, helper {helper:.0f} (threshold {cfg.active_threshold:g}), {verdict}"


def _ratings(
    row: FunctionFinding, inside: list[BranchNode], region: BranchNode, parent_lines: int, helper_lines: int, cfg: EngineConfig
) -> tuple[float, float]:
    """(parent, helper) ratings in the active rating mode."""
    if cfg.rating_mode == "nesting":
        parent_score = max(row.score - sum(b.weight for b in inside), 0)
        helper_score = sum(_rebased_weight(b, region.depth) for b in inside)
        return (
            rating_nesting(parent_score, parent_lines, row.outputs_effective),
            rating_nesting(helper_score, helper_lines, 1),
        )
    return (
        rating_master(max(row.branches - len(inside), 0), parent_lines, row.outputs_effective),
        rating_master(len(inside), helper_lines, 1),
    )


def _rebased_weight(branch: BranchNode, base_depth: int) -> int:
    """The decision's weight when the block's own depth becomes depth 0."""
    if branch.operator and not branch.control_flow:
        return 1
    return max(branch.depth - base_depth, 0) + 1
