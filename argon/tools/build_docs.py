"""Build argon/docs/argon_audit.html: a self-contained page documenting what Argon does today.

Run from the project root (the folder holding pyproject.toml):

    python -m tools.build_docs              # writes docs/argon_audit.html
    python -m tools.build_docs --with-tests # also runs the test suite and records the result

Everything is read from the source, so rebuilding after a change keeps the page honest.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
import sys
from collections import Counter
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from argon import cli
from tools import docs_html as h
from tools import markdown_lite
from tools.collect import Entry, Module, analyze_module, collect_tests, fields_of, product_files
from tools.spec_map import ROWS

ROOT = Path(__file__).resolve().parent.parent
TITLE = "Argon"

CAPABILITIES = [
    "Parses <code>.js .jsx .mjs .cjs .ts .mts .cts .tsx .py</code> with Rust-backed ast-grep (tree-sitter grammars)",
    "Counts decisions: flat cyclomatic count (radon / ESLint parity) and a nesting-weighted score with the isolation tax on nested functions",
    "Scores every named function plus module-level code, with <code>rating_master</code> and <code>rating_nesting</code>",
    "Flags files: concentration, semantic path logic, fragmentation (PR-wide), plus fail-closed <code>unparseable</code>; files too large to analyze are listed under <code>errors</code> and left out of the score",
    "Computes a 0-100 purity score weighted by altered lines and a success / failure check conclusion",
    "Resolves imports against the scanned set: relative paths, tsconfig aliases / baseUrl (the CLI reads tsconfig.json and passes the alias map in through <code>EngineConfig</code>, so the engine stays I/O-free), Python absolute and relative",
    "Counts async boundaries (listeners by receiver, constructors, decorators) and determinate outputs",
    "Counts effective lines (statements, comments and docstrings excluded, long lines weighted)",
    "Scores only changed lines with <code>--base REF</code>; inherits <code>.gitignore</code> inside a git repo",
    "Prints a file table, a function ranking, the PR-comment preview, or the full result as JSON",
    "Calibrates thresholds from human and agentic code (<code>--calibrate-out</code>, <code>--fit-thresholds</code>)",
    "Pure engine: bytes in, models out; no disk, git or network access under <code>engine/</code> (a test enforces it)",
]
NOT_BUILT = [
    "GitHub App, webhook receiver, debounce and Check Runs (Phase 1)",
    "Worker processes, queue and cancellation (Phase 1)",
    "Postgres schema and repository (Phase 1), dashboard REST API (Phase 2)",
    "Enterprise forwarding and cloud templates, <code>.argonignore</code> and <code>// argon-skip</code> (Phase 3)",
]
PIPELINE = [
    ("1 skip", "should_skip_file: vendored and generated paths, unsupported extensions"),
    ("2 parse", "parse_source: decode safely, ast-grep tree, ERROR and MISSING coverage"),
    ("3 walk", "analyze_tree: one iterative pass collecting decisions, scopes, outputs, imports, lines"),
    ("4 rate", "per-function rating_master and rating_nesting"),
    ("5 flag", "concentration, semantic path, coupling hotspot, unparseable"),
    ("6 fragment", "largest import group over the density threshold"),
    ("7 score", "weighted purity score and conclusion"),
    ("8 report", "table, ranking, summary markdown, JSON"),
]


def build(out: Path, run_tests: bool) -> Path:
    modules = [analyze_module(ROOT, path) for path in product_files(ROOT)]
    tests = collect_tests(ROOT)
    spec_problems = check_spec_map(modules, tests)
    body = "".join(
        [
            overview(modules, tests, run_tests),
            h.section("capabilities", "Capabilities", capabilities()),
            h.section("pipeline", "Pipeline", h.pipeline(PIPELINE) + "<p>Entry point: <code>run_local(files, altered, cfg)</code> in <code>engine/run.py</code>.</p>"),
            h.section("scoring", "Scoring rules", scoring_rules()),
            h.section("cli", "Command line", cli_reference()),
            h.section("config", "Configuration", config_reference()),
            h.section("models", "Data models", models_reference()),
            h.section("sample", "Sample run", sample_run()),
            h.section("spec", "Spec coverage", spec_coverage(spec_problems)),
            h.section("modules", "Module reference", module_reference(modules)),
            h.section("tests", "Tests", test_inventory(tests)),
            h.section("audit", "Audit notes", audit_notes(modules, spec_problems)),
        ]
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(h.page(TITLE, body, modules), encoding="utf-8")
    return out


def all_entries(modules: list[Module]) -> list[tuple[Module, Entry]]:
    return [(module, entry) for module in modules for entry in module.entries]


def overview(modules: list[Module], tests: dict, run_tests: bool) -> str:
    entries = [entry for _, entry in all_entries(modules)]
    callables = [entry for entry in entries if entry.kind != "class"]
    undocumented = sum(1 for entry in entries if not entry.doc)
    grades = Counter(entry.rank for entry in callables)
    cards = h.cards(
        [
            ("modules", str(len(modules))), ("lines of product code", f"{sum(m.lines for m in modules):,}"),
            ("functions and methods", str(len(callables))), ("classes", str(len(entries) - len(callables))),
            ("tests", str(sum(len(names) for names in tests.values()))),
            ("radon A or B", f"{grades['A'] + grades['B']} of {len(callables)}"), ("undocumented", str(undocumented)),
        ]
    )
    intro = (
        "<p>Argon scores structural bloat in JavaScript, TypeScript and Python: it counts decisions, weighs them by "
        "nesting, rates each function, and turns the result into a purity score. This page is generated from the "
        "source, so it describes what the code does today.</p>"
    )
    return h.section("overview", "Overview", intro + cards + provenance(run_tests))


def provenance(run_tests: bool) -> str:
    rows = [
        ["generated", datetime.now().strftime("%Y-%m-%d %H:%M")],
        ["python", sys.version.split()[0]],
        ["ast-grep-py", version("ast-grep-py")], ["pydantic", version("pydantic")], ["radon", version("radon")],
        ["test suite", test_result() if run_tests else "not run for this build (use --with-tests)"],
    ]
    return h.table(["", ""], rows)


def test_result() -> str:
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
    last = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else "no output"
    return f"{last} (exit {done.returncode})"


def capabilities() -> str:
    return "<h3>Built</h3>" + h.bullets(CAPABILITIES) + "<h3>Not built yet</h3>" + h.bullets(NOT_BUILT)


def scoring_rules() -> str:
    text = (ROOT / "SCORING.md").read_text(encoding="utf-8")
    body = text.split("\n", 1)[1]  # drop the "# How Argon scores code" title
    return markdown_lite.to_html(body).replace("<h2", "<h3").replace("</h2>", "</h3>")


def cli_reference() -> str:
    rows = []
    for action in cli._parser()._actions:
        if action.option_strings == ["-h", "--help"]:
            continue
        name = ", ".join(action.option_strings) or action.dest
        metavar = action.metavar if isinstance(action.metavar, str) else " ".join(action.metavar or [])
        default = "" if action.default in (None, False, []) else str(action.default)
        rows.append([f"<code>{h.esc(name)} {h.esc(metavar)}</code>".replace(" </code>", "</code>"), h.esc(action.help or ""), f"<code>{h.esc(default)}</code>" if default else ""])
    codes = f"<p>Exit codes: <code>{cli.EXIT_SUCCESS}</code> success, <code>{cli.EXIT_FAILURE}</code> failure, <code>{cli.EXIT_USAGE}</code> usage error or nothing to analyze.</p>"
    usage = "<pre><code>python -m argon.cli &lt;path&gt; [&lt;path&gt; ...] [flags]</code></pre>"
    return usage + h.table(["Flag", "Meaning", "Default"], rows, raw=True) + codes


def config_reference() -> str:
    note = "<p>Every tunable lives in <code>EngineConfig</code>; unknown keys in a <code>--config</code> TOML are an error. Thresholds come from <code>engine/calibrated.py</code>.</p>"
    return note + h.fields_table(fields_of(ROOT / "argon" / "engine" / "config.py", "EngineConfig"))


def models_reference() -> str:
    parts = []
    for name in ("FunctionFinding", "FileFinding", "SkippedFile", "EngineResult"):
        parts.append(f"<h3>{name}</h3>" + h.fields_table(fields_of(ROOT / "argon" / "schemas.py", name)))
    return "".join(parts)


def sample_run() -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(["tests/fixtures/spec", "--top", "8"])
    return (
        "<p>Output of <code>python -m argon.cli tests/fixtures/spec --top 8</code> on the spec's scoring fixtures "
        f"(exit code {code}):</p><pre><code>{h.esc(buffer.getvalue())}</code></pre>"
    )


def check_spec_map(modules: list[Module], tests: dict) -> list[str]:
    """Problems found while verifying that every spec reference still exists."""
    known = {name for names in tests.values() for name, _ in names}
    paths = {module.path for module in modules}
    problems = []
    for row in ROWS:
        problems += [f"{row.ident}: test {name} not found" for name in row.tests if name not in known]
        problems += [f"{row.ident}: module {name} not found" for name in _module_names(row.where) if f"argon/{name}" not in paths]
    return problems


def _module_names(where: str) -> list[str]:
    """The .py files named in a SpecRow.where, ignoring notes in parentheses."""
    parts = [part.strip() for part in where.split("(")[0].split(",")]
    return [part for part in parts if part.endswith(".py")]


def spec_coverage(problems: list[str]) -> str:
    rows = []
    for row in ROWS:
        tests = "<br>".join(f"<code>{h.esc(name)}</code>" for name in row.tests) or '<span class="mute">none</span>'
        rows.append([row.ident, h.esc(row.title), f'<span class="tag-{row.status}">{row.status}</span>', h.esc(row.where), tests])
    status = '<p class="ok">Every referenced test and module exists.</p>' if not problems else h.bullets([f'<span class="bad">{h.esc(p)}</span>' for p in problems])
    legend = "<p><b>built</b> as specified, <b>amended</b> differs from the PDF (see Scoring rules), <b>switch</b> built but off by default, <b>phase1</b> not part of the MVP.</p>"
    return legend + status + h.table(["ID", "Item", "Status", "Where", "Tests"], rows, raw=True)


def module_reference(modules: list[Module]) -> str:
    controls = (
        '<input id="q" class="search" placeholder="Filter functions by name or description" aria-label="Filter">'
        '<p><button id="expand">Expand all</button> <button id="collapse">Collapse all</button> '
        '<span id="count" class="mute"></span></p>'
    )
    return controls + "".join(h.module_section(module) for module in modules)


def test_inventory(tests: dict[str, list[tuple[str, int]]]) -> str:
    total = sum(len(names) for names in tests.values())
    note = f"<p>{total} test functions (parametrized tests run several cases each). Run with <code>python -m pytest</code>; add <code>-m parity</code> for the radon and ESLint corpus checks.</p>"
    rows = [[f"<code>{h.esc(path)}</code>", str(len(names)), ", ".join(f"<code>{h.esc(name)}</code>" for name, _ in names)] for path, names in tests.items()]
    return note + h.table(["File", "Tests", "Names"], rows, raw=True)


def audit_notes(modules: list[Module], spec_problems: list[str]) -> str:
    pairs = all_entries(modules)
    callables = [(m, e) for m, e in pairs if e.kind != "class"]
    return "".join(
        [
            "<h3>Radon grades</h3>", grade_table(callables),
            "<h3>Highest radon complexity</h3>", top_table(callables, lambda e: e.complexity, "radon"),
            "<h3>Highest Argon score</h3>", top_table([p for p in callables if p[1].score is not None], lambda e: e.score or 0, "Argon score"),
            "<h3>Functions flagged nested</h3>", nested_list(callables),
            "<h3>Largest modules</h3>", h.table(["Module", "Lines"], [[m.path, str(m.lines)] for m in sorted(modules, key=lambda m: -m.lines)[:8]]),
            "<h3>Undocumented definitions</h3>", undocumented_list(pairs),
            "<h3>Known limits</h3>", h.bullets(KNOWN_LIMITS),
        ]
    )


KNOWN_LIMITS = [
    "Thresholds were fitted on two synthetic fixtures; refit on real repos before gating real pull requests",
    "<code>forEach</code> / <code>.map</code> callbacks are taxed as nested functions but are not counted as loops",
    "Python files with syntax errors above 5% coverage fail closed; smaller errors are scored with a warning",
    "tree-sitter grammars in ast-grep 0.45.3 can't parse a few newer syntaxes (TS variance annotations); those files are flagged",
    "The effective-line rule keeps packed code at about 63% of its formatted size, not the original spec's 70%",
]


def grade_table(callables: list[tuple[Module, Entry]]) -> str:
    counts = Counter(entry.rank for _, entry in callables)
    return h.table(["Grade", "Functions"], [[h.grade(rank), str(counts[rank])] for rank in "ABCDEF" if counts[rank]], raw=True)


def top_table(pairs: list[tuple[Module, Entry]], key, label: str, count: int = 10) -> str:
    rows = [[f"{m.path}:{e.start}", f"<code>{h.esc(e.qualname)}</code>", str(key(e))] for m, e in sorted(pairs, key=lambda p: -key(p[1]))[:count]]
    return h.table(["Location", "Function", label], rows, raw=True)


def nested_list(callables: list[tuple[Module, Entry]]) -> str:
    flagged = [f"<code>{h.esc(m.path)}:{e.start} {h.esc(e.qualname)}</code> (depth {e.depth})" for m, e in callables if e.nested]
    return h.bullets(flagged) if flagged else '<p class="ok">None: no function has a decision 3 or more levels deep.</p>'


def undocumented_list(pairs: list[tuple[Module, Entry]]) -> str:
    missing = [f"<code>{h.esc(m.path)}:{e.start} {h.esc(e.qualname)}</code>" for m, e in pairs if not e.doc]
    return h.bullets(missing) if missing else '<p class="ok">Every function, method and class has a docstring.</p>'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Argon audit page.")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "argon_audit.html")
    parser.add_argument("--with-tests", action="store_true", help="run the test suite and record its result")
    args = parser.parse_args(argv)
    out = build(args.out, args.with_tests)
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
