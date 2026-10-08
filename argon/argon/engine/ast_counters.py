"""analyze_tree: the one-pass walk over an ast-grep tree (spec §8.5, §9).

The rules it applies are split by job: branches.py (what is a decision),
nesting.py (what nests), scopes.py (functions and their tax), collectors.py
(outputs, async boundaries, imports, comments), lines.py (effective lines).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ast_grep_py import SgNode

from argon.engine.branches import BRANCH_CANDIDATE_TYPES, BranchNode, branch_for
from argon.engine.collectors import BLOCK_KINDS, DEAD, MUTED, TERMINATOR_KINDS, Collectors, child_flag_base, child_flags
from argon.engine.config import DEFAULT_CONFIG, EngineConfig, matches_any
from argon.engine.imports import PythonImport, resolve_imports
from argon.engine.languages import ParsedSource, parse_source
from argon.engine.lines import LineWeights, physical_lines
from argon.engine.nesting import child_frames, has_uniform_depth
from argon.engine.scopes import Frame, ScopeTracker, is_scope

__all__ = [
    "AstMetrics",
    "BranchNode",
    "FunctionStats",
    "ParsedSource",
    "PythonImport",
    "TooWide",
    "analyze_tree",
    "count_async_boundaries",
    "count_cyclomatic_branches",
    "count_determinate_outputs",
    "count_relative_imports",
    "count_weighted_branches",
    "find_branch_nodes",
    "parse_source",
]


class TooWide(Exception):
    """A node has more children than EngineConfig.max_children; the file is not walked."""


@dataclass(frozen=True)
class FunctionStats:
    """One named function (or the <module> pseudo-function), before rating."""

    name: str
    start_line: int
    end_line: int
    score: int
    branches: int  # own + anonymous children, no base
    max_depth: int  # reported: 1 = flat, 0 = no decisions
    determinate_outputs: int
    outputs_effective: int
    saturated: bool
    line_ranges: tuple[tuple[int, int], ...]  # own lines, nested named functions cut out
    char_ranges: tuple[tuple[int, int], ...] = ()  # own characters (start, end exclusive), same cut-out

    @property
    def nested(self) -> bool:
        """True when the deepest decision is 3+ levels deep (reported depth, §8.4)."""
        return self.max_depth >= 3

    def effective_lines(self, weights: LineWeights, altered: frozenset[int] | None = None) -> int:
        """Effective lines the function owns; only altered lines if a set is given.

        Lines shared with another function are split by character ownership (Test 1).
        """
        return round(weights.own_total(self.char_ranges, altered))


@dataclass(frozen=True)
class AstMetrics:
    """Everything one file contributes, collected in one traversal."""

    language: str
    line_count: int
    cyclomatic_branches: int  # flat, whole file, no base
    nesting_score: int  # weighted, whole file incl. module level
    max_depth: int  # deepest effective decision in the file, reported
    branches: tuple[BranchNode, ...]
    functions: tuple[FunctionStats, ...]  # named rows, source order
    module: FunctionStats  # module-level code as one pseudo-function
    async_boundary_count: int
    js_specifiers: tuple[str, ...]
    namespace_defs: frozenset[tuple[str, str]]  # JS/TS `Namespace.member = ...` at top level
    namespace_refs: frozenset[tuple[str, str]]  # JS/TS `Namespace.member` reads
    python_imports: tuple[PythonImport, ...]
    line_weights: LineWeights


def analyze_tree(parsed: ParsedSource, cfg: EngineConfig = DEFAULT_CONFIG, filename: str = "") -> AstMetrics:
    """Collect decisions, scopes, outputs, async boundaries, imports and comments once."""
    walk = _Walk(parsed, cfg, filename)
    walk.run()
    return walk.metrics()


@dataclass
class _Siblings:
    """The children of a wide node still to visit, held as one pending node (constant memory).

    Visiting a child pushes this back before the child's own descendants, so source order
    and the DEAD-after-a-terminator rule are the same as with the bulk child list.
    """

    pending: SgNode  # next child to visit
    block: bool  # a statement list: a return / throw makes later siblings DEAD
    inherited: int  # MUTED bit every child gets
    dead: int  # DEAD bit the next child gets


class _Walk:
    """One iterative pass. Stack items are (node, depth, flags); node None ends a scope."""

    def __init__(self, parsed: ParsedSource, cfg: EngineConfig, filename: str):
        """Set up the stack, scope tracker and collectors for one file."""
        self.parsed = parsed
        self.cfg = cfg
        self.in_test = bool(filename) and matches_any(filename.replace("\\", "/"), cfg.test_path_globs)
        self.line_count = len(physical_lines(parsed.text))
        self.scopes = ScopeTracker(self.line_count, cfg.score_cap, len(parsed.text))
        self.collectors = Collectors(cfg, parsed.language)
        self.branches: list[BranchNode] = []
        self.rows: list[Frame] = []
        self.deepest = -1
        self.stack: list[tuple[SgNode | None, int, int]] = [(parsed.root, 0, 0)]

    def run(self) -> None:
        """Pop until empty: end-of-scope markers pay the tax, other items are visited."""
        while self.stack:
            node, depth, flags = self.stack.pop()
            if node is None:
                self._close_scope(depth)
            elif isinstance(node, _Siblings):
                self._next_sibling(node, depth)
            else:
                self._visit(node, depth, flags)

    def _close_scope(self, definition_depth: int) -> None:
        """Every inner node is scored: pay the tax upward (§8.5)."""
        closed = self.scopes.exit(definition_depth)
        if closed.is_row:
            self.rows.append(closed)

    def _visit(self, node: SgNode, depth: int, flags: int) -> None:
        """Collect, then open a scope or count a decision, then push the children."""
        kind = node.kind()
        wide = node.child(self.cfg.lazy_children_threshold) is not None  # a cheap probe: builds no wrappers
        self.collectors.observe(node, kind, flags, self.scopes.current)
        if is_scope(node, kind):
            self._guard_width(node, wide)
            self._open_scope(node, kind, depth, flags)
            return
        control_flow = self._count_decision(node, kind, depth, flags)
        if wide and has_uniform_depth(kind, control_flow):
            self._push_siblings(node, kind, depth, flags)
            return
        self._guard_width(node, wide)
        self._push_children(node, kind, depth, flags, control_flow)

    def _count_decision(self, node: SgNode, kind: str, depth: int, flags: int) -> bool:
        """Count the node if it is a decision; True when it is a control-flow boolean."""
        branch = _branch(node, kind, depth, flags, self.cfg, self.in_test)
        if branch is not None:
            self._count(branch)
        return branch is not None and branch.control_flow

    def _push_children(self, node: SgNode, kind: str, depth: int, flags: int, control_flow: bool) -> None:
        """Queue every child with its depth and flags (bulk fetch, for narrow nodes)."""
        frames = child_frames(node, kind, depth, control_flow)
        frame_flags = child_flags(node, kind, flags, self.in_test)
        for (child, child_depth), child_flag in zip(reversed(frames), reversed(frame_flags)):
            self.stack.append((child, child_depth, child_flag))

    def _guard_width(self, node: SgNode, wide: bool) -> None:
        """Nodes that need per-child depth rules are still read in bulk, so they keep a cap."""
        if wide and node.child(self.cfg.max_children) is not None:
            raise TooWide

    def _push_siblings(self, node: SgNode, kind: str, depth: int, flags: int) -> None:
        """Queue a wide node's children to be visited one at a time, all at the node's depth."""
        inherited, dead = child_flag_base(kind, flags, self.in_test)
        self.stack.append((_Siblings(node.child(0), kind in BLOCK_KINDS, inherited, dead), depth, 0))

    def _next_sibling(self, siblings: _Siblings, depth: int) -> None:
        """Visit the pending child; the rest of its siblings wait underneath its descendants."""
        child = siblings.pending
        following = child.next()
        flag = siblings.inherited | siblings.dead
        if siblings.block and child.kind() in TERMINATOR_KINDS:
            siblings.dead = DEAD
        if following is not None:
            siblings.pending = following
            self.stack.append((siblings, depth, 0))
        self._visit(child, depth, flag)

    def _open_scope(self, node: SgNode, kind: str, depth: int, flags: int) -> None:
        """A function / lambda / class is scored on its own scale, from depth 0."""
        self.scopes.enter(node, kind, depth)
        self.collectors.scope_output(node, kind, self.scopes.current)
        self.stack.append((None, depth, 0))
        self.stack.extend((child, 0, flags & MUTED) for child in reversed(node.children()))

    def _count(self, branch: BranchNode) -> None:
        """Record a decision in the current scope and track the deepest effective depth."""
        self.branches.append(branch)
        self.scopes.add(branch)
        self.deepest = max(self.deepest, self.scopes.current.base + branch.depth)

    def metrics(self) -> AstMetrics:
        """Build the file's AstMetrics once the walk has finished."""
        weights = LineWeights.build(
            self.parsed.text,
            self.collectors.comment_spans,
            self.cfg.effective_line_width,
            self.collectors.statement_starts,
        )
        module = self.scopes.module
        rows = sorted(self.rows, key=lambda frame: frame.start_line)
        return AstMetrics(
            language=self.parsed.language,
            line_count=self.line_count,
            cyclomatic_branches=module.flat,
            nesting_score=module.score,
            max_depth=self.deepest + 1,
            branches=tuple(sorted(self.branches, key=lambda branch: branch.line)),
            functions=tuple(_stats(frame, frame.score) for frame in rows),
            module=_stats(module, module.unowned),
            async_boundary_count=len(self.collectors.async_receivers) + self.collectors.async_sites,
            js_specifiers=tuple(self.collectors.js_specifiers),
            namespace_defs=frozenset(self.collectors.ns_defs),
            namespace_refs=frozenset(self.collectors.ns_refs),
            python_imports=tuple(self.collectors.py_imports),
            line_weights=weights,
        )


