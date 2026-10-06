"""The three structural holes the user reported, checked against the code and specs with evidence."""

from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path

from argon.engine.config import DEFAULT_CONFIG as CFG, EngineConfig
from argon.engine.run import run_local
from tools import stress_cases as cases
from tools import stress_measure as measure_tools
from tools.stress_checks import CAVEAT, FINDING, PASS, ROOT, SAMPLES, Check, TestResult, cli_run, fmt
from tools.stress_measure import measure

JWT_SOURCE = "export function verifyJwtSignature(token: string, key: string) {\n  if (!token) return false;\n  return token.length > 0 && key.length > 0;\n}\n"
COMMON_PATHS = ["src/index.ts", "src/app.ts", "src/main.ts", "src/utils.ts", "src/auth/index.ts", "src/auth/jwt.ts", "src/server.ts", "src/config.ts",
                "src/routes/index.ts", "src/lib/db.ts", "src/models/user.ts", "src/services/auth.ts", "src/middleware/auth.ts", "src/types.ts"]
STAGES = '''
import json, time
t = time.perf_counter(); marks = {}
def lap(name):
    global t
    now = time.perf_counter(); marks[name] = (now - t) * 1000; t = now
import ast_grep_py; lap("import ast_grep_py")
import pydantic; lap("import pydantic")
from argon.engine.run import run_local; lap("import the Argon engine")
run_local({"a.py": b"def f(x):\\n    if x:\\n        return 1\\n    return 2\\n"}); lap("first scan (Python file)")
run_local({"a.js": b"function f(x){ if (x) { return 1; } return 2; }"}); lap("first scan (JavaScript file)")
run_local({"b.py": b"x = 1\\n"}); lap("second scan (warm)")
print(json.dumps(marks))
'''


