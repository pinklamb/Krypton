"""Run the stress tests and the reported-issue checks and write argon/docs/stress_test_findings.html.

From the project root (the folder holding pyproject.toml):

    python -m tools.stress_report            # full run (a few minutes)
    python -m tools.stress_report --quick    # smaller scaling, leak and thread runs
"""

from __future__ import annotations

import argparse
import html
import pickle
import sys
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from argon.engine.config import DEFAULT_CONFIG as CFG
from tools import docs_html as h
from tools.stress_checks import CAVEAT, FAIL, FINDING, PASS, ROOT, Check, TestResult, test1, test2
from tools.stress_holes import hole1, hole2, hole3
from tools.stress_issues_b import issue4, issue5, issue6
from tools.stress_issues_c import issue7, issue8, issue9
from tools.stress_issues_d import issue10, issue11, issue12, issue13
from tools.stress_issues_e import issue14, issue15, issue16, issue17
from tools.stress_scope import test4
from tools.stress_switch import test3

esc = html.escape
LABELS = {PASS: "PASS", CAVEAT: "PASS, WITH A CAVEAT", FINDING: "FINDING", FAIL: "FAIL"}
EXTRA_CSS = """
.pill{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px;font-weight:600;border:1px solid var(--line);white-space:nowrap}
.pill.pass{color:var(--ok)}.pill.caveat,.pill.finding{color:var(--warn)}.pill.fail{color:var(--bad)}
.check{background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--line);border-radius:8px;padding:8px 14px;margin:10px 0}
.check.pass{border-left-color:var(--ok)}.check.caveat,.check.finding{border-left-color:var(--warn)}.check.fail{border-left-color:var(--bad)}
.check h4{margin:0 0 4px}.cmd{color:var(--mute);margin:14px 0 2px}.out{max-height:520px}
.notice{background:var(--panel);border:1px solid var(--line);border-left:6px solid var(--bad);border-radius:8px;padding:10px 16px;margin:16px 0}
main{max-width:1000px;margin:0 auto}.layout{display:block}
"""

NOTICE = """
<div class="notice"><h3>Read this first: one change I made outside the project, by accident</h3>
<p>The cycle check (issue 8) ran <code>npm install esbuild</code> in a temporary folder that had no <code>package.json</code>.
npm then looked upward and used <code>C:\\Users\\Me\\package.json</code>, your home folder's npm project, instead. Result:</p>
<ul><li><code>"esbuild": "^0.28.2"</code> is now listed under <code>dependencies</code> in <code>C:\\Users\\Me\\package.json</code>.</li>
<li><code>package-lock.json</code> was updated, and <code>node_modules\\esbuild</code> and <code>node_modules\\@esbuild\\win32-x64</code> (0.28.2) were installed there.</li>
<li>Nothing else changed: only 4 of the 891 entries in that <code>node_modules</code> were touched, and no other package depends on esbuild. The npm log confirms a single <code>npm install esbuild</code>.</li></ul>
<p><b>I did not undo it</b>, because an older npm log (Nov 2025) shows esbuild 0.25.12 in some project's tree, so I can't rule out that esbuild was already one of your dependencies and I only bumped it. If it was not yours, remove it with
<code>cd C:\\Users\\Me</code> then <code>npm uninstall esbuild</code>. If it was, <code>npm install esbuild@0.25.12</code> puts back the version I saw.
The check now creates its own <code>package.json</code> in the temp folder first, and I re-ran it to confirm your home files are no longer touched.</p></div>
"""

