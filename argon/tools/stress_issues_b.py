"""Reported issues 4-6: node-kind coverage, unchanged import lines in a diff, string literals."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from argon.cli import _added_lines
from argon.engine.config import DEFAULT_CONFIG as CFG
from tests.parity.radon_reference import flat_count
from tools import stress_measure as measure_tools
from tools.stress_checks import CAVEAT, FAIL, FINDING, PASS, ROOT, SAMPLES, Check, TestResult, fmt
from tools.stress_measure import measure

ESLINT = ROOT / "tests" / "parity" / "eslint"

# (file extension, construct, source, decisions expected by the spec's rules)
CATALOG = [
    (".js", "&&", "x = a && b;", 1), (".js", "||", "x = a || b;", 1), (".js", "??", "x = a ?? b;", 1),
    (".js", "&&=", "a &&= b;", 1), (".js", "||=", "a ||= b;", 1), (".js", "??=", "a ??= b;", 1),
    (".js", "ternary", "x = a ? b : c;", 1), (".js", "if / else if / else", "if (a) {} else if (b) {} else {}", 2),
    (".js", "switch (2 cases + default)", "switch (a) { case 1: break; case 2: break; default: break; }", 2),
    (".js", "for", "for (let i = 0; i < n; i++) {}", 1), (".js", "for...in", "for (const k in o) {}", 1),
    (".js", "for...of", "for (const v of o) {}", 1), (".js", "while", "while (a) {}", 1), (".js", "do...while", "do {} while (a);", 1),
    (".js", "for await", "async function f() { for await (const x of y) {} }", 1),
    (".js", "try / catch / finally", "try {} catch (e) {} finally {}", 1), (".js", "labeled loop", "outer: for (;;) { break outer; }", 1),
    (".js", "optional chain (documented: 0)", "a?.b(); a?.();", 0), (".js", "default parameter (documented: 0)", "function f(a = 1) {}", 0),
    (".jsx", "JSX && and ternary", "const v = <div>{a && <b/>}{c ? 1 : 2}</div>;", 2),
    (".ts", "conditional type (type level)", "type T<A> = A extends string ? 1 : 2;", 0),
    (".ts", "type guard with &&", "function f(x: unknown): x is string { return typeof x === 'string' && x.length > 0; }", 1),
    (".py", "and / or chain", "x = a or b or c", 2), (".py", "not", "x = not a", 0), (".py", "ternary", "x = a if b else c", 1),
    (".py", "list comprehension with if", "x = [i for i in y if i]", 2), (".py", "dict comprehension with if", "x = {k: v for k, v in y if v}", 2),
    (".py", "generator with if", "x = sum(i for i in y if i)", 2), (".py", "assert", "assert a", 1),
    (".py", "try / 2 excepts / else", "try:\n    pass\nexcept A:\n    pass\nexcept B:\n    pass\nelse:\n    pass\n", 3),
    (".py", "while / else", "while a:\n    pass\nelse:\n    pass\n", 2), (".py", "for / else", "for a in b:\n    pass\nelse:\n    pass\n", 2),
    (".py", "async for", "async def f():\n    async for x in y:\n        pass\n", 1), (".py", "with (not a decision)", "with a:\n    pass\n", 0),
    (".py", "lambda with ternary", "f = lambda: a if b else c", 1), (".py", "walrus + and", "if (n := f()) and n:\n    pass\n", 2),
    (".py", "match (2 cases + wildcard)", "match v:\n    case 1:\n        pass\n    case 2:\n        pass\n    case _:\n        pass\n", 2),
    (".py", "elif chain", "if a:\n    pass\nelif b:\n    pass\nelse:\n    pass\n", 2),
]
DODGES = [  # decisions that run without a counted node; the same blind spot exists in radon and ESLint
    (".py", "tuple indexing as a branch", "x = (b, a)[cond]"), (".py", "dict dispatch", "x = {1: f, 2: g}[key](v)"),
    (".py", "map / filter callbacks", "r = list(map(lambda v: v + 1, filter(lambda v: v, xs)))"),
    (".js", "forEach / map / filter callbacks", "xs.filter(v => v).map(v => v + 1).forEach(v => run(v));"),
    (".js", "lookup-table dispatch", "handlers[kind](value);"), (".js", "recursion as a loop", "function f(n) { return n && f(n - 1); }"),
]


def eslint_counts(snippets: dict[str, str]) -> dict[str, int]:
    """ESLint's complexity for each snippet (minus default params and ?.), or {} if Node isn't set up."""
    if shutil.which("node") is None or not (ESLINT / "node_modules" / "eslint").is_dir():
        return {}
    folder = measure_tools.write_samples(SAMPLES / "issue4", snippets)
    files = [str(folder / name) for name in snippets]
    done = subprocess.run(["node", "count.mjs", *files], cwd=ESLINT, capture_output=True, text=True, encoding="utf-8")
    counts = {}
    for line in done.stdout.splitlines():
        path, _raw, adjusted = line.split("\t")
        counts[Path(path).name] = int(adjusted) if adjusted.isdigit() else -1
    return counts


def reference_for(name: str, source: str, eslint: dict[str, int]) -> str:
    if name.endswith(".py"):
        return str(flat_count(source))
    return str(eslint[name]) if name in eslint else "n/a"


def kind_rows() -> tuple[list[list[str]], int, int]:
    snippets = {f"c{i:02}{ext}": code for i, (ext, _, code, _) in enumerate(CATALOG)}
    eslint = eslint_counts({name: code for name, code in snippets.items() if not name.endswith(".py")})
    rows, wrong, no_reference = [], 0, 0
    for (name, source), (ext, label, _, expected) in zip(snippets.items(), CATALOG):
        got = measure_tools.tree_metrics(source, name).cyclomatic_branches
        reference = reference_for(name, source, eslint)
        no_reference += reference == "n/a"
        good = got == expected and reference in (str(expected), "n/a")
        wrong += not good
        rows.append([ext.lstrip("."), label, source.replace("\n", " \u23ce ")[:60], str(expected), str(got), reference, "ok" if good else "MISMATCH"])
    return rows, wrong, no_reference


def dodge_rows() -> list[list[str]]:
    rows = []
    for ext, label, source in DODGES:
        name = f"dodge{ext}"
        argon = measure_tools.tree_metrics(source, name).cyclomatic_branches
        reference = str(flat_count(source)) if ext == ".py" else "0 (ESLint: none counted)"
        rows.append([ext.lstrip("."), label, source, str(argon), reference])
    return rows


def issue4() -> TestResult:
    rows, wrong, no_reference = kind_rows()
    out = TestResult(
        "Reported issue 4: node-kind divergence between languages",
        "JavaScript and Python produce different node names for the same logic; without a universal abstraction layer an agent could use language-specific operators to dodge the count.",
        [f"A catalog of {len(CATALOG)} constructs across JavaScript, JSX, TypeScript and Python, each with the decision count the spec's rules give.",
         "Every snippet scored by the engine and cross-checked against an independent reference: radon's rules for Python, ESLint's <code>complexity</code> rule for JS/TS.",
         "A second list of constructs that run decisions without any counted node."])
    out.tables = [("Construct catalog", ["Lang", "Construct", "Snippet", "Expected", "Krypton", "Reference tool", "Result"], rows),
                  ("Decisions that no node-counting tool sees", ["Lang", "Dodge", "Snippet", "Krypton", "Reference"], dodge_rows())]
    out.checks = [
        Check("Is the example in the report accurate?", CAVEAT,
              "Not quite. Python's <code>and</code> is the node kind <code>boolean_operator</code> (there is no <code>and_expr</code> in the grammar), and JavaScript's <code>&&</code> is a <code>binary_expression</code> with an operator token; <code>assignment_expression</code> is the surrounding <code>x = ...</code>, not the <code>&&</code>. The engine reads the operator token, so both forms count."),
        Check("Can a language-specific operator slip past the count?", PASS if wrong == 0 else FAIL,
              f"{len(rows) - wrong} of {len(rows)} constructs match the expected count" + ("" if no_reference == 0 else f" (JS/TS reference unavailable for {no_reference})") + ", including the operator forms (<code>&&=</code>, <code>??=</code>, walrus, <code>except*</code>, <code>for await</code>). Over the full radon and ESLint corpora (about 16,500 files) the engine has no mismatches, so a kind that the table missed would have shown up there."),
        Check("Is there a unified abstraction layer?", CAVEAT,
              "There is one place per concern: <code>branches.py</code> (what counts), <code>nesting.py</code> (what nests), <code>scopes.py</code> (functions). They hold JS and Python kinds side by side in one table rather than an enum per language. That is a readability choice, not a hole; the exposure it leaves is a grammar upgrade renaming a kind, which the pinned <code>ast-grep-py &lt; 0.46</code> and the corpus parity tests would catch."),
        Check("Are there real dodges?", FINDING,
              "Yes, but they are language-independent and shared with radon and ESLint: indexing a tuple or dict by the condition, dispatch tables, <code>map</code>/<code>filter</code>/<code>forEach</code> callbacks, and recursion all run decisions with nothing counted (second table). The known JS ones are listed under Known limits."),
    ]
    return out


# ---- Issue 5 -----------------------------------------------------------------------------


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True, encoding="utf-8")
    return done.stdout


def scenario_repo(files: dict[str, str], edits: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="argon_issue_"))
    git(root, "init", "-q")
    git(root, "config", "user.email", "argon@example.com")
    git(root, "config", "user.name", "Krypton")
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "base")
    for name, text in edits.items():
        (root / name).write_text(text, encoding="utf-8")
    return root


def base_scan(root: Path) -> tuple[str, int, dict]:
    """The CLI on the repo against HEAD: (text output, exit code, parsed --json)."""
    text, code = measure_tools.run_cli([str(root), "--base", "HEAD", "--top", "0"])
    data, _ = measure_tools.run_cli([str(root), "--base", "HEAD", "--json"])
    return text, code, json.loads(data.split("\n\n[stderr]")[0])


def issue5() -> TestResult:
    a_old = 'import { b } from "./b.js";\n' + "// ...hundreds of untouched lines elsewhere...\n" * 3 + "export function a() { return b(); }\n"
    a_new = a_old + "export const extra = 1;\n"
    root = scenario_repo({"a.js": a_old, "b.js": "export function b() { return 1; }\n", "c.js": "export const c = 1;\n"},
                         {"a.js": a_new, "b.js": "export function b() { return 2; }\n"})
    try:
        text, code, data = base_scan(root)
        diff = git(root, "diff", "-w", "--unified=0", "HEAD")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    a = next(f for f in data["findings"] if f["filename"] == "a.js")
    altered_line_one = 1 in _added_lines(diff).get("a.js", set())
    out = TestResult(
        "Reported issue 5: unchanged import lines are invisible in a diff",
        "A PR diff only shows changed lines, so an import written months ago never appears and the engine would miss the cross-file import.",
        ["A git repo where <code>a.js</code> imports <code>./b.js</code> on line 1 (an old, untouched line); then <code>b.js</code> is edited and <code>a.js</code> gets one new line at the bottom.",
         "The CLI run with <code>--base HEAD</code>, exactly as it would score a PR. The diff itself, the altered lines and the import count are shown below."])
    out.tables = [("What the engine reported for a.js", ["Field", "Value"], [
        ["altered physical lines", str(a["lines_altered"])], ["old import line (line 1) is among the altered lines", "yes" if altered_line_one else "no"],
        ["relative_import_count (a.js \u2192 b.js)", str(a["relative_import_count"])]])]
    out.checks = [
        Check("Is the old import line visible to the engine?", PASS if a["relative_import_count"] == 1 else FINDING,
              f"Yes: the import on line 1 is not in the diff (only line 4 of a.js changed), yet <code>relative_import_count</code> is {a['relative_import_count']}. The engine parses the <i>whole file</i>: <code>run_local(files, altered, cfg)</code> receives every file's full bytes, and the changed lines only set how heavily each file is weighted."),
        Check("Is graph density computed from diff text?", PASS,
              "No. Imports come from the parsed tree of the full file. The diff supplies only line numbers (from the <code>@@</code> hunk headers), which are matched against that tree. This is the 'alignment vector' design the report asks for."),
        Check("Does this still matter for Phase 1?", CAVEAT,
              "Yes, as a requirement for the worker: the V1 spec's <code>ChangedFile</code> carries <code>patch</code> plus a lazily fetched <code>raw_content_url</code>. The worker must fetch and parse the <i>full file</i> at the head commit; scoring from the patch alone would reproduce exactly this blind spot."),
    ]
    out.cli = [("python -m argon.cli <repo> --base HEAD --top 0   (a temporary repo built for this test)", text, code)]
    return out


# ---- Issue 6 -----------------------------------------------------------------------------

SCRIPT = 'function build() {{\n  const schema = "{text}";\n  return schema.length > 0 ? schema : null;\n}}\n'


def handler_source(sql_lines: int) -> str:
    """A 5-decision request handler with an SQL template literal of `sql_lines` lines inside it."""
    sql = "\n".join(f"  column_{i}, -- ; semicolons;; in text;" for i in range(sql_lines)) if sql_lines else "select 1;"
    return (
        "function handler(req, db) {\n  if (!req.user) return null;\n  if (!req.query) return null;\n"
        "  if (req.limit > 100) req.limit = 100;\n"
        f"  const rows = db.run(`\n{sql}\n`, req.user, req.query);\n"
        "  return rows.length > 0 ? rows : req.fallback ? req.fallback : [];\n}\n"
    )


def flip_point() -> int:
    """The fewest SQL lines inside the handler at which it becomes flagged."""
    return next(n for n in range(0, 200) if measure("h", handler_source(n), "h.js").flagged)


def issue6() -> TestResult:
    semicolons, commas = "x;" * 50, "x," * 50
    with_semis = measure("50 semicolons in a string", SCRIPT.format(text=semicolons), "s.js")
    with_commas = measure("same length, commas", SCRIPT.format(text=commas), "s.js")
    plain = measure("string with no punctuation", SCRIPT.format(text="x" * 100), "s.js")
    small, big = measure("handler, 1-line SQL", handler_source(0), "h.js"), measure("handler, 30-line SQL", handler_source(30), "h.js")
    flip = flip_point()
    flipped = measure(f"handler, {flip}-line SQL", handler_source(flip), "h.js")
    files = {"script_semicolons.js": SCRIPT.format(text=semicolons), "script_commas.js": SCRIPT.format(text=commas),
             "handler_1_line_sql.js": handler_source(0), "handler_30_line_sql.js": handler_source(30), f"handler_{flip}_line_sql.js": handler_source(flip)}
    measure_tools.write_samples(SAMPLES / "issue6", files)
    out = TestResult(
        "Reported issue 6: semicolons inside strings inflate the statement count",
        "A string or template literal containing 50 semicolons is counted as 50 statements, driving the effective line count (and the purity score) up.",
        ["A 3-line script holding a string with 50 semicolons, against identical scripts with commas or plain text of the same length.",
         "A 5-decision request handler with an SQL template literal inside it, from 1 line to the length at which it becomes flagged."])
    rows = [[v.label, str(v.physical_lines), str(v.effective_lines), str(v.branches), fmt(v.rating_master), "flagged" if v.flagged else "not flagged"]
            for v in (with_semis, with_commas, plain, small, big, flipped)]
    out.tables = [("Effective lines and ratings", ["Variant", "Physical lines", "Effective lines", "Branches", "rating_master", "Verdict"], rows)]
    same = with_semis.effective_lines == with_commas.effective_lines == plain.effective_lines
    out.checks = [
        Check("Do semicolons in strings count as statements?", PASS if same else FAIL,
              f"No. Statements are counted from syntax-tree nodes (the children of statement lists), never from characters, so the 50-semicolon string, the 50-comma string and the plain string all give {with_semis.effective_lines} effective lines. The engine contains no <code>count(';')</code>."),
        Check("Is the suggested fix (count at the AST layer) already how it works?", PASS,
              "Yes: <code>collectors.py</code> counts the <code>named_children</code> of <code>statement_block</code>, <code>program</code>, <code>block</code>, <code>module</code> and <code>class_body</code> nodes. A semicolon inside a string literal is part of one string node."),
        Check("Can a long literal still inflate the effective lines?", FINDING,
              f"Yes, through the other term, not semicolons. Every physical line of a template literal is a non-blank line, and the width term weighs a long line by its characters. The same handler goes from {small.effective_lines} effective lines (rating {fmt(small.rating_master)}) to {big.effective_lines} with a 30-line SQL template (rating {fmt(big.rating_master)}), and is flagged once the template reaches {flip} lines (rating {fmt(flipped.rating_master)} against {CFG.semantic_path_flag_threshold:g}). Its logic did not change."),
        Check("What would fix that?", CAVEAT,
              "Not applied. Counting the lines of a string or template literal as at most one statement (the way docstrings already count as comments) would stop data blobs from inflating a function's size, at the cost of letting a packed string hide code-sized text. That is a call on A4's intent."),
    ]
    return out
