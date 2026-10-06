"""Read Argon's source and measure it: signatures, docstrings, radon grades, Argon's own scores."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from radon.complexity import cc_rank, cc_visit

from argon.engine.ast_counters import analyze_tree
from argon.engine.config import DEFAULT_CONFIG
from argon.engine.languages import parse_source

DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass
class Entry:
    """One function, method or class."""

    kind: str  # "function" | "method" | "class"
    qualname: str
    signature: str
    doc: str
    start: int
    end: int
    source: str
    complexity: int = 0  # radon cyclomatic complexity (includes radon's +1 base)
    rank: str = "-"  # radon grade A-F
    score: int | None = None  # Argon nesting-weighted score
    branches: int | None = None
    depth: int | None = None  # Argon reported depth, 1 = flat

    @property
    def nested(self) -> bool:
        return self.depth is not None and self.depth >= 3

    @property
    def summary(self) -> str:
        return self.doc.strip().splitlines()[0] if self.doc.strip() else ""


@dataclass
class Module:
    path: str  # POSIX, relative to the project root
    doc: str
    lines: int
    entries: list[Entry] = field(default_factory=list)


@dataclass
class Field:
    name: str
    annotation: str
    default: str
    comment: str


def product_files(root: Path) -> list[Path]:
    files = sorted((root / "argon").glob("*.py")) + sorted((root / "argon" / "engine").glob("*.py"))
    return [path for path in files if path.read_text(encoding="utf-8").strip()]


def analyze_module(root: Path, path: Path) -> Module:
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(root).as_posix()
    tree = ast.parse(text)
    entries = list(_entries(tree.body, "", text.splitlines(), in_class=False))
    _add_radon(entries, text)
    _add_argon(entries, relative, text)
    return Module(relative, ast.get_docstring(tree) or "", len(text.splitlines()), entries)


def _entries(body: list[ast.stmt], prefix: str, lines: list[str], in_class: bool):
    """Definitions in source order, with nested ones qualified (Cart.total, outer.inner)."""
    for node in body:
        if not isinstance(node, DEFS):
            continue
        qualname = prefix + node.name
        yield _entry(node, qualname, lines, in_class)
        yield from _entries(node.body, qualname + ".", lines, isinstance(node, ast.ClassDef))


def _entry(node: ast.AST, qualname: str, lines: list[str], in_class: bool) -> Entry:
    first = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
    kind = "class" if isinstance(node, ast.ClassDef) else "method" if in_class else "function"
    return Entry(
        kind=kind,
        qualname=qualname,
        signature=_signature(node),
        doc=ast.get_docstring(node) or "",
        start=node.lineno,
        end=node.end_lineno,
        source="\n".join(lines[first - 1 : node.end_lineno]),
    )


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        return f"class {node.name}({bases})" if bases else f"class {node.name}"
    keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{keyword} {node.name}({ast.unparse(node.args)}){returns}"


def _add_radon(entries: list[Entry], text: str) -> None:
    graded = {}
    for block in cc_visit(text):
        for item in _radon_blocks(block):
            graded[item.lineno] = (item.complexity, cc_rank(item.complexity))
    for entry in entries:
        entry.complexity, entry.rank = graded.get(entry.start, (0, "-"))


def _radon_blocks(block):
    yield block
    for child in [*getattr(block, "closures", []), *getattr(block, "methods", [])]:
        yield from _radon_blocks(child)
    for inner in getattr(block, "inner_classes", []):
        yield from _radon_blocks(inner)


def _add_argon(entries: list[Entry], relative: str, text: str) -> None:
    metrics = analyze_tree(parse_source(relative, text.encode()), DEFAULT_CONFIG, relative)
    rows = {function.start_line: function for function in metrics.functions}
    for entry in entries:
        row = rows.get(entry.start)
        if row is not None:
            entry.score, entry.branches, entry.depth = row.score, row.branches, row.max_depth


def fields_of(path: Path, class_name: str) -> list[Field]:
    """A class's annotated fields with their defaults and trailing `# comment`s."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [_field(item, lines) for item in node.body if isinstance(item, ast.AnnAssign)]
    return []


def _field(node: ast.AnnAssign, lines: list[str]) -> Field:
    comment = re.search(r"#\s*(.*)$", lines[node.end_lineno - 1])
    default = ast.unparse(node.value) if node.value is not None else "(required)"
    return Field(ast.unparse(node.target), ast.unparse(node.annotation), default, comment.group(1) if comment else "")


def collect_tests(root: Path) -> dict[str, list[tuple[str, int]]]:
    """{test file: [(test name, line)]} for every test_* function under tests/."""
    found = {}
    for path in sorted((root / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [(node.name, node.lineno) for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
        found[path.relative_to(root).as_posix()] = sorted(names, key=lambda item: item[1])
    return found