START_HERE = [
    ("test1", "Packing code onto one line", "can drop a flagged file just under the threshold (the escape band is quantified there); per-function line attribution on shared lines is fixed."),
    ("test2", "File shattering", "4 fully cross-importing files do <i>not</i> trip the fragmentation rule; it first fires at 6."),
    ("test3", "Switch-case game", "distinct outputs dilute nested logic; nesting mode can be fooled by hiding nests in a small switch."),
    ("test11", "Reported issue 7: memory and time", "fixed for long lines (refused before parsing, linear time); a tall 1 MB array can still need several hundred MB."),
    ("test15", "Reported issue 11: chained listeners", "one fluent <code>.on().on()</code> pipeline counts as several boundaries and can trip the concentration rule."),
    ("test17", "Reported issue 13: the diff parser", "fixed: the diff parser now reads hunk counts, so <code>++ </code> lines and files with spaces or non-ASCII names are handled."),
    ("test12", "Reported issue 8: import cycles", "there is no cycle detection; a prototype shows it would catch Test 2's scenario."),
]


def pill(verdict: str) -> str:
    return f'<span class="pill {verdict}">{LABELS[verdict]}</span>'


def check_html(check: Check) -> str:
    return f'<div class="check {check.verdict}"><h4>{esc(check.question)} {pill(check.verdict)}</h4><div>{check.detail}</div></div>'


def cli_html(command: str, output: str, code: int) -> str:
    return f'<div class="cmd"><code>{esc(command)}</code> (exit code {code})</div><pre class="out"><code>{esc(output)}</code></pre>'


def test_section(index: int, result: TestResult) -> str:
    tables = "".join(f"<h4>{esc(caption)}</h4>" + h.table(headers, rows) for caption, headers, rows in result.tables)
    outputs = "".join(cli_html(*entry) for entry in result.cli)
    body = (
        f"<p><b>The claim or challenge:</b> {esc(result.ask)}</p><h4>What I ran</h4>{h.bullets(result.setup)}"
        f"<h4>Verdicts</h4>{''.join(check_html(check) for check in result.checks)}"
        + (f"<h4>Numbers</h4>{tables}" if tables else "")
        + (f"<h4>CLI output</h4>{outputs}" if outputs else "")
    )
    return h.section(f"test{index}", f"{result.title}  \u2014  {LABELS[result.verdict]}", body)


def summary_table(results: list[TestResult], first_index: int) -> str:
    rows = []
    for offset, result in enumerate(results):
        notes = "; ".join(check.question for check in result.checks if check.verdict in (FINDING, FAIL, CAVEAT)) or "all checks passed"
        rows.append([f'<a href="#test{first_index + offset}">{esc(result.title)}</a>', pill(result.verdict), esc(notes)])
    return h.table(["", "Verdict", "Needs your attention"], rows, raw=True)


def start_here() -> str:
    items = [f'<a href="#{anchor}"><b>{esc(title)}</b></a>: {text}' for anchor, title, text in START_HERE]
    return h.bullets(items)


def findings_list(results: list[TestResult]) -> str:
    items = [f"<b>{esc(result.title)}</b>: {esc(check.question)} {check.detail}" for result in results for check in result.checks if check.verdict in (FINDING, FAIL)]
    return h.bullets(items) if items else "<p>No findings.</p>"


def run_details(quick: bool) -> str:
    return h.table(["", ""], [
        ["generated", datetime.now().strftime("%Y-%m-%d %H:%M")],
        ["thresholds", f"rating_master {CFG.semantic_path_flag_threshold:g}, rating_nesting {CFG.nesting_flag_threshold:g}, active mode {CFG.rating_mode}"],
        ["effective-line width", f"{CFG.effective_line_width} characters"], ["score cap", f"{CFG.score_cap:,}"],
        ["versions", f"python {sys.version.split()[0]}, ast-grep-py {version('ast-grep-py')}, radon {version('radon')}"],
        ["run size", "quick (smaller scaling, leak and thread runs)" if quick else "full"],
        ["re-run", "python -m tools.stress_report   (from Krypton/argon)"],
        ["sample files", "argon/stress/samples/  (inputs for the tests; run the CLI on them yourself)"],
    ])


