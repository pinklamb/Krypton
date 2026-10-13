"""Per-node counters gathered during the walk: outputs, async boundaries, imports, comments (spec §9)."""

from __future__ import annotations

from collections import Counter

from ast_grep_py import SgNode

from krypton.engine.config import EngineConfig
from krypton.engine.imports import PythonImport, js_call_specifier, js_declaration_specifier, python_imports
from krypton.engine.lines import Span
from krypton.engine.node_utils import collapsed, iter_children, node_text
from krypton.engine.scopes import Frame

# Walk flags carried on each stack item.
DEAD = 1  # after an unconditional return / throw / raise in the same block: no outputs (A1)
MUTED = 2  # inside an assert in a test file: boolean operators don't count (B9)

BLOCK_KINDS = frozenset({"statement_block", "program", "switch_case", "switch_default", "block", "module"})
# Containers whose named children are statements (A4 statement count).
STATEMENT_LISTS = BLOCK_KINDS | {"class_body"}
TERMINATOR_KINDS = frozenset({"return_statement", "throw_statement", "raise_statement"})
DOCSTRING_OWNERS = frozenset({"function_definition", "class_definition"})


class Collectors:
    """Counters gathered during the walk: outputs, async boundaries, imports and comment spans."""

    def __init__(self, cfg: EngineConfig, language: str):
        """Empty counters plus the node-kind handlers for this file's language."""
        self.cfg = cfg
        self.async_receivers: set[str] = set()
        self.async_sites = 0
        self.js_specifiers: list[str] = []
        self.py_imports: list[PythonImport] = []
        self.ns_defs: set[tuple[str, str]] = set()  # (namespace, member) assigned at top level: `Racer.world = ...`
        self.ns_refs: set[tuple[str, str]] = set()  # (namespace, member) read anywhere: `Racer.world`
        self.comment_spans: list[Span] = []
        self.statement_starts: Counter[int] = Counter()  # 0-based line -> statements (A4)
        shared = {"return_statement": self._output, "comment": self._comment}
        shared.update(dict.fromkeys(STATEMENT_LISTS, self._statements))
        if language == "python":
            self._handlers = {
                **shared,
                "yield": self._output,
                "call": self._py_call,
                "decorator": self._py_decorator,
                "import_statement": self._py_import,
                "import_from_statement": self._py_import,
                "expression_statement": self._docstring,
            }
        else:
            self._handlers = {
                **shared,
                "yield_expression": self._output,
                "call_expression": self._js_call,
                "new_expression": self._js_constructor,
                "assignment_expression": self._js_assignment,
                "member_expression": self._js_member,
                "import_statement": self._js_import,
                "export_statement": self._js_import,
            }

    def observe(self, node: SgNode, kind: str, flags: int, frame: Frame) -> None:
        """Run the handler registered for this node kind, if any."""
        handler = self._handlers.get(kind)
        if handler is not None:
            handler(node, flags, frame)

    def scope_output(self, node: SgNode, kind: str, frame: Frame) -> None:
        """A Python lambda body or an expression-bodied arrow is the scope's output."""
        body = node.field("body")
        if body is not None and kind in ("lambda", "arrow_function") and body.kind() != "statement_block":
            frame.outputs.add(output_key(body))

    # outputs (§9.1)
    def _output(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Add a return / yield value to the current scope's outputs, unless dead code (A1)."""
        if flags & DEAD or not node.is_named():  # `yield` is also the bare keyword token
            return
        named = node.named_children()
        frame.outputs.add(output_key(named[0]) if named else "<void>")

    # async boundaries (§9.2)
    def _js_call(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Listener calls (socket.on, ...) as async boundaries; require / import() specifiers."""
        function = node.field("function")
        if function is not None and function.kind() == "member_expression":
            if node_text(function.field("property")) in self.cfg.async_listener_methods:
                self.async_receivers.add(collapsed(node_text(function.field("object"))))
        specifier = js_call_specifier(node)
        if specifier is not None:
            self.js_specifiers.append(specifier)

    def _js_constructor(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Count `new WebSocket(...)`-style async constructors, one per site."""
        self.async_sites += node_text(node.field("constructor")) in self.cfg.async_constructors

    def _js_assignment(self, node: SgNode, flags: int, frame: Frame) -> None:
        """An `x.onmessage = ...` handler, and a top-level `Namespace.member = ...` definition."""
        self._js_onmessage(node, flags, frame)
        left = node.field("left")
        if frame.kind == "module" and left is not None and left.kind() == "member_expression":
            self.ns_defs.update(_namespace_member(left))

    def _js_member(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Record `Namespace.member` reads; engine/coupling.py keeps the ones that match a shared global."""
        self.ns_refs.update(_namespace_member(node))

    def _js_onmessage(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Count `x.onmessage = ...` handlers, deduplicated by receiver."""
        left = node.field("left")
        if left is not None and left.kind() == "member_expression":
            if node_text(left.field("property")) == "onmessage":
                self.async_receivers.add(collapsed(node_text(left.field("object"))))

    def _py_call(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Listener calls deduplicated by receiver; consumer constructors (KafkaConsumer) per site."""
        function = node.field("function")
        if function is None:
            return
        if function.kind() == "attribute":
            if node_text(function.field("attribute")) in self.cfg.py_async_listener_methods:
                self.async_receivers.add(collapsed(node_text(function.field("object"))))
        elif function.kind() == "identifier":
            self.async_sites += function.text() in self.cfg.py_async_constructors

    def _py_decorator(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Count async decorators (@app.task, @shared_task, ...), one per site."""
        self.async_sites += _last_attribute(node) in self.cfg.py_async_decorators

    # imports (§9.3)
    def _py_import(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Record the Python imports of an import / from-import statement."""
        self.py_imports.extend(python_imports(node))

    def _js_import(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Record the source specifier of a JS import / export-from statement."""
        specifier = js_declaration_specifier(node)  # None for `export const x = 1`
        if specifier is not None:
            self.js_specifiers.append(specifier)

    # comments, docstrings and statements, for effective lines (§9.4)
    def _statements(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Each statement in a statement list; `a(); b(); c();` on one line counts 3."""
        value = node.field("value")  # a switch case's `case value:` is not a statement
        for child in iter_children(node, named=True):
            if child.kind() != "comment" and child != value:
                self.statement_starts[child.range().start.line] += 1

    def _comment(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Record a comment's span so its lines aren't effective (§9.4)."""
        self.comment_spans.append(_span(node))

    def _docstring(self, node: SgNode, flags: int, frame: Frame) -> None:
        """Record a module / class / function docstring as a comment span (§9.4)."""
        named = node.named_children()
        if len(named) == 1 and named[0].kind() == "string" and _is_first_statement(node):
            self.comment_spans.append(_span(node))


def child_flag_base(kind: str, flags: int, in_test: bool) -> tuple[int, int]:
    """(inherited MUTED bit, DEAD bit before the first child) for a node's children."""
    inherited = flags & MUTED
    if in_test and kind == "assert_statement":
        inherited |= MUTED
    return inherited, flags & DEAD


def child_flags(node: SgNode, kind: str, flags: int, in_test: bool) -> list[int]:
    """Flags for each child: DEAD after a terminator in a block, MUTED inside test asserts."""
    inherited, dead = child_flag_base(kind, flags, in_test)
    children = node.children()
    if kind not in BLOCK_KINDS:
        return [inherited | dead] * len(children)
    result = []
    for child in children:
        result.append(inherited | dead)
        if child.kind() in TERMINATOR_KINDS:
            dead = DEAD
    return result


def output_key(node: SgNode) -> str:
    """Whitespace-collapsed returned expression, outer parentheses removed."""
    while node.kind() == "parenthesized_expression" and len(node.named_children()) == 1:
        node = node.named_children()[0]
    return collapsed(node.text())


def _last_attribute(decorator: SgNode) -> str:
    """`@app.task`, `@shared_task`, `@celery.task(bind=True)` -> "task" / "shared_task"."""
    named = decorator.named_children()
    target = named[0] if named else None
    if target is not None and target.kind() == "call":
        target = target.field("function")
    if target is not None and target.kind() == "attribute":
        target = target.field("attribute")
    return node_text(target)


def _is_first_statement(node: SgNode) -> bool:
    """A string that opens a module, or the body block of a def / class, is a docstring."""
    parent = node.parent()
    if parent is None:
        return False
    if parent.kind() == "block":
        owner = parent.parent()
        if owner is None or owner.kind() not in DOCSTRING_OWNERS:
            return False
    elif parent.kind() != "module":
        return False
    first = next((child for child in iter_children(parent, named=True) if child.kind() != "comment"), None)
    return first == node


def _span(node: SgNode) -> Span:
    """The node's (start line, start column, end line, end column), 0-based."""
    span = node.range()
    return (span.start.line, span.start.column, span.end.line, span.end.column)


def _namespace_member(node: SgNode) -> set[tuple[str, str]]:
    """{(object, property)} when a member expression is `identifier.property`, else empty."""
    owner, member = node.field("object"), node.field("property")
    if owner is None or member is None or owner.kind() != "identifier":
        return set()
    return {(owner.text(), member.text())}

