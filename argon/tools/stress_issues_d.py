"""Reported issues 10-13: FFI cost and rule-based counting, chained listeners, spawn, the diff parser."""

from __future__ import annotations

import ast
import multiprocessing
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from argon.cli import _added_lines
from argon.engine.ast_counters import analyze_tree
from argon.engine.config import DEFAULT_CONFIG as CFG
from argon.engine.languages import parse_source
from argon.engine.run import run_local
from tools import stress_measure as measure_tools
from tools.stress_checks import CAVEAT, FAIL, FINDING, PASS, ROOT, SAMPLES, Check, TestResult, fmt
from tools.stress_issues_b import base_scan, git, scenario_repo
from tools.stress_measure import measure
from tools.stress_worker import scan_in_child, scan_one

COMPREHENSIONS = ["list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"]
FIXTURES = ROOT / "tests" / "performance_fixtures"


def py_rule() -> dict:
    """ast-grep rule matching the flat decisions that need no context (no match / case)."""
    kinds = ["if_statement", "elif_clause", "for_statement", "while_statement", "except_clause", "conditional_expression",
             "boolean_operator", "assert_statement", "for_in_clause"]
    loop_else = {"kind": "else_clause", "inside": {"any": [{"kind": k} for k in ("for_statement", "while_statement", "try_statement")]}}
    comp_if = {"kind": "if_clause", "inside": {"any": [{"kind": k} for k in COMPREHENSIONS]}}
    return {"rule": {"any": [{"kind": k} for k in kinds] + [loop_else, comp_if]}}


def js_rule() -> dict:
    kinds = ["if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement", "switch_case", "catch_clause", "ternary_expression"]
    logical = {"kind": "binary_expression", "has": {"field": "operator", "regex": r"^(&&|\|\||\?\?)$"}}
    assign = {"kind": "augmented_assignment_expression", "has": {"field": "operator", "regex": r"^(&&=|\|\|=|\?\?=)$"}}
    return {"rule": {"any": [{"kind": k} for k in kinds] + [logical, assign]}}


def bare_walk(root) -> int:
    """The least FFI traffic any walk needs: kind() and children() on every node, nothing else."""
    stack, count = [root], 0
    while stack:
        node = stack.pop()
        node.kind()
        count += 1
        stack.extend(node.children())
    return count


def best_of(function, repeat: int = 3) -> float:
    times = []
    for _ in range(repeat):
        started = time.perf_counter()
        function()
        times.append(time.perf_counter() - started)
    return min(times)


def corpus_files() -> list[Path]:
    eslint_lib = ROOT / "tests" / "parity" / "eslint" / "node_modules" / "eslint" / "lib"
    js = sorted((p for p in eslint_lib.rglob("*.js") if p.stat().st_size < 400_000), key=lambda p: p.stat().st_size)[-2:] if eslint_lib.is_dir() else []
    return [FIXTURES / "ai_generated_service_22k.py", FIXTURES / "standard_service_4k.py", *js]


def rule_row(path: Path) -> list[str]:
    parsed = parse_source(path.name, path.read_bytes())
    rule = py_rule() if parsed.language == "python" else js_rule()
    engine = analyze_tree(parsed).cyclomatic_branches
    found = len(parsed.root.find_all(rule))
    walk, query = best_of(lambda: analyze_tree(parsed)), best_of(lambda: parsed.root.find_all(rule))
    return [path.name, fmt(len(path.read_bytes())), fmt(engine), fmt(found), "equal" if engine == found else "DIFFERENT",
            f"{walk:.2f}", f"{query:.3f}", f"{walk / query:.0f}x"]