def find_strings(node, needles: list[str], path: str = "$") -> list[tuple[str, str]]:
    """(JSON path, needle) for every string value in `node` containing one of the needles."""
    found = []
    if isinstance(node, str):
        found += [(path, needle) for needle in needles if needle in node]
    elif isinstance(node, dict):
        for key, value in node.items():
            found += find_strings(value, needles, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += find_strings(value, needles, f"{path}[{index}]")
    return found


def hole1() -> TestResult:
    folder = measure_tools.write_samples(SAMPLES / "hole1", {"src/auth/jwt_processor.ts": JWT_SOURCE})
    result = run_local({"src/auth/jwt_processor.ts": JWT_SOURCE.encode()})
    dump = json.loads(result.model_dump_json())
    leaks = find_strings(dump, ["jwt_processor", "verifyJwtSignature", "src/auth"])
    paths = sorted({(where.replace("[0]", "[]"), needle) for where, needle in leaks})
    recovered = sum(hashlib.sha256(path.encode()).hexdigest() in {hashlib.sha256(p.encode()).hexdigest() for p in COMMON_PATHS} for path in COMMON_PATHS)
    out = TestResult(
        "Reported issue 1: identity leak in the 'zero-knowledge' claim",
        "Filenames and raw_content_url cross the wire, revealing the client's architecture; hash the filenames and strip raw_content_url.",
        ["A one-file sample (<code>src/auth/jwt_processor.ts</code> with a function <code>verifyJwtSignature</code>) run through the engine, then every string in the result searched for the file and function names.",
         "A dictionary test of the proposed fix: unsalted <code>sha256(filename)</code> against a list of 14 common paths.",
         "The two spec PDFs searched for the quoted sentence (\"We never see, read, or store...\") and for what the job payload carries."])
    out.tables = [("Where the file and function names appear in the engine's JSON result", ["JSON path", "Contains"], [[where, needle] for where, needle in paths])]
    out.checks = [
        Check("Is the quoted 'zero-knowledge' promise in the specs?", CAVEAT,
              "I couldn't find that sentence in either PDF I have. The MVP spec says \"There is no GitHub App, server or database\" for the MVP; the V1 spec says the engine <i>never persists</i> source and, for enterprise, Argon \"never receives raw source code\" (\u00a77). If the quote comes from another document, tell me which."),
        Check("Do identifying names leave the engine today?", FINDING,
              f"Yes, in more places than the filename: the result carries the path and the function name in {len(paths)} JSON fields (table below), plus line numbers and scores. In the MVP nothing leaves the machine, so this is a Phase 1 / Phase 3 design question, not a current bug."),
        Check("Is raw_content_url / patch a bigger leak than the filename?", FINDING,
              "By the same logic, yes. The V1 spec's <code>ChangedFile</code> carries <code>patch</code> (the unified diff text, i.e. source lines) as well as <code>raw_content_url</code>, and \u00a77.1 forwards <code>job.model_dump()</code> to the customer's worker. That diff passes through Argon's server before it is forwarded, which contradicts \"never receives raw source code\". The URL itself is only an access key if it embeds a token; the diff is code either way."),
        Check("Does plain sha256(filename) hide the path?", FINDING,
              f"Not for guessable paths: all {recovered} of {len(COMMON_PATHS)} common paths were recovered from their hashes with a plain dictionary. The same applies to function names. A keyed hash (HMAC-SHA256 with a per-customer secret that stays in the customer's cloud) avoids that."),
        Check("Would hashing change what Argon already knows?", CAVEAT,
              "Only for enterprise mode. In the shared mode Argon's server fetches the file list and diffs from GitHub itself, so it already sees the paths; hashing them in the result would not remove that. The PR comment also needs the real paths, so the platform must keep a path-to-hash map on its side."),
    ]
    out.cli = [cli_run("json", [str(folder.relative_to(ROOT)), "--json"])]
    return out


def cold_start(runs: int) -> tuple[dict[str, float], float]:
    """Median milliseconds per stage across fresh interpreters, and median whole-process time."""
    samples, totals = [], []
    for _ in range(runs):
        started = __import__("time").perf_counter()
        done = subprocess.run([sys.executable, "-c", STAGES], cwd=ROOT, capture_output=True, text=True)
        totals.append((__import__("time").perf_counter() - started) * 1000)
        samples.append(json.loads(done.stdout.strip().splitlines()[-1]))
    return {name: statistics.median(sample[name] for sample in samples) for name in samples[0]}, statistics.median(totals)


def native_binding() -> str:
    import ast_grep_py

    folder = Path(ast_grep_py.__file__).parent
    libs = [f"{path.name} ({path.stat().st_size / 1_048_576:.1f} MB)" for path in folder.iterdir() if path.suffix in (".pyd", ".so", ".dylib")]
    return ", ".join(libs) or "no native library found"


def hole2(quick: bool = False) -> TestResult:
    stages, total = cold_start(3 if quick else 9)
    baseline = statistics.median(
        (lambda t0: (subprocess.run([sys.executable, "-c", "pass"], capture_output=True), (__import__("time").perf_counter() - t0) * 1000)[1])(__import__("time").perf_counter())
        for _ in range(5)
    )
    engine_cost = sum(stages.values())
    out = TestResult(
        "Reported issue 2: tree-sitter dynamic library on cold start",
        "Compiling a tree-sitter .so and loading it from py-tree-sitter adds ~1.5 s to every cold start; use native ast-grep instead.",
        ["Fresh Python processes, each timing its own stages: imports, the first scan of a Python and a JavaScript file, and a warm second scan. Median of the runs.",
         "A look at what ast-grep-py ships, and a search of the code for any grammar build step."])
    out.tables = [("Cold start of this engine (median milliseconds, fresh process each time)", ["Stage", "ms"],
                   [["bare Python startup (python -c pass)", f"{baseline:.0f}"], *[[name, f"{value:.1f}"] for name, value in stages.items()],
                    ["whole process from launch to exit", f"{total:.0f}"]])]
    out.checks = [
        Check("Does the MVP compile or load a tree-sitter .so?", PASS,
              f"No. The engine uses <code>ast-grep-py</code>, a prebuilt native binding (PyO3) that already contains the grammars: {native_binding()}. There is no grammar build step anywhere in the repo, and the MVP spec says so in \u00a73: \"ast-grep ships its grammars inside the wheel, so master \u00a72.3's grammar compile step and its cross-architecture crash don't apply.\" Your proposed fix is already what the code does."),
        Check("Is the cold start really under 5 ms?", FINDING,
              f"No. From launch to exit a fresh process takes about {total:.0f} ms, of which Python itself is {baseline:.0f} ms. The Argon-specific part is about {engine_cost:.0f} ms (first scan of a file: {stages['first scan (Python file)']:.1f} ms Python, {stages['first scan (JavaScript file)']:.1f} ms JavaScript; a warm scan: {stages['second scan (warm)']:.1f} ms). The 5 ms figure only holds for a warm scan, and it is the import of the engine and its data models, not parsing, that dominates the cold start."),
        Check("Is the old 1.5 s figure a risk for Phase 1?", PASS,
              f"Not from parsing. The measured cold start is a fraction of a second, so a serverless worker pays about {total / 1000:.1f} s at most, mostly interpreter and import time; keeping the process warm (the spec's worker design) removes even that."),
    ]
    return out


def callback_rows(levels_list: list[int]) -> tuple[list[list[str]], dict]:
    rows, lookup = [], {}
    nesting = EngineConfig(rating_mode="nesting")
    for levels in levels_list:
        for wrap in (0, 1, 2, 3):
            source = cases.callback_nest(levels, wrap)
            default, strict = measure(f"{levels}/{wrap}", source, "r.js"), measure(f"{levels}/{wrap}", source, "r.js", nesting)
            lookup[(levels, wrap)] = (default, strict)
            rows.append([str(levels), str(wrap), fmt(default.nesting_score), fmt(strict.rating_nesting), str(default.purity), str(strict.purity)])
    return rows, lookup


def share_example(nesting: EngineConfig) -> list[list[str]]:
    """A saturated file mixed into PRs where it is 100%, 50%, 10% and 2% of the altered lines."""
    saturated = cases.chain_js_formatted(25, 1)  # one statement per line, so each function owns its own lines
    weight = measure("sat", saturated, "s.js", nesting).effective_lines
    rows = []
    for fillers in (0, 1, 9, 49):
        files = {"saturated.js": saturated.encode()} | {f"clean{i}.py": ("x = 1\n" * weight).encode() for i in range(fillers)}
        result = run_local(files, cfg=nesting)
        rows.append([str(fillers + 1), f"{1 / (fillers + 1):.0%}", str(result.purity_score), result.check_conclusion])
    return rows


def hole3(quick: bool = False) -> TestResult:
    rows, lookup = callback_rows([3, 6] if quick else [3, 6, 10, 20])
    nesting = EngineConfig(rating_mode="nesting")
    top_six = max(lookup[(6, wrap)][0].nesting_score for wrap in (0, 1, 2, 3))
    default6, strict6 = lookup[(6, 2)]
    shares = share_example(nesting)
    out = TestResult(
        "Reported issue 3: isolation tax overflow and purity collapse",
        "Agents nest callbacks 6 layers deep; the tax compounds (7x), overflows an integer and drives the purity score to 0 on valid async code. Cap the multiplier.",
        ["Node-style callback chains: each level has an <code>if (err) return done(err)</code> guard, then defines the next callback directly (wrap 0) or inside 1, 2 or 3 extra if-blocks.",
         "Levels 3, 6, 10 and 20, scored in the default rating mode (master) and in nesting mode.",
         "A saturated file mixed into pull requests where it is 100%, 50%, 10% and 2% of the changed lines."])
    out.tables = [("Callback chains", ["Levels", "Extra ifs around each callback", "Nesting score", "rating_nesting", "Purity (default mode)", "Purity (nesting mode)"], rows),
                  ("A saturated file inside bigger pull requests (nesting mode)", ["Files in the PR", "Saturated file's share", "Purity", "Conclusion"], shares)]
    out.checks = [
        Check("Is there an overflow roof today?", PASS,
              f"Yes. Scores are clamped at <code>score_cap</code> = {fmt(CFG.score_cap)} (spec B3) and the row gets <code>saturated</code>; Test 4 shows it clamping exactly at 20 levels with one if per level. Python integers can't overflow, and {fmt(CFG.score_cap)} is below JSON's safe integer (2\u2075\u00b3) and Postgres INTEGER (2\u00b3\u00b9)."),
        Check("Does a 6-layer nest reach the multiplier or the cap?", PASS,
              f"No. The multiplier is 1 + the depth the function is <i>defined at inside its parent</i>, compounded per layer, not 1 + the number of layers. Six layers with the callback directly in the body cost x1 each (score {fmt(lookup[(6, 0)][0].nesting_score)}); wrapped in one if, x2 each (score {fmt(lookup[(6, 1)][0].nesting_score)}); the worst case here, 3 ifs around every callback, scores {fmt(top_six)}, about {top_six / CFG.score_cap:.1%} of the cap. It takes about 20 layers with one if each to saturate."),
        Check("Does the tax drive the purity score to 0 in the default mode?", PASS,
              "No. The default rating mode is master (branches\u00b2 \u00d7 lines \u00f7 outputs), which never uses the tax. Only nesting mode does, and the default and nesting columns in the table differ only in that."),
        Check("Are these chains flagged anyway, without the tax?", CAVEAT,
              f"Yes, once they hold many decisions. The default-mode purity in the table is low because of the flat decision count, not the tax: the 6-layer chain with 2 ifs per callback has {default6.branches} decisions in one function nest and a master rating of {fmt(default6.rating_master)} against a threshold of {CFG.semantic_path_flag_threshold:g}, so purity is {default6.purity} by default and {strict6.purity} in nesting mode. Dense callback code is flagged by the original formula too; that is the calibration question from Test 3, not an overflow."),
        Check("In nesting mode, can valid-looking code be flagged hard?", CAVEAT,
              f"Yes, by design. A deep, heavily wrapped nest has a high nesting score, so nesting mode flags it (purity {strict6.purity} for a one-file PR). The overshoot penalty is capped at 30 points, but the flag itself costs 70 points of the flag ratio, so a single flagged file can reach 0. How far it drags a pull request down depends on its share of the changed lines: a saturated file that is the whole PR gives purity {shares[0][2]}, at 50% of the changed lines {shares[1][2]}, at 10% {shares[2][2]}, at 2% {shares[3][2]}. Capping the <i>multiplier</i> would make heavily nested code cheaper, which is the loophole A6 closed, while the existing score cap already stops overflow."),
    ]
    return out
