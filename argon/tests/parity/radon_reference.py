"""Independent Python references for parity (spec §12.3), built on radon 6.0.1 and Python's own ast.

flat_count(): radon's exact per-node rules, every decision in the file in one total.
nesting_score(): radon's per-construct increments + Argon's nesting math (§7-§8),
tracked over `ast`, separately from tree-sitter.
"""

from __future__ import annotations

import ast

from radon.visitors import ComplexityVisitor


class FlatVisitor(ComplexityVisitor):
    """radon's rules without its per-function blocks: one total for the file (no +1 base, B5)."""

    def visit_FunctionDef(self, node):
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Assert(self, node):
        # §6.4: radon 6.0.1 never descends into an assert; Argon counts its and / or.
        self.complexity += 1
        self.generic_visit(node)

    def visit_TryStar(self, node):
        # §6.4: radon 6.0.1 predates `except*` (ast.TryStar) and scores it 0.
        self.complexity += len(node.handlers) + bool(node.orelse)
        self.generic_visit(node)

    def visit_Match(self, node):
        self.complexity += match_increment(node)
        for child in ast.iter_child_nodes(node):
            self.visit(child)


def match_increment(node: ast.Match) -> int:
    """§6.2: only a catch-all WITHOUT a guard is free. radon 6.0.1 also frees `case _ if x:`."""
    free = any(getattr(case.pattern, "pattern", False) is None and case.guard is None for case in node.cases)
    return max(0, len(node.cases) - free)


def flat_count(code: str) -> int:
    visitor = FlatVisitor(off=False)
    visitor.visit(ast.parse(code))
    return visitor.complexity


class _NoRecurse(ast.NodeVisitor):
    def generic_visit(self, node):
        pass


class _Increment(ComplexityVisitor, _NoRecurse):
    """radon's ComplexityVisitor with recursion cut off: scores exactly one node."""


def radon_increment(node: ast.AST) -> int:
    if isinstance(node, ast.TryStar):
        return len(node.handlers) + bool(node.orelse)
    if isinstance(node, ast.Match):
        return match_increment(node)
    visitor = _Increment(off=False)
    if isinstance(node, ast.Assert):
        visitor.visit_Assert(node)
    else:
        visitor.generic_visit(node)
    return visitor.complexity


SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)


class NestingReference:
    def __init__(self, code: str):
        self.lines = code.splitlines()
        self.score = 0
        self.decisions = 0

    def visit(self, node: ast.AST, depth: int, scale: int) -> None:
        if isinstance(node, SCOPES):
            self._visit_scope(node, depth, scale)
        elif isinstance(node, COMPREHENSIONS):
            self._visit_comprehension(node, depth, scale)
        else:
            self._count(node, depth, scale)
            for child, child_depth in self._children(node, depth):
                self.visit(child, child_depth, scale)

    def _visit_scope(self, node, depth, scale):
        for decorator in getattr(node, "decorator_list", []):
            self.visit(decorator, depth, scale)
        inner = scale * (depth + 1)  # the isolation tax
        for field, value in ast.iter_fields(node):
            if field != "decorator_list":
                self._visit_value(value, 0, inner)

    def _count(self, node, depth, scale):
        increment = radon_increment(node)
        self.decisions += increment
        weight = 1 if isinstance(node, ast.BoolOp) else depth + 1
        self.score += increment * weight * scale

    def _visit_comprehension(self, node, depth, scale):
        """§7.3: each for / if clause one level deeper, element innermost."""
        level = depth
        for generator in node.generators:
            assert radon_increment(generator) == 1 + len(generator.ifs)
            self.decisions += 1 + len(generator.ifs)
            self.score += (level + 1) * scale
            self.visit(generator.target, level, scale)
            self.visit(generator.iter, level, scale)
            level += 1
            for condition in generator.ifs:
                self.score += (level + 1) * scale
                self.visit(condition, level, scale)
                level += 1
        for element in (getattr(node, "elt", None), getattr(node, "key", None), getattr(node, "value", None)):
            if element is not None:
                self.visit(element, level, scale)

    def _visit_value(self, value, depth, scale):
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, ast.AST):
                self.visit(item, depth, scale)

    def _children(self, node, depth):
        deeper = depth + 1
        if isinstance(node, ast.If):
            orelse_depth = depth if _is_elif(node) else deeper
            return _at(node.test, depth) + _at(node.body, deeper) + _at(node.orelse, orelse_depth)
        if isinstance(node, ast.IfExp):
            chained = isinstance(node.orelse, ast.IfExp) and not self._parenthesized(node.orelse)
            return _at(node.test, depth) + _at(node.body, deeper) + _at(node.orelse, depth if chained else deeper)
        if isinstance(node, (ast.For, ast.AsyncFor)):
            return _at(node.target, depth) + _at(node.iter, depth) + _at(node.body, deeper) + _at(node.orelse, deeper)
        if isinstance(node, ast.While):
            return _at(node.test, depth) + _at(node.body, deeper) + _at(node.orelse, deeper)
        if isinstance(node, (ast.Try, ast.TryStar)):
            handlers = [item for handler in node.handlers for item in _at(handler.type, depth) + _at(handler.body, deeper)]
            return _at(node.body, depth) + handlers + _at(node.orelse, deeper) + _at(node.finalbody, depth)
        if isinstance(node, ast.match_case):
            return _at(node.pattern, depth) + _at(node.guard, depth) + _at(node.body, deeper)
        return [(child, depth) for child in ast.iter_child_nodes(node)]

    def _parenthesized(self, node: ast.expr) -> bool:
        """`a if c else (b if d else e)` nests; ast drops the parentheses, the source keeps them."""
        line = self.lines[node.lineno - 1].encode("utf-8")[: node.col_offset].decode("utf-8", "replace")
        row = node.lineno - 1
        while not line.strip() and row > 0:
            row -= 1
            line = self.lines[row]
        return line.rstrip().endswith("(")


def nesting_score(code: str) -> tuple[int, int]:
    """(nesting-weighted score, flat decision count) for a Python source string."""
    reference = NestingReference(code)
    reference.visit(ast.parse(code), 0, 1)
    return reference.score, reference.decisions


def _at(values, depth):
    values = values if isinstance(values, list) else [values]
    return [(value, depth) for value in values if isinstance(value, ast.AST)]


def _is_elif(parent: ast.If) -> bool:
    return (
        len(parent.orelse) == 1
        and isinstance(parent.orelse[0], ast.If)
        and parent.orelse[0].col_offset == parent.col_offset
    )