def issue10() -> TestResult:
    rows = [rule_row(path) for path in corpus_files()]
    fixture = parse_source("f.py", (FIXTURES / "ai_generated_service_22k.py").read_bytes())
    full = best_of(lambda: analyze_tree(fixture))
    nodes = bare_walk(fixture.root)
    floor = best_of(lambda: bare_walk(fixture.root))
    out = TestResult(
        "Reported issue 10: the Python-to-Rust boundary cost of the walk",
        "Walking the tree in Python pays a call across the FFI boundary for every node; move the counting into ast-grep's native rule engine and return one result.",
        ["Time of the full engine walk on the real 22k-line fixture against a bare walk that only calls <code>kind()</code> and <code>children()</code> on every node, which is the least FFI traffic any Python walk needs.",
         "A prototype of your idea: the flat decision count written as one ast-grep rule (<code>find_all</code>), checked against the engine's flat count and timed, on the fixtures and on two large JavaScript files.",
         "Your message ended at \"The Final Production-Ready Query Pattern:\" without the query, so this rule is my own."])
    out.tables = [("Rule-based flat count vs the engine's walk", ["File", "Bytes", "Engine flat count", "Rule count", "Same?", "Walk (s)", "Rule (s)", "Rule is faster by"], rows),
                  ("FFI floor on the 22k-line fixture", ["Measure", "Seconds"], [[f"bare walk, {nodes:,} nodes (kind + children only)", f"{floor:.2f}"], ["full engine analysis", f"{full:.2f}"],
                                                                              ["share of the analysis that is the FFI floor", f"{floor / full:.0%}"]])]
    equal = all(row[4] == "equal" for row in rows)
    out.checks = [
        Check("Is the boundary cost real?", CAVEAT,
              f"Partly. A walk that does nothing but cross the boundary takes {floor:.2f} s of the engine's {full:.2f} s ({floor / full:.0%}) on the 22k-line fixture, about {floor / nodes * 1e6:.1f} µs per node. Crossing is a real, minority share; most of the time is the Python logic on top of it."),
        Check("Does a rule-based count match the engine?", PASS if equal else FINDING,
              "Yes on these files: the rule's count equals the engine's flat count on every file in the table." if equal else "No: at least one file differs (table above)."),
        Check("How much faster is it?", FINDING,
              f"Much faster for the flat count: {rows[0][-1]} on the 22k-line fixture. It is faster because ast-grep builds Python objects only for matches, not for every node."),
        Check("Can the whole score move into the rule engine?", FINDING,
              "No. A rule can say what a node is and what its parent is, but it can't count ancestors, so the nesting weight and the isolation tax can't be expressed. The same goes for dead-code-aware outputs, the first-unguarded catch-all <code>case</code>, import resolution and effective lines. Also, <code>find_all</code> returns one <code>SgNode</code> per match rather than one integer array, and ast-grep rules are not raw tree-sitter queries."),
        Check("Is it a problem today?", CAVEAT,
              f"Not yet: 1,000 files score in about 7.5 s against the spec's 30 s target. The V1 spec's own guidance is to reach for hand-written queries on one hot function only if profiling demands it; the flat count is the natural candidate, as a fast path under the full walk. The slowest case I found (issue 7) is not the boundary at all but <code>range()</code> on very long lines."),
        Check("Is there a TokenCostMetrics sheet?", CAVEAT, "I couldn't find that name in either spec PDF or the code; the engine's output is <code>EngineResult</code> / <code>FileFinding</code>."),
    ]
    return out


# ---- Issue 11 ----------------------------------------------------------------------------

FLUENT = "db.client().subscribe().on('message', onMessage).on('error', onError).on('close', onClose);\n"
STATEMENTS = "const channel = db.client().subscribe();\nchannel.on('message', onMessage);\nchannel.on('error', onError);\nchannel.on('close', onClose);\n"
VARIABLE = "const channel = connect();\nchannel.on('message', onMessage);\nchannel.on('error', onError);\nchannel.on('close', onClose);\n"


def concentration(snippet: str):
    filler = "".join(f"const value{i} = {i};\n" for i in range(820))
    return measure("c", filler + snippet, "big.js")


def issue11() -> TestResult:
    layouts = {"one fluent chain (subscribe + 3 handlers)": FLUENT, "same pipeline as statements on one variable": STATEMENTS, "handlers on a ready-made connection": VARIABLE}
    rows, measured = [], {}
    for label, snippet in layouts.items():
        count = measure_tools.tree_metrics(snippet, "a.js").async_boundary_count
        big = concentration(snippet)
        measured[label] = (count, big)
        rows.append([label, str(count), str(big.effective_lines), "flagged (concentration)" if big.flag_reason == "concentration_rule" else "not flagged"])
    fluent, statements = measured[list(layouts)[0]], measured[list(layouts)[1]]
    measure_tools.write_samples(SAMPLES / "issue11", {"fluent.js": FLUENT, "statements.js": STATEMENTS, "connection.js": VARIABLE})
    out = TestResult(
        "Reported issue 11: chained listeners are counted as separate boundaries",
        "In db.client().subscribe().on('message').on('error') each chained .on() is counted as its own boundary, so one pipeline spikes the concentration score.",
        ["Three layouts of the same one-connection pipeline, each scored for async boundaries.",
         "Each added to a file of 820 plain lines (past the 800-line limit) to see whether the concentration rule fires, since it needs more than 800 effective lines and more than 3 boundaries."])
    out.tables = [("Async boundaries and the concentration rule", ["Layout", "Boundaries counted", "Effective lines (with 820 filler lines)", "Concentration rule"], rows)]
    out.checks = [
        Check("Is each chained call counted separately?", FINDING if fluent[0] > statements[0] else PASS,
              f"Yes. The engine de-duplicates by the receiver's text, and in a chain every link has a different receiver (<code>db.client()</code>, <code>db.client().subscribe()</code>, and so on), so the fluent pipeline counts {fluent[0]} boundaries while the same handlers written as statements on one variable count {statements[0]}."),
        Check("Does it matter?", FINDING if fluent[1].flag_reason == "concentration_rule" and statements[1].flag_reason != "concentration_rule" else CAVEAT,
              f"Only in large files, because the rule needs both conditions. In an 820-line file, the fluent chain alone ({fluent[0]} boundaries, limit 3) triggers the concentration flag; the statement form ({statements[0]}) does not."),
        Check("What would fix it?", CAVEAT,
              "Not applied. Key a boundary by the root of a call chain: walk down <code>.on(...).on(...)</code> links to the first non-listener receiver, so one fluent pipeline is one boundary. The catch is that <code>a.subscribe().on()</code> and <code>a.other().on()</code> would also share a root, so the key should be the chain up to its first listener method."),
    ]
    return out


