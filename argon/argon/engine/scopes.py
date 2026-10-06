"""Scopes, the isolation tax, effective depth, saturation and row names (spec §8)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ast_grep_py import SgNode

from argon.engine.branches import BranchNode
from argon.engine.node_utils import collapsed


SCOPE_NODE_TYPES = frozenset(
    {
        "function_definition",
        "lambda",
        "class_definition",
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "function",
        "generator_function",
        "arrow_function",
        "method_definition",
        "class_declaration",
        "class",
        "abstract_class_declaration",
    }
)
CLASS_KINDS = frozenset({"class_definition", "class_declaration", "class", "abstract_class_declaration"})
# Declarations and methods are always named rows (§8.1).
DECLARATION_KINDS = frozenset(
    {
        "function_definition",
        "function_declaration",
        "generator_function_declaration",
        "method_definition",
        "class_definition",
        "class_declaration",
        "abstract_class_declaration",
    }
)
# An anonymous function is named when it is the direct value of one of these.
BINDING_NAME_FIELDS = {
    "variable_declarator": "name",
    "pair": "key",
    "field_definition": "property",
    "public_field_definition": "name",
    "assignment": "left",  # Python: only `name = lambda ...`, see _bound_name
}


@dataclass
class Frame:
    """Running totals for one open scope; the module is the outermost frame."""

    kind: str
    name: str | None  # None = anonymous: no row, stats merge into the parent
    qualname: str
    start_line: int
    end_line: int
    start_index: int  # character offsets of the scope's source, end exclusive
    end_index: int
    base: int  # effective depth this scope starts at (A6)
    row_owner: "Frame | None"  # nearest enclosing row (named function or module)
    score: int = 0  # own weighted decisions + taxed inner scopes
    unowned: int = 0  # score without named rows' taxed contributions (for <module>)
    flat: int = 0  # every decision inside, for the file's flat count
    branches: int = 0  # decisions directly in this scope and its anonymous children
    max_depth: int = -1  # deepest effective decision (internal, 0-based); -1 = none
    saturated: bool = False
    outputs: set[str] = field(default_factory=set)
    child_rows: list["Frame"] = field(default_factory=list)  # named rows this one owns

    @property
    def is_row(self) -> bool:
        """Named functions and the module get rows; classes and anonymous functions don't (§8.1)."""
        return self.name is not None and self.kind not in CLASS_KINDS


def is_scope(node: SgNode, kind: str) -> bool:
    """True for named function, lambda and class nodes."""
    # `function`, `class` and `lambda` are also bare keyword tokens; only named nodes are scopes.
    return kind in SCOPE_NODE_TYPES and node.is_named()


def module_frame(line_count: int, text_length: int) -> Frame:
    """The outermost frame: module-level code, the <module> pseudo-function."""
    return Frame("module", "<module>", "<module>", 1, max(line_count, 1), 0, text_length, 0, None)


class ScopeTracker:
    """Open frames, innermost last; closing one pays its tax into the parent."""

    def __init__(self, line_count: int, score_cap: int, text_length: int):
        """Start with only the module frame open."""
        self.module = module_frame(line_count, text_length)
        self.frames = [self.module]
        self.cap = score_cap

    @property
    def current(self) -> Frame:
        """The innermost open frame."""
        return self.frames[-1]

    def enter(self, node: SgNode, kind: str, local_depth: int) -> None:
        """Open a frame for a function / lambda / class defined at the given local depth."""
        parent = self.current
        name = _scope_name(node, kind)
        prefix = _qualifying_parent(self.frames)
        span = node.range()
        frame = Frame(
            kind=kind,
            name=name,
            qualname=f"{prefix}.{name}" if prefix and name else (name or "<anonymous>"),
            start_line=span.start.line + 1,
            end_line=span.end.line + 1,
            start_index=span.start.index,
            end_index=span.end.index,
            base=parent.base + local_depth,
            row_owner=parent if parent.is_row or parent is self.module else parent.row_owner,
        )
        if frame.is_row:
            frame.row_owner.child_rows.append(frame)
        self.frames.append(frame)

    def add(self, branch: BranchNode) -> None:
        """Count a decision in the current frame: flat count, branches, depth and weight."""
        frame = self.current
        frame.flat += 1
        frame.branches += 1
        frame.max_depth = max(frame.max_depth, frame.base + branch.depth)
        self._raise_score(frame, branch.weight, own=True)

    def exit(self, definition_depth: int) -> Frame:
        """Close the innermost frame; its parent pays inner score x (1 + definition depth)."""
        inner = self.frames.pop()
        parent = self.current
        parent.flat += inner.flat
        parent.saturated = parent.saturated or inner.saturated
        self._raise_score(parent, inner.score * (1 + definition_depth), own=False)
        if not inner.is_row:
            # Anonymous functions and classes have no row: their stats roll up (§8.1).
            parent.unowned = self._capped(parent, parent.unowned + inner.unowned * (1 + definition_depth))
            parent.branches += inner.branches
            parent.max_depth = max(parent.max_depth, inner.max_depth)
        return inner

    def _raise_score(self, frame: Frame, amount: int, own: bool) -> None:
        """Add to a frame's score (and unowned score for its own decisions), capped."""
        frame.score = self._capped(frame, frame.score + amount)
        if own:
            frame.unowned = self._capped(frame, frame.unowned + amount)

    def _capped(self, frame: Frame, value: int) -> int:
        """B3: the tax compounds without limit in deep callback chains."""
        if value > self.cap:
            frame.saturated = True
            return self.cap
        return value


def _qualifying_parent(frames: list[Frame]) -> str:
    """`Cart.total`, `outer.inner`: the nearest enclosing named function or class."""
    for frame in reversed(frames):
        if frame.kind != "module" and frame.name is not None:
            return frame.qualname
    return ""


def _scope_name(node: SgNode, kind: str) -> str | None:
    """§8.1: declarations and methods, or a function that is the direct value of a binding."""
    if kind in DECLARATION_KINDS:
        named = node.field("name")
        return named.text() if named is not None else None
    return _bound_name(node)


def _bound_name(node: SgNode) -> str | None:
    """The binding name if the function is the direct value of a declarator, pair, field or name."""
    parent = node.parent()
    if parent is None:
        return None
    name_field = BINDING_NAME_FIELDS.get(parent.kind())
    if name_field is None or _value_of(parent) != node:
        return None
    target = parent.field(name_field)
    if target is None or (parent.kind() == "assignment" and target.kind() != "identifier"):
        return None  # Python `self.x = lambda: ...` is not a plain name
    return collapsed(target.text())


def _value_of(binding: SgNode) -> SgNode | None:
    """The bound value: `value` / `right` field, else the last named child (class fields)."""
    for field_name in ("value", "right"):
        value = binding.field(field_name)
        if value is not None:
            return value
    named = binding.named_children()
    return named[-1] if named else None
