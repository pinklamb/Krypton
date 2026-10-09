"""A small, dependency-aware spreadsheet engine, independent of Argon."""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class SheetError(Exception):
    pass


class ParseError(SheetError):
    pass


class CircularReference(SheetError):
    pass


_CELL = re.compile(r"^([A-Za-z]+)([1-9][0-9]*)$")
_TOKEN = re.compile(r'\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)|([A-Za-z]+[1-9][0-9]*)|([A-Za-z_][A-Za-z_0-9]*)|("(?:""|[^"])*")|(<=|>=|<>|[+\-*/^=<>(),:]))')


def normalize_ref(ref: str) -> str:
    m = _CELL.fullmatch(ref)
    if not m:
        raise ParseError(f"invalid cell reference: {ref}")
    return m.group(1).upper() + str(int(m.group(2)))


def col_number(col: str) -> int:
    n = 0
    for c in col.upper():
        n = n * 26 + ord(c) - 64
    return n


def col_name(n: int) -> str:
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def expand_range(start: str, end: str) -> list[str]:
    a, b = _CELL.fullmatch(normalize_ref(start)), _CELL.fullmatch(normalize_ref(end))
    assert a and b
    c1, r1 = col_number(a.group(1)), int(a.group(2))
    c2, r2 = col_number(b.group(1)), int(b.group(2))
    if (c2-c1+1)*(r2-r1+1) > 100_000:
        raise SheetError("range exceeds 100,000 cells")
    return [f"{col_name(c)}{r}" for r in range(min(r1,r2), max(r1,r2)+1)
            for c in range(min(c1,c2), max(c1,c2)+1)]


@dataclass(frozen=True)
class Token:
    kind: str
    value: str


def tokenize(text: str) -> list[Token]:
    result, pos = [], 0
    while pos < len(text):
        if text[pos:].isspace():
            break
        m = _TOKEN.match(text, pos)
        if not m:
            raise ParseError(f"unexpected input at {text[pos:pos+20]!r}")
        number, ref, ident, string, op = m.groups()
        if number is not None: result.append(Token("number", number))
        elif ref is not None: result.append(Token("ref", ref))
        elif ident is not None: result.append(Token("ident", ident.upper()))
        elif string is not None: result.append(Token("string", string[1:-1].replace('""','"')))
        else: result.append(Token("op", op))
        pos = m.end()
    result.append(Token("eof", ""))
    return result


class Parser:
    """Recursive-descent parser; formula grammar supports arithmetic, comparisons, calls and ranges."""
    def __init__(self, source: str):
        self.tokens = tokenize(source[1:] if source.startswith("=") else source)
        self.i = 0

    @property
    def t(self): return self.tokens[self.i]
    def take(self):
        t = self.t; self.i += 1; return t
    def accept(self, value):
        if self.t.value == value: self.take(); return True
        return False
    def require(self, value):
        if not self.accept(value): raise ParseError(f"expected {value!r}")

    def parse(self):
        node = self.comparison()
        if self.t.kind != "eof": raise ParseError(f"unexpected token {self.t.value!r}")
        return node

    def comparison(self):
        n = self.additive()
        while self.t.value in ("=", "<>", "<", ">", "<=", ">="):
            op = self.take().value; n = ("binary", op, n, self.additive())
        return n
    def additive(self):
        n = self.product()
        while self.t.value in ("+", "-"):
            op = self.take().value; n = ("binary", op, n, self.product())
        return n
    def product(self):
        n = self.power()
        while self.t.value in ("*", "/"):
            op = self.take().value; n = ("binary", op, n, self.power())
        return n
    def power(self):
        n = self.unary()
        if self.accept("^"): n = ("binary", "^", n, self.power())
        return n
    def unary(self):
        if self.t.value in ("+", "-"):
            op = self.take().value; return ("unary", op, self.unary())
        return self.atom()
    def atom(self):
        t = self.take()
        if t.kind == "number": return ("number", float(t.value))
        if t.kind == "string": return ("string", t.value)
        if t.kind == "ref":
            ref = normalize_ref(t.value)
            if self.accept(":"):
                if self.t.kind != "ref": raise ParseError("range endpoint must be a cell")
                return ("range", ref, normalize_ref(self.take().value))
            return ("ref", ref)
        if t.kind == "ident":
            if t.value in ("TRUE", "FALSE") and self.t.value != "(":
                return ("boolean", t.value == "TRUE")
            if not self.accept("("): raise ParseError(f"unknown name {t.value}")
            args = []
            if not self.accept(")"):
                while True:
                    args.append(self.comparison())
                    if self.accept(")"): break
                    self.require(",")
            return ("call", t.value, tuple(args))
        if t.value == "(":
            n = self.comparison(); self.require(")"); return n
        raise ParseError(f"unexpected token {t.value!r}")


