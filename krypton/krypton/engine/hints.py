"""Refactoring hints: where a heavy function is heavy, and a fixed piece of advice for its shape.

Pure data in, text out. A hint names the deepest decision, the block that holds it and that
block's share of the function's decision weight, then gives one sentence chosen by the
function's shape. It predicts nothing and changes no score.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Collection, Iterable, Mapping
from itertools import accumulate
from typing import NamedTuple

from krypton.engine.ast_counters import AstMetrics, FunctionStats
from krypton.engine.branches import BranchNode
from krypton.engine.config import EngineConfig
from krypton.engine.lines import LineWeights
from krypton.engine.prediction import predict_extraction
from krypton.schemas import FileFinding, FunctionFinding

NESTED_DEPTH = 3  # a function this deep gets the early-return advice
SEQUENTIAL_MIN = 8  # this many conditions in a row suggest a lookup table
LONG_FUNCTION_LINES = 60
SEQUENTIAL_KINDS = frozenset({"if_statement", "elif_clause", "switch_case", "case_clause"})
PARTNERS_SHOWN = 3
MIN_REGION_LINES = 3  # a block shorter than this is not worth a helper
WHOLE_FUNCTION_SHARE = 0.9  # a block holding this much of the weight is the function itself


class Hint(NamedTuple):
    """One hint: how heavy it is (for ordering), its key, its text, and the block it names."""

    rating: float
    key: str
    text: str
    region: tuple[int, int] | None = None


def function_hints(filename: str, metrics: AstMetrics, rows: list[FunctionFinding], cfg: EngineConfig) -> list[Hint]:
    """A hint for each function over the threshold or nested 3+ deep."""
    lines = [branch.line for branch in metrics.branches]
    heavy = [(stats, row) for stats, row in zip([*metrics.functions, metrics.module], rows) if _is_heavy(row, cfg)]
    found = [(row, _hint(row, _own_branches(stats, metrics.branches, lines), metrics.line_weights, cfg)) for stats, row in heavy]
    return [Hint(_rating(row, cfg), f"{filename}:{row.start_line} {row.name}", *hint) for row, hint in found if hint]


def _is_heavy(row: FunctionFinding, cfg: EngineConfig) -> bool:
    """Over the flag threshold, or nested three or more levels deep."""
    return _rating(row, cfg) > cfg.active_threshold or row.nested or _overloaded(row, cfg)


def _overloaded(row: FunctionFinding, cfg: EngineConfig) -> bool:
    """At least max_decisions_per_function decisions or max_function_score nesting score."""
    return row.branches >= cfg.max_decisions_per_function or row.score >= cfg.max_function_score


def _lead(row: FunctionFinding, cfg: EngineConfig) -> str:
    """What makes the function heavy, said first: times over the threshold, and any overload limit passed."""
    over = _rating(row, cfg) / cfg.active_threshold
    parts = [f"{over:.1f}x over the threshold"] * (over > 1)
    parts += [f"{row.branches} decisions (limit {cfg.max_decisions_per_function})"] * (row.branches >= cfg.max_decisions_per_function)
    parts += [f"nesting score {row.score} (limit {cfg.max_function_score})"] * (row.score >= cfg.max_function_score)
    return "".join(f"{part}; " for part in parts)


def file_hints(findings: Iterable[FileFinding], partners: Mapping[str, Collection[str]], cfg: EngineConfig) -> list[Hint]:
    """Hints about a whole file: coupling partners to cut, an over-budget file to split, wrappers to merge."""
    found = [_file_hint(finding, partners, cfg) for finding in findings]
    return [hint for hint in found if hint]


def _file_hint(finding: FileFinding, partners: Mapping[str, Collection[str]], cfg: EngineConfig) -> Hint | None:
    """The one file-level hint for this file, by its flag, or None."""
    if finding.flag_reason == "coupling_hotspot":
        return _coupling_hint(finding, partners)
    if finding.flag_reason == "file_overload":
        return _budget_hint(finding, cfg)
    if finding.overhead_tax > 0:
        return Hint(finding.overhead_tax, finding.filename, f"{finding.trivial_wrapper_count} of {len(finding.functions)} functions are trivial wrappers; merge them into their callers or give them real work")
    return None


def _coupling_hint(finding: FileFinding, partners: Mapping[str, Collection[str]]) -> Hint:
    """Name the files this one is most tied to."""
    names = ", ".join(sorted(partners.get(finding.filename, ()))[:PARTNERS_SHOWN])
    return Hint(float(finding.coupling_weight), finding.filename, f"tied to {finding.coupling_weight} files, including {names}; cut those dependencies first (events, narrower interfaces)")


def _budget_hint(finding: FileFinding, cfg: EngineConfig) -> Hint:
    """Say the file is over its total budget and where to start splitting."""
    heaviest = ", ".join(f.name for f in sorted(finding.functions, key=lambda f: -f.score)[:PARTNERS_SHOWN])
    text = f"total nesting score {finding.nesting_score} is over the file budget of {cfg.max_file_weight}, so moving code between helpers will not lower it; split the file by responsibility, starting with {heaviest}"
    return Hint(float(finding.nesting_score), finding.filename, text)


def _rating(row: FunctionFinding, cfg: EngineConfig) -> float:
    """The function's rating in the active rating mode."""
    return row.rating_nesting if cfg.rating_mode == "nesting" else row.rating_master


