"""Import collection and resolution against the scanned file set (spec §9.3)."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from ast_grep_py import SgNode

from krypton.engine.config import EngineConfig
from krypton.engine.node_utils import node_text


JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


@dataclass(frozen=True)
class PythonImport:
    """`from ..pkg.mod import a` -> PythonImport(2, "pkg/mod", ("a",)); level 0 = absolute."""

    level: int
    module: str
    names: tuple[str, ...]


def js_declaration_specifier(node: SgNode) -> str | None:
    """`import x from "./a"` / `export * from "./a"` -> "./a"."""
    return _string_specifier(node.field("source"))


def js_call_specifier(node: SgNode) -> str | None:
    """`require("./a")` / `import("./a")` -> "./a"; template strings are skipped."""
    function = node.field("function")
    is_dynamic_import = function is not None and function.kind() == "import"
    is_require = function is not None and function.kind() == "identifier" and function.text() == "require"
    arguments = node.field("arguments")
    if not (is_dynamic_import or is_require) or arguments is None or not arguments.named_children():
        return None
    return _string_specifier(arguments.named_children()[0])


def python_imports(node: SgNode) -> list[PythonImport]:
    """`import a.b, c as d` -> one absolute import per module; `from` imports -> one."""
    if node.kind() == "import_statement":
        return [PythonImport(0, _dotted_path(_module_of(child).text()), ()) for child in node.named_children()]
    module_text = node_text(node.field("module_name"))
    level = len(module_text) - len(module_text.lstrip("."))
    names = tuple(
        _module_of(child).text()
        for child in node.named_children()[1:]
        if child.kind() != "wildcard_import" and _module_of(child) is not None
    )
    return [PythonImport(level, _dotted_path(module_text[level:]), names)]


def _module_of(child: SgNode) -> SgNode:
    """The module-name node, unwrapping `x as y` aliases."""
    return child.field("name") if child.kind() == "aliased_import" else child


def _dotted_path(text: str) -> str:
    """a.b.c -> a/b/c."""
    return text.replace(".", "/")


def _string_specifier(node: SgNode | None) -> str | None:
    """A string literal's text without quotes; None for anything else."""
    if node is None or node.kind() != "string":
        return None
    text = node.text()
    return text[1:-1] if len(text) >= 2 else None


def resolve_imports(
    importer: str,
    js_specifiers: tuple[str, ...],
    py_imports: tuple[PythonImport, ...],
    file_paths: frozenset[str],
    cfg: EngineConfig,
) -> set[str]:
    """Distinct scanned files this file imports; never itself, never packages."""
    resolved = set()
    for specifier in js_specifiers:
        resolved.update(_resolve_js(importer, specifier, file_paths, cfg))
    for imported in py_imports:
        resolved.update(_resolve_python(importer, imported, file_paths, cfg))
    resolved.discard(importer)
    return resolved


def _resolve_js(importer: str, specifier: str, file_paths: frozenset[str], cfg: EngineConfig) -> list[str]:
    """The scanned file a JS specifier reaches (relative or alias), as a 0- or 1-item list."""
    if specifier.startswith(("./", "../")):
        bases = [posixpath.join(posixpath.dirname(importer), specifier)]
    else:
        bases = _alias_bases(specifier, cfg)
    for base in bases:
        hit = _first_existing(_js_candidates(posixpath.normpath(base)), file_paths)
        if hit is not None:
            return [hit]
    return []


def _alias_bases(specifier: str, cfg: EngineConfig) -> list[str]:
    """tsconfig `paths` patterns first (one `*` wildcard), then `baseUrl`."""
    root = cfg.ts_base_url or "."
    bases = []
    for pattern, targets in cfg.ts_path_aliases.items():
        bases += _expand_alias(pattern, targets, specifier, root)
    if cfg.ts_base_url is not None:
        bases.append(posixpath.join(cfg.ts_base_url, specifier))
    return bases


def _expand_alias(pattern: str, targets: list[str], specifier: str, root: str) -> list[str]:
    """`"@/*": ["src/*"]` turns `@/b` into `src/b`; a non-matching pattern gives nothing."""
    star = _wildcard_match(pattern, specifier)
    if star is None:
        return []
    return [posixpath.join(root, target.replace("*", star)) for target in targets]


def _wildcard_match(pattern: str, specifier: str) -> str | None:
    """What `*` matched if the specifier fits the alias pattern ('' if exact), else None."""
    if "*" not in pattern:
        return "" if pattern == specifier else None
    prefix, suffix = pattern.split("*", 1)
    fits = specifier.startswith(prefix) and specifier.endswith(suffix)
    if not fits or len(specifier) < len(prefix) + len(suffix):
        return None
    return specifier[len(prefix) : len(specifier) - len(suffix)]


def _js_candidates(base: str) -> list[str]:
    """Paths to try: as written, .js -> .ts / .tsx, each extension, then /index.*."""
    candidates = [base]
    if base.endswith(".js"):
        candidates += [base[:-3] + ".ts", base[:-3] + ".tsx"]
    candidates += [base + extension for extension in JS_EXTENSIONS]
    candidates += [posixpath.join(base, "index" + extension) for extension in JS_EXTENSIONS]
    return candidates


def _resolve_python(
    importer: str, imported: PythonImport, file_paths: frozenset[str], cfg: EngineConfig
) -> list[str]:
    """Scanned files a Python import reaches, from the importer's folder or each source root."""
    if imported.level:
        folders = [_relative_folder(importer, imported.level)]
    else:
        folders = list(cfg.python_source_roots)
    resolved = []
    for folder in folders:
        resolved += _resolve_in_folder(folder, imported, file_paths)
    return resolved


def _resolve_in_folder(folder: str, imported: PythonImport, file_paths: frozenset[str]) -> list[str]:
    """The module or package file under one folder, plus each name tried as a submodule."""
    module_dir = posixpath.join(folder, imported.module) if imported.module else folder
    targets = [_first_existing(_python_candidates(module_dir, bool(imported.module)), file_paths)]
    # `from a.b import c`: c may be a submodule
    targets += [
        _first_existing(_python_candidates(posixpath.join(module_dir, _dotted_path(name)), True), file_paths)
        for name in imported.names
    ]
    return [target for target in targets if target is not None]


def _relative_folder(importer: str, level: int) -> str:
    """One folder up per leading dot beyond the first."""
    folder = posixpath.dirname(importer)
    for _ in range(level - 1):
        folder = posixpath.dirname(folder)
    return folder or "."


def _python_candidates(module_dir: str, is_module: bool) -> list[str]:
    """Paths to try: mod.py then mod/__init__.py; only __init__.py for a bare package."""
    package = posixpath.normpath(posixpath.join(module_dir, "__init__.py"))
    if not is_module:
        return [package]  # `from . import x`: the package itself
    return [posixpath.normpath(module_dir + ".py"), package]


def _first_existing(candidates: list[str], file_paths: frozenset[str]) -> str | None:
    """The first candidate that is in the scanned file set, or None."""
    return next((path for path in candidates if path in file_paths), None)