def references(node) -> set[str]:
    kind = node[0]
    if kind == "ref": return {node[1]}
    if kind == "range": return set(expand_range(node[1], node[2]))
    if kind in ("binary",): return references(node[2]) | references(node[3])
    if kind == "unary": return references(node[2])
    if kind == "call": return set().union(*(references(arg) for arg in node[2])) if node[2] else set()
    return set()


def _number(value: Any) -> float:
    if isinstance(value, bool): return float(value)
    if isinstance(value, (int, float)): return float(value)
    if value in (None, ""): return 0.0
    try: return float(value)
    except (ValueError, TypeError): raise SheetError(f"cannot use {value!r} as a number")


class Spreadsheet:
    def __init__(self):
        self.cells: dict[str, Any] = {}
        self._asts: dict[str, Any] = {}
        self.dependencies: dict[str, set[str]] = {}
        self._cache: dict[str, Any] = {}

    def set(self, ref: str, value: Any):
        ref = normalize_ref(ref)
        self.cells[ref] = value
        self._asts.pop(ref, None); self._cache.clear()
        if isinstance(value, str) and value.startswith("="):
            self._asts[ref] = Parser(value).parse()
            self.dependencies[ref] = references(self._asts[ref])
        else:
            self.dependencies.pop(ref, None)

    def get(self, ref: str): return self.evaluate(normalize_ref(ref))

    def evaluate(self, ref: str):
        ref = normalize_ref(ref)
        return self._eval_cell(ref, [])

    def _eval_cell(self, ref, stack):
        if ref in self._cache: return self._cache[ref]
        if ref in stack:
            cycle = " -> ".join(stack[stack.index(ref):] + [ref])
            raise CircularReference(f"circular reference: {cycle}")
        if ref not in self.cells: return 0
        raw = self.cells[ref]
        if ref not in self._asts: return raw
        value = self._eval(self._asts[ref], stack + [ref])
        self._cache[ref] = value
        return value

    def _eval(self, node, stack):
        kind = node[0]
        if kind == "number" or kind == "string" or kind == "boolean": return node[1]
        if kind == "ref": return self._eval_cell(node[1], stack)
        if kind == "range": return [self._eval_cell(r, stack) for r in expand_range(node[1], node[2])]
        if kind == "unary":
            v = _number(self._eval(node[2], stack)); return v if node[1] == "+" else -v
        if kind == "binary":
            op = node[1]; a, b = self._eval(node[2], stack), self._eval(node[3], stack)
            if op in ("=", "<>", "<", ">", "<=", ">="):
                return {"=": lambda: a == b, "<>": lambda: a != b, "<": lambda: a < b,
                        ">": lambda: a > b, "<=": lambda: a <= b, ">=": lambda: a >= b}[op]()
            x, y = _number(a), _number(b)
            if op == "+": return x+y
            if op == "-": return x-y
            if op == "*": return x*y
            if op == "/":
                if y == 0: raise SheetError("division by zero")
                return x/y
            return x**y
        if kind == "call":
            name, nodes = node[1], node[2]
            # IF evaluates only the selected branch.
            if name == "IF":
                if len(nodes) not in (2,3): raise SheetError("IF expects 2 or 3 arguments")
                return self._eval(nodes[1], stack) if self._eval(nodes[0], stack) else (self._eval(nodes[2], stack) if len(nodes)==3 else False)
            args = [self._eval(n, stack) for n in nodes]
            flat = [item for arg in args for item in (arg if isinstance(arg, list) else [arg])]
            nums = [_number(v) for v in flat if isinstance(v, (int, float, bool)) or v in (None, "")]
            if name == "SUM": return sum(nums)
            if name == "AVERAGE":
                if not nums: raise SheetError("AVERAGE needs at least one number")
                return sum(nums)/len(nums)
            if name == "MIN": return min(nums) if nums else 0
            if name == "MAX": return max(nums) if nums else 0
            if name == "COUNT": return len(nums)
            if name == "AND": return all(bool(v) for v in flat)
            if name == "OR": return any(bool(v) for v in flat)
            if name == "NOT" and len(args)==1: return not bool(args[0])
            raise SheetError(f"unknown function or invalid arguments: {name}")
        raise SheetError("invalid expression")

    def find_cycles(self) -> list[list[str]]:
        """Return dependency cycles using DFS, including the repeated start vertex."""
        state: dict[str, int] = {}; stack: list[str] = []; cycles: list[list[str]] = []
        def visit(cell):
            state[cell] = 1; stack.append(cell)
            for dep in self.dependencies.get(cell, ()):
                if dep not in self.dependencies: continue
                if state.get(dep, 0) == 0: visit(dep)
                elif state.get(dep) == 1:
                    cycle = stack[stack.index(dep):] + [dep]
                    if cycle not in cycles: cycles.append(cycle)
            stack.pop(); state[cell] = 2
        for cell in self.dependencies:
            if state.get(cell, 0) == 0: visit(cell)
        return cycles

    @classmethod
    def from_csv(cls, path: str | Path):
        sheet = cls()
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row_num, row in enumerate(csv.reader(f), 1):
                for col_num_, value in enumerate(row, 1):
                    if value != "":
                        parsed: Any = value
                        if not value.startswith("="):
                            try: parsed = float(value) if any(c in value for c in ".eE") else int(value)
                            except ValueError: pass
                        sheet.set(f"{col_name(col_num_)}{row_num}", parsed)
        return sheet

    def to_csv(self, path: str | Path, evaluated: bool = True):
        coords = list(self.cells)
        max_col = max((col_number(_CELL.fullmatch(r).group(1)) for r in coords), default=0)
        max_row = max((int(_CELL.fullmatch(r).group(2)) for r in coords), default=0)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for row in range(1, max_row+1):
                values = []
                for col in range(1, max_col+1):
                    ref = f"{col_name(col)}{row}"
                    if ref not in self.cells: values.append("")
                    elif evaluated:
                        try: values.append(self.get(ref))
                        except SheetError as e: values.append(f"#ERROR: {e}")
                    else: values.append(self.cells[ref])
                writer.writerow(values)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate formulas in a CSV spreadsheet")
    parser.add_argument("input", help="input CSV file")
    parser.add_argument("-o", "--output", help="write evaluated CSV to this path")
    parser.add_argument("--show-formulas", action="store_true", help="write raw cells instead of evaluated values")
    parser.add_argument("--cell", action="append", default=[], metavar="A1", help="print evaluated cell (repeatable)")
    parser.add_argument("--cycles", action="store_true", help="report dependency cycles")
    args = parser.parse_args(argv)
    try:
        sheet = Spreadsheet.from_csv(args.input)
        if args.cycles:
            for cycle in sheet.find_cycles(): print("cycle: " + " -> ".join(cycle))
        for ref in args.cell: print(f"{normalize_ref(ref)}={sheet.get(ref)}")
        if args.output: sheet.to_csv(args.output, evaluated=not args.show_formulas)
    except (OSError, SheetError) as exc:
        print(f"error: {exc}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