def _branch(
    node: SgNode, kind: str, depth: int, flags: int, cfg: EngineConfig, in_test: bool
) -> BranchNode | None:
    """The decision at this node with its depth, or None; skips test-file asserts (B9)."""
    if kind not in BRANCH_CANDIDATE_TYPES:
        return None
    if in_test and kind == "assert_statement":
        return None  # B9: asserts in test files don't count
    branch = branch_for(node, kind, cfg)
    if branch is None or (flags & MUTED and branch.operator):
        return None  # B9: nor do the boolean operators inside them
    return replace(branch, depth=depth)


def _stats(frame: Frame, score: int) -> FunctionStats:
    """Freeze a closed frame into FunctionStats, capping outputs at branches + 1 (A1)."""
    raw_outputs = len(frame.outputs)
    return FunctionStats(
        name=frame.qualname,
        start_line=frame.start_line,
        end_line=frame.end_line,
        score=score,
        branches=frame.branches,
        max_depth=frame.max_depth + 1,
        determinate_outputs=raw_outputs,
        outputs_effective=max(1, min(raw_outputs, frame.branches + 1)),  # A1 cap
        saturated=frame.saturated,
        line_ranges=_own_ranges(frame),
        char_ranges=_own_chars(frame),
    )


def _own_ranges(frame: Frame) -> tuple[tuple[int, int], ...]:
    """The frame's lines minus the lines of named functions nested directly in it."""
    ranges = []
    cursor = frame.start_line
    for child in sorted(frame.child_rows, key=lambda row: row.start_line):
        if child.start_line > cursor:
            ranges.append((cursor, child.start_line - 1))
        cursor = max(cursor, child.end_line + 1)
    if cursor <= frame.end_line:
        ranges.append((cursor, frame.end_line))
    return tuple(ranges)