def _own_branches(stats: FunctionStats, branches: tuple[BranchNode, ...], lines: list[int]) -> list[BranchNode]:
    """The decisions on the function's own lines (nested named functions excluded), in source order."""
    own: list[BranchNode] = []
    for first, last in stats.line_ranges:
        own.extend(branches[bisect_left(lines, first) : bisect_right(lines, last)])
    return own


def _hint(
    row: FunctionFinding, own: list[BranchNode], weights: LineWeights, cfg: EngineConfig
) -> tuple[str, tuple[int, int] | None] | None:
    """(sentence, block lines): where the weight is, advice for the shape, and the predicted effect."""
    if not own:
        return None
    deepest = max(own, key=lambda branch: branch.depth)
    total = sum(b.weight for b in own)
    region, held = _best_region(own, deepest, total)
    where = f"deepest decision at line {deepest.line} (depth {deepest.depth + 1})"
    if region is not None:
        place = "inside" if region.line <= deepest.line <= region.end_line else "and the heaviest block is"
        where += f", {place} lines {region.line}-{region.end_line} which hold {held / total:.0%} of this function's decision weight"
    text = f"{_lead(row, cfg)}{where}; {_advice(row, own, region)}"
    if region is None:
        return text, None
    inside = [b for b in own if region.line <= b.line <= region.end_line]
    return f"{text}. {predict_extraction(row, inside, region, weights, cfg)}", (region.line, region.end_line)


def _best_region(own: list[BranchNode], deepest: BranchNode, total: int) -> tuple[BranchNode | None, int]:
    """The block worth extracting, preferring one that contains the deepest decision, then the heaviest."""
    options = _extractable_blocks(own, total)
    if not options:
        return None, 0
    return max(options, key=lambda option: (option[0].line <= deepest.line <= option[0].end_line, option[1]))


def _extractable_blocks(own: list[BranchNode], total: int) -> list[tuple[BranchNode, int]]:
    """(block, weight it holds): a few lines long, two or more decisions, not the whole function."""
    lines = [b.line for b in own]
    prefix = [0, *accumulate(b.weight for b in own)]
    found = []
    for block in own:
        low, high = bisect_left(lines, block.line), bisect_right(lines, block.end_line)
        held = prefix[high] - prefix[low]
        if _worth_extracting(block, high - low, held, total):
            found.append((block, held))
    return found


def _worth_extracting(block: BranchNode, decisions: int, held: int, total: int) -> bool:
    """Long enough, with at least two decisions, and not so much of the function that it only moves it."""
    return block.end_line - block.line + 1 >= MIN_REGION_LINES and decisions >= 2 and held <= WHOLE_FUNCTION_SHARE * total


def _advice(row: FunctionFinding, own: list[BranchNode], region: BranchNode | None) -> str:
    """The advice for the function's shape; the first rule that fits wins."""
    extract = f"move lines {region.line}-{region.end_line} into a helper" if region else None
    rules = (_deep_advice(own, extract), _chain_advice(own), _operator_advice(own), _long_advice(row, region))
    return next((text for text in rules if text), extract or "decisions are spread over single lines; combine related conditions or return early")


def _deep_advice(own: list[BranchNode], extract: str | None) -> str | None:
    """Three or more levels deep: flatten with early returns."""
    if max(b.depth for b in own) + 1 < NESTED_DEPTH:
        return None
    return "invert conditions with early returns" + (f", or {extract}" if extract else "")


def _chain_advice(own: list[BranchNode]) -> str | None:
    """A long run of top-level conditions: a table or dispatch map."""
    sequential = sum(b.depth == 0 and b.node_type in SEQUENTIAL_KINDS for b in own)
    if sequential < SEQUENTIAL_MIN:
        return None
    return f"{sequential} sequential conditions: use a lookup table or dispatch map, or split by case group"


def _operator_advice(own: list[BranchNode]) -> str | None:
    """Mostly boolean operators: name the conditions."""
    if sum(b.operator is not None for b in own) * 2 < len(own):
        return None
    return "mostly boolean operators: name the conditions as small predicate helpers"


def _long_advice(row: FunctionFinding, region: BranchNode | None) -> str | None:
    """Long with no block worth extracting: split into steps."""
    if row.effective_lines < LONG_FUNCTION_LINES or region is not None:
        return None
    return "long function with spread-out decisions: split it into steps at its natural sections"