def page(tests: list[TestResult], issues: list[TestResult], quick: bool) -> str:
    intro = (
        f"<p>Part A: the four stress tests you specified. Part B: the {len(issues)} structural problems you reported, each checked against the actual code and specs. "
        "For every one I ran what could be run and reasoned about the rest, and where a claim doesn't match what I found I say so. "
        "Each section lists what I ran, a verdict per question, the numbers, and real CLI output where it applies. "
        "<b>Three fixes were applied after the first run</b> (line attribution for functions sharing a line, the <code>--base</code> diff parser, and a line-length guard); those sections say so and show the re-measured numbers. Scoring rules and thresholds are unchanged; every other finding is for you to decide on.</p>"
        '<p><span class="pill pass">PASS</span> behaves as intended / the claim does not hold. <span class="pill caveat">PASS, WITH A CAVEAT</span> works with a limit, or a judgment call. '
        '<span class="pill finding">FINDING</span> the expectation was not met, or the claim is valid. <span class="pill fail">FAIL</span> a bug in my test or in the engine.</p>'
    )
    everything = tests + issues
    summary = (
        intro + "<h3>Start here</h3>" + start_here() + "<h3>Part A: stress tests</h3>" + summary_table(tests, 1)
        + "<h3>Part B: reported issues</h3>" + summary_table(issues, len(tests) + 1)
        + "<h3>Findings in detail</h3>" + findings_list(everything) + "<h3>Run details</h3>" + run_details(quick)
    )
    body = NOTICE + h.section("summary", "Summary", summary) + "".join(test_section(i, r) for i, r in enumerate(everything, 1))
    nav = " ".join(f'<a href="#test{i}">{esc(r.title.split(":")[0])}</a>' for i, r in enumerate(everything, 1))
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Krypton stress tests</title><style>{h.CSS}{EXTRA_CSS}</style></head><body><main>"
        f'<h1>Krypton stress tests and reported issues</h1><p><a href="#summary">Summary</a> {nav}</p>{body}</main></body></html>'
    )


def registry(quick: bool) -> dict[str, tuple[str, object]]:
    """Every check by key (test1..test4, issue1..issue17), with the group it belongs to."""
    tests = {"test1": test1, "test2": test2, "test3": test3, "test4": lambda: test4(quick)}
    issues = [hole1, lambda: hole2(quick), lambda: hole3(quick), issue4, issue5, issue6, issue7, issue8, issue9, issue10, issue11, issue12, issue13,
              issue14, issue15, issue16, lambda: issue17(quick)]
    return {key: ("tests", run) for key, run in tests.items()} | {f"issue{n}": ("issues", run) for n, run in enumerate(issues, 1)}


def run_all(quick: bool, only: set[str] | None, cache: Path) -> tuple[list[TestResult], list[TestResult]]:
    """Run the checks named in `only` (all if None); the rest come from the cache of the last run."""
    saved = pickle.loads(cache.read_bytes()) if only and cache.exists() else {}
    results, groups = {}, {"tests": [], "issues": []}
    for key, (group, run) in registry(quick).items():
        results[key] = run() if only is None or key in only or key not in saved else saved[key]
        groups[group].append(results[key])
        print(f"  ran {key}" if only is None or key in only or key not in saved else f"  cached {key}", file=sys.stderr, flush=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(results))
    return groups["tests"], groups["issues"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Krypton's math stress tests and write the findings page.")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "stress_test_findings.html")
    parser.add_argument("--quick", action="store_true", help="smaller scaling, leak and thread runs")
    parser.add_argument("--only", metavar="KEYS", help="re-run only these (comma-separated: test1..test4, issue1..issue17); the rest are reused from the last run")
    args = parser.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    tests, issues = run_all(args.quick, only, args.out.parent / ".stress_cache.pkl")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page(tests, issues, args.quick), encoding="utf-8")
    for result in tests + issues:
        print(f"{LABELS[result.verdict]:22} {result.title}")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