def _own_chars(frame: Frame) -> tuple[tuple[int, int], ...]:
    """The frame's characters minus the spans of named functions nested directly in it."""
    ranges = []
    cursor = frame.start_index
    for child in sorted(frame.child_rows, key=lambda row: row.start_index):
        if child.start_index > cursor:
            ranges.append((cursor, child.start_index))
        cursor = max(cursor, child.end_index)
    if cursor < frame.end_index:
        ranges.append((cursor, frame.end_index))
    return tuple(ranges)


def find_branch_nodes(tree: ParsedSource, cfg: EngineConfig = DEFAULT_CONFIG) -> list[BranchNode]:
    """Branches in source order, for CLI diagnostics."""
    return list(analyze_tree(tree, cfg).branches)


def count_cyclomatic_branches(tree: ParsedSource, cfg: EngineConfig = DEFAULT_CONFIG) -> int:
    """Count decision points; no McCabe base value of one (B5)."""
    return analyze_tree(tree, cfg).cyclomatic_branches


def count_weighted_branches(tree: ParsedSource, cfg: EngineConfig = DEFAULT_CONFIG) -> int:
    """Nesting-weighted decisions: depth 0 = +1, deeper structures compound, scopes taxed."""
    return analyze_tree(tree, cfg).nesting_score


def count_determinate_outputs(tree: ParsedSource, cfg: EngineConfig = DEFAULT_CONFIG) -> int:
    """Sum of every function's outputs_effective (§9.1)."""
    return sum(function.outputs_effective for function in analyze_tree(tree, cfg).functions)


def count_async_boundaries(tree: ParsedSource, cfg: EngineConfig = DEFAULT_CONFIG) -> int:
    """Distinct listeners (by receiver) plus constructor / decorator sites (§9.2)."""
    return analyze_tree(tree, cfg).async_boundary_count


def count_relative_imports(
    tree: ParsedSource,
    importer_path: str,
    pr_file_set: set[str],
    cfg: EngineConfig = DEFAULT_CONFIG,
) -> int:
    """Distinct scanned files this file imports, however the import is written (§9.3)."""
    metrics = analyze_tree(tree, cfg)
    file_paths = frozenset(path.replace("\\", "/") for path in pr_file_set)
    importer = importer_path.replace("\\", "/")
    return len(resolve_imports(importer, metrics.js_specifiers, metrics.python_imports, file_paths, cfg))