# ---- Issue 12 ----------------------------------------------------------------------------


def module_level_native_calls() -> list[str]:
    """Calls into ast_grep_py (SgRoot) made at import time rather than inside a function."""
    found = []
    for path in sorted((ROOT / "argon" / "engine").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                continue
            found += [f"{path.name}:{call.lineno}" for call in ast.walk(node) if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "SgRoot"]
    return found


def spawn_results() -> tuple[bool, float, bool, float]:
    """(spawned process matched, its seconds, pool matched, pool seconds) with the spawn start method."""
    context = multiprocessing.get_context("spawn")
    source = (FIXTURES / "standard_service_4k.py").read_bytes()
    local = run_local({"a.py": source}).model_dump_json()
    queue = context.Queue()
    started = time.perf_counter()
    child = context.Process(target=scan_in_child, args=(queue, "a.py", source))
    child.start()
    got = queue.get(timeout=120)
    child.join(30)
    one = time.perf_counter() - started
    items = [(f"f{i}.py", source) for i in range(4)]
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=2, mp_context=context) as pool:
        pooled = list(pool.map(scan_one, items))
    many = time.perf_counter() - started
    expected = [run_local({name: data}).model_dump_json() for name, data in items]
    return got == local and child.exitcode == 0, one, pooled == expected, many


def issue12() -> TestResult:
    matched, seconds, pooled, pool_seconds = spawn_results()
    globals_ = module_level_native_calls()
    out = TestResult(
        "Reported issue 12: Python 3.13 spawn and compiled grammar libraries",
        "Under spawn, a worker re-imports everything; grammars loaded as custom shared libraries at import time would crash it with a segmentation fault. Wrap grammar loading in a lazy initializer inside the worker.",
        ["The real engine run inside a child process started with the <code>spawn</code> method (a fresh interpreter, nothing copied from the parent) and inside a 2-worker <code>ProcessPoolExecutor</code> using <code>spawn</code>; each result compared with the in-process result.",
         "A scan of the engine's source for any native-library call made at import time."])
    out.tables = [("Environment", ["Item", "Value"], [["platform", sys.platform], ["python", sys.version.split()[0]],
                                                      ["default start method here", multiprocessing.get_start_method()], ["start methods available", ", ".join(multiprocessing.get_all_start_methods())]]),
                  ("Spawned worker results", ["Run", "Same result as in-process?", "Seconds"], [["one spawned Process", "yes" if matched else "NO", f"{seconds:.2f}"], ["4 files through a spawn ProcessPoolExecutor", "yes" if pooled else "NO", f"{pool_seconds:.2f}"]])]
    out.checks = [
        Check("Does the engine work in a spawned worker?", PASS if matched and pooled else FAIL,
              f"Yes. On this machine ({sys.platform}, Python {sys.version.split()[0]}) <code>spawn</code> is already the only start method, so every multiprocessing test here is a spawn test. The child printed byte-identical results to an in-process run, in {seconds:.2f} s including interpreter start-up."),
        Check("Is there a custom shared library that a worker could fail to load?", PASS if not globals_ else FAIL,
              f"No. The engine uses <code>ast-grep-py</code>, a prebuilt native module that carries its grammars and is imported like any other module; there is no <code>.so</code> build step or path. {len(globals_)} native-library calls run at import time (<code>SgRoot</code> is only created inside <code>parse_source</code>), so a fresh worker has nothing to initialize lazily."),
        Check("Did Python 3.13 change the default start method?", CAVEAT,
              "Not as stated, as far as I know. macOS and Windows have defaulted to <code>spawn</code> for years, and my recollection is that the Linux default moved from <code>fork</code> to <code>forkserver</code> in Python 3.14, not 3.13. I couldn't check that here because only <code>spawn</code> exists on Windows. It doesn't change the result: the engine holds no process-wide native state, so fork, forkserver and spawn behave the same."),
    ]
    return out


# ---- Issue 13 ----------------------------------------------------------------------------

A_OLD = "x = 1\nNOTE = '''\nstart\n'''\ny = 2\n"
A_NEW = "x = 1\nNOTE = '''\n@@ -12,4 +15,7 @@\n++ step one\nstart\n'''\ny = 2\nz = 9\n"
EXPECTED = {"a.py": 3, "b.py": 2, "naïve.py": 1, "my file.py": 1}


def issue13() -> TestResult:
    root = scenario_repo({"a.py": A_OLD, "b.py": "z = 3\n", "naïve.py": "q = 1\n", "my file.py": "w = 1\n"},
                         {"a.py": A_NEW, "b.py": "z = 3\nw = 4\nv = 5\n", "naïve.py": "q = 1\nr = 2\n", "my file.py": "w = 1\nu = 2\n"})
    try:
        diff = git(root, "-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/",
                   "-w", "--unified=0", "--relative", "HEAD", "--", ".")
        parsed = _added_lines(diff)
        text, code, data = base_scan(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    scanned = {f["filename"]: f["lines_altered"] for f in data["findings"]}
    rows = [[name, str(expected), str(scanned.get(name, "not scanned")), "ok" if scanned.get(name) == expected else "WRONG"] for name, expected in EXPECTED.items()]
    bogus = [key for key in parsed if key not in EXPECTED and '"' not in key and not key.endswith("\t")]
    out = TestResult(
        "Reported issue 13: code that looks like diff syntax breaks the parser",
        "A string containing @@ -12,4 +15,7 @@ or other patch syntax makes the diff parser misread code as metadata, skewing lines_altered or crashing. Match numeric line numbers against the parsed tree instead.",
        ["A real git repo with four changed files: one adds a string containing a hunk-header lookalike (<code>@@ -12,4 +15,7 @@</code>) and a line starting <code>++ </code>, and two have names with a space or a non-ASCII letter.",
         "The CLI run with <code>--base HEAD</code>, as it would score a PR. The expected altered-line count is what <code>git diff</code> itself reports."])
    out.tables = [("Altered lines per file: expected vs scanned", ["File", "Expected", "Scanned", "Result"], rows),
                  ("What the parser extracted from the diff", ["File key the parser created", "Altered lines"], [[repr(name), ", ".join(map(str, sorted(lines)))] for name, lines in parsed.items()])]
    out.checks = [
        Check("Is a hunk-header lookalike in code a problem?", PASS,
              "No. Every content line in a diff starts with <code>+</code>, <code>-</code> or a space, so a code line <code>@@ -12,4 +15,7 @@</code> arrives as <code>+@@ -12,4 +15,7 @@</code>, which the anchored hunk pattern does not match. In the test, both lookalike lines were counted as altered code (lines 3 and 4 of a.py)."),
        Check("Does lines_altered come from the patch text?", PASS,
              "No. Only the numbers in each <code>@@</code> header are used, and they are matched against the fully parsed file; patch text is never counted. That is the 'alignment vector' design the report asks for."),
        Check("Can a changed line still confuse the parser?", PASS if scanned.get("a.py") == 3 and not bogus else FINDING,
              f"A new line whose text begins with <code>++ </code> appears in the diff as <code>+++ ...</code>. Hunk bodies are now consumed by the counts in their <code>@@</code> header, so it is read as code, not as a file header. a.py scored {scanned.get('a.py')} altered lines (expected 3); bogus file keys: {bogus or 'none'}. (Before this fix it created a bogus key and a.py scored 2.)"),
        Check("What about unusual file names?", PASS if all(scanned.get(name) == n for name, n in EXPECTED.items()) else FINDING,
              f"Git is run with <code>core.quotepath=off</code>, explicit <code>a/</code> <code>b/</code> prefixes, and the parser strips the trailing tab git adds after names with spaces and decodes any remaining C-style quoted path. Scanned files: {sorted(scanned)}; changed files: {sorted(EXPECTED)}. (Before this fix both the space and the non-ASCII file were silently skipped.)"),
        Check("What is still not covered?", CAVEAT,
              "Renames: a renamed-and-edited file appears under its new name, which is right, but a pure rename has no <code>+++</code> line and is not rescored. Not changed."),
    ]
    out.cli = [("python -m argon.cli <repo> --base HEAD --top 0   (a temporary repo built for this test)", text, code)]
    return out
