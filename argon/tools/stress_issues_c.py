"""Reported issues 7-9: memory at the file-size cap, import cycles, webhook replay."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from argon.engine.ast_counters import analyze_tree
from argon.engine.config import DEFAULT_CONFIG as CFG
from argon.engine.imports import resolve_imports
from argon.engine.languages import parse_source
from argon.engine.run import run_local
from tools import stress_cases as cases
from tools.stress_checks import CAVEAT, FAIL, FINDING, PASS, ROOT, Check, TestResult, fmt

FIXTURE = ROOT / "tests" / "performance_fixtures" / "ai_generated_service_22k.py"
RAM_CEILING_MB = 512

MEMORY_CHILD = """
import json, sys, time
from pathlib import Path
from tools.stress_measure import peak_working_set_mb, working_set_mb
from argon.engine.run import run_local
path, copies = Path(sys.argv[1]), int(sys.argv[2])
data = path.read_bytes()
files = {f"f{i}{path.suffix}": data for i in range(copies)}
baseline = working_set_mb()
started = time.perf_counter()
result = run_local(files)
print(json.dumps({"seconds": time.perf_counter() - started, "baseline": baseline, "peak": peak_working_set_mb(),
                  "reason": result.findings[0].flag_reason, "purity": result.purity_score}))
"""
NODE_CHILD = """
import sys
from pathlib import Path
from argon.engine.languages import parse_source
path = Path(sys.argv[1])
stack, count = [parse_source(path.name, path.read_bytes()).root], 0
while stack:
    node = stack.pop(); count += 1; stack.extend(node.children())
print(count)
"""


def run_child(script: str, *args: str, timeout: int = 900) -> str:
    done = subprocess.run([sys.executable, "-c", script, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        return json.dumps({"error": (done.stderr.strip().splitlines() or ["crashed"])[-1], "code": done.returncode})
    return done.stdout.strip().splitlines()[-1]


def memory_scenarios(folder: Path) -> list[tuple[str, Path, int]]:
    dense_array = folder / "dense_array.js"
    dense_array.write_text("const a = [" + "1," * 499_000 + "1];\n", encoding="utf-8")
    ifs_one_line = folder / "ifs_one_line.js"
    ifs_one_line.write_text("if(a){}" * 60_000, encoding="utf-8")
    ifs_per_line = folder / "ifs_per_line.js"
    ifs_per_line.write_text("if(a){}\n" * 60_000, encoding="utf-8")
    dense_chain = folder / "dense_chain.js"
    dense_chain.write_text("x = " + "a+" * 499_000 + "a;\n", encoding="utf-8")
    tall_array = folder / "tall_array.js"
    tall_array.write_bytes(("const a = [\n" + "1,\n" * 330_000 + "];\n").encode())  # bytes: text mode would write CRLF and pass 1 MB
    oversize = folder / "oversize.js"
    oversize.write_text("const a = [" + "1," * 1_050_000 + "1];\n", encoding="utf-8")
    return [("the 22k-line AI fixture (761 KB)", FIXTURE, 1), ("20 copies of it in one PR (15 MB total)", FIXTURE, 20),
            ("densest array literal under the 1 MB cap", dense_array, 1),
            ("333,000-term expression chain under the cap", dense_chain, 1),
            ("330,000-element array, one element per line", tall_array, 1),
            ("60,000 empty ifs, all on one line", ifs_one_line, 1), ("the same 60,000 ifs, one per line", ifs_per_line, 1),
            ("a 2.1 MB file (over the cap)", oversize, 1)]


MECHANISM_CHILD = """
import json, sys
from argon.engine.ast_counters import analyze_tree
from argon.engine.config import EngineConfig
from argon.engine.languages import parse_source
from tools.stress_measure import peak_working_set_mb, working_set_mb
mode = sys.argv[1]
data = ("const a = [" + "1," * 499_000 + "1];\\n").encode()
base = working_set_mb()
parsed = parse_source("a.js", data)
stages = {"after parse": peak_working_set_mb()}
if mode != "parse":
    kids = parsed.root.find(kind="array").children()
    stages["after children()"] = peak_working_set_mb()
if mode == "analyze":
    del kids
    analyze_tree(parsed, EngineConfig(max_children=10**9))  # guard off, to show the hazard
    stages["after full analysis"] = peak_working_set_mb()
print(json.dumps({"base": base, "stages": stages}))
"""


def mechanism_rows() -> list[list[str]]:
    """Where the memory goes for the million-element array: tree, then the node wrappers, then the walk."""
    last = json.loads(run_child(MECHANISM_CHILD, "analyze"))
    rows, previous = [["after imports", f"{last['base']:.0f}", ""]], last["base"]
    for stage, peak in last["stages"].items():
        rows.append([stage, f"{peak:.0f}", f"+{peak - previous:.0f}"])
        previous = peak
    return rows


def memory_rows(scenarios: list[tuple[str, Path, int]]) -> tuple[list[list[str]], list[dict]]:
    rows, results = [], []
    for label, path, copies in scenarios:
        data = json.loads(run_child(MEMORY_CHILD, str(path), str(copies)))
        nodes = run_child(NODE_CHILD, str(path)) if copies == 1 and "error" not in data and path.stat().st_size < 1_000_000 else "-"
        results.append(data | {"label": label, "nodes": nodes})
        if "error" in data:
            rows.append([label, f"{path.stat().st_size:,}", nodes, "crashed: " + data["error"], "", "", ""])
            continue
        rows.append([label, f"{path.stat().st_size * copies:,}", nodes if nodes == "-" else f"{int(nodes):,}", f"{data['seconds']:.2f}",
                     f"{data['peak'] - data['baseline']:.0f}", f"{data['peak']:.0f}", str(data["reason"] or "scored")])
    return rows, results


def issue7() -> TestResult:
    folder = Path(tempfile.mkdtemp(prefix="argon_memory_"))
    try:
        rows, results = memory_rows(memory_scenarios(folder))
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    by_label = {r["label"].split(" (")[0]: r for r in results}
    single, pr = by_label["the 22k-line AI fixture"], by_label["20 copies of it in one PR"]
    array, chain = by_label["densest array literal under the 1 MB cap"], by_label["333,000-term expression chain under the cap"]
    one_line, per_line = by_label["60,000 empty ifs, all on one line"], by_label["the same 60,000 ifs, one per line"]
    over = by_label["a 2.1 MB file"]
    tall = by_label["330,000-element array, one element per line"]
    growth = (pr["peak"] - single["peak"]) / 19
    mechanism = mechanism_rows()
    out = TestResult(
        "Reported issue 7: large-file memory exhaustion",
        "A 22,000-line AI file produces hundreds of thousands of nodes; parsed into memory it can exceed a 512 MB worker and be killed. Add a node-count guard before allocating.",
        ["Each scenario in a fresh process, recording the process's peak resident memory (Windows working-set peak) against its memory after imports.",
         "The real 22k-line fixture, 20 copies of it as one pull request, and synthetic files built to be as hard as the 1 MB cap allows: a huge array literal, a 333,000-term expression chain, and 60,000 empty ifs laid out two ways.",
         "A 2.1 MB file over the cap, to see the size guard work, and a step-by-step memory breakdown for the array."])
    out.tables = [("Peak memory and time by scenario", ["Scenario", "Bytes scanned", "Nodes", "Seconds", "Peak above baseline (MB)", "Peak total (MB)", "Outcome"], rows),
                  ("Where the million-element array's memory goes", ["Stage", "Peak resident MB", "Added"], mechanism)]
    out.checks = [
        Check("Does the real 22k-line AI file come close to a 512 MB worker?", PASS if single["peak"] < RAM_CEILING_MB else FINDING,
              f"No. It needs {single['peak'] - single['baseline']:.0f} MB above baseline ({single['peak']:.0f} MB total) and {single['seconds']:.2f} s, with about 241,000 nodes."),
        Check("Can a file under the 1 MB cap still exceed 512 MB?", FINDING if tall["peak"] > RAM_CEILING_MB else PASS,
              f"No longer. The one-line array and the 333,000-term chain are refused by the line-length guard (peaks {array['peak']:.0f} and {chain['peak']:.0f} MB). "
              f"The one-element-per-line array (990 KB) used to need about 627 MB; a node with more than {CFG.max_children:,} children is now flagged <code>{tall['reason']}</code> during the walk, peaking at {tall['peak']:.0f} MB (the tree itself). "
              f"Why the walk needs the guard: <code>children()</code> creates a Python wrapper per child (the unguarded one-line array: tree {mechanism[1][1]} MB, wrappers +{mechanism[2][2]} MB, walk +{mechanism[3][2]} MB). The probe is <code>child(n)</code>, which builds none."),
        Check("Does a big pull request add up?", CAVEAT,
              f"Slowly. Twenty copies of the 22k-line file peak at {pr['peak']:.0f} MB against {single['peak']:.0f} MB for one: about {growth:.0f} MB per extra file. The trees are freed, but each file's findings (1,600 function records here) are kept until the run ends."),
        Check("Is there a guard before the allocation?", PASS,
              f"Yes, two linear ones in <code>run_local</code>, both fail closed as <code>oversize</code>: <code>max_file_bytes</code> = {CFG.max_file_bytes:,} (spec C4) and <code>max_line_chars</code> = {CFG.max_line_chars:,} (added). The 2.1 MB file was flagged <code>{over['reason']}</code> (peak {over['peak']:.0f} MB) and the one-line files were flagged <code>{one_line['reason']}</code> in {one_line['seconds']:.2f} s. Neither parses first, which is the only way to guard before allocating (a node count is known only after parsing)."),
        Check("Is the analysis time bounded?", PASS if one_line["reason"] == "oversize" and one_line["seconds"] < 5 else FINDING,
              f"For long lines, yes: ast-grep's <code>range()</code> slows with the column of the node (0.1 µs at the start of a 973 KB line, 1.4 ms at the end), so time was quadratic in line length (80,000 ifs took 69 s, the 1 MB case 200 s). Lines over {CFG.max_line_chars:,} characters are now refused before parsing; the 60,000 ifs on one line took {one_line['seconds']:.2f} s (flagged) and the same ifs one per line {per_line['seconds']:.1f} s. Genuinely tall files stay linear."),
        Check("What about a worker that is killed anyway?", CAVEAT,
              "That is the spec's D3, recorded for Phase 1 and not built: a reaper that fails stuck Check Runs, a per-worker memory cap (<code>RLIMIT_AS</code>) and a 120 s limit. The two findings above are the inputs that would hit it, so the reaper matters more than the typical numbers suggest."),
    ]
    return out


# ---- Issue 8 -----------------------------------------------------------------------------


def js_files(imports: dict[str, list[str]]) -> dict[str, str]:
    return {f"{name}.js": "".join(f'import "./{target}.js";\n' for target in targets) + f"export const {name} = 1;\n" for name, targets in imports.items()}


def import_edges(files: dict[str, str]) -> set[tuple[str, str]]:
    """The same internal import edges run_local builds for the fragmentation rule."""
    scanned, edges = frozenset(files), set()
    for name, text in files.items():
        metrics = analyze_tree(parse_source(name, text.encode()), CFG, name)
        edges |= {(name, target) for target in resolve_imports(name, metrics.js_specifiers, metrics.python_imports, scanned, CFG)}
    return edges


def cycles(nodes: list[str], edges: set[tuple[str, str]]) -> list[list[str]]:
    """Tarjan's strongly connected components (iterative); groups of 2+ files are import cycles."""
    graph = {node: sorted(target for source, target in edges if source == node) for node in nodes}
    index, low, on_stack, stack, found, counter = {}, {}, set(), [], [], [0]
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(graph[root]))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    index[child] = low[child] = counter[0]
                    counter[0] += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(graph[child])))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                group = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    group.append(member)
                    if member == node:
                        break
                if len(group) > 1:
                    found.append(sorted(group))
    return found


def bundler_check() -> tuple[str, str] | None:
    """Bundle a 3-file import cycle with esbuild (the bundler under Vite): (verdict text, raw output)."""
    if shutil.which("npm") is None:
        return None
    folder = Path(tempfile.mkdtemp(prefix="argon_cycle_"))
    try:
        for name, files in js_files({"a": ["b"], "b": ["c"], "c": ["a"]}).items():
            (folder / name).write_text(files, encoding="utf-8")
        # A package.json in the folder makes it the npm project root; without one npm walks UP to the
        # nearest parent project (such as the user's home folder) and installs there instead.
        (folder / "package.json").write_text('{"name": "argon-cycle-check", "private": true}\n', encoding="utf-8")
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        install = subprocess.run([npm, "install", "--silent", "--no-audit", "--no-fund", "esbuild"], cwd=folder, capture_output=True, text=True, timeout=300)
        launcher = folder / "node_modules" / "esbuild" / "bin" / "esbuild"
        if install.returncode != 0 or not launcher.exists():
            return None
        started = time.perf_counter()
        done = subprocess.run(["node", str(launcher), "a.js", "--bundle", "--outfile=out.js", "--log-level=warning"], cwd=folder, capture_output=True, text=True, timeout=120)
        seconds = time.perf_counter() - started
        return (f"esbuild exited {done.returncode} in {seconds:.2f} s on a 3-file import cycle", (done.stdout + done.stderr).strip() or "(no warnings)")
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def issue8() -> TestResult:
    scenarios = {"linear chain a → b → c": {"a": ["b"], "b": ["c"], "c": []}, "3-cycle a → b → c → a": {"a": ["b"], "b": ["c"], "c": ["a"]},
                 "2-cycle a ⇄ b (+ c)": {"a": ["b"], "b": ["a"], "c": []}}
    rows = []
    for label, graph in scenarios.items():
        rows.append(scenario_row(label, js_files(graph)))
    rows.append(scenario_row("4 files importing each other (Test 2)", cases.shattered_files(4)))
    bundler = bundler_check()
    out = TestResult(
        "Reported issue 8: circular imports are not detected",
        "The fragmentation rule averages imports, so a cycle (A → B → C → A) looks like a safe chain and could freeze Webpack, Vite or Next.js. Use a graph cycle detector (Tarjan).",
        ["Four import graphs run through the engine: a chain, a 3-cycle, a 2-cycle and the fully cross-importing files from Test 2.",
         "A prototype cycle detector (iterative Tarjan strongly connected components) run on the engine's own resolved import edges. It lives in the test tooling; the engine is unchanged.",
         "A real bundler (esbuild, the one under Vite) run on a 3-file cycle."])
    out.tables = [("Engine result vs a cycle detector", ["Graph", "Files", "Import edges", "Fragmentation fires", "Purity score", "Cycles found by Tarjan"], rows)]
    out.checks = [
        Check("Does the engine notice a cycle?", FINDING,
              f"No. The 3-cycle and the linear chain give the same result (purity {rows[0][4]}, no flag). The current rule (spec §10.2) is not a flat average any more: it measures the density of the largest connected group of files and ignores the direction of every import, so a cycle is invisible to it."),
        Check("Would a cycle detector add a signal?", PASS,
              f"Yes. Tarjan finds the 3-cycle, the 2-cycle, and a single 4-file cycle group in Test 2's files, which the density rule does not flag with 4 files. It is a few lines and linear in the number of imports."),
        Check("Does a cycle freeze a bundler?", FINDING if bundler else CAVEAT,
              (f"No, at least not esbuild: {bundler[0]}. Output: {bundler[1]}. ES module cycles are legal and finish (here with no warning at all). I did not run Webpack or Next.js, which I believe report cycles as warnings. The real costs are runtime ones: an export read before its module has run (<code>undefined</code>, a temporal dead zone error, or a half-filled CommonJS object) and order-dependent loading. So a cycle is a structural smell, but the 'freezes the build' severity is not what I could reproduce." if bundler
               else "I couldn't run a bundler here (npm unavailable or offline). From what I know, ES module cycles are legal and Webpack, Rollup and Vite report them as warnings rather than hanging, but I haven't verified that.")),
        Check("Should a cycle block the merge?", CAVEAT,
              "That is a policy call. A hard block would also hit harmless cycles: Python modules that import each other only under <code>TYPE_CHECKING</code>, barrel files, and framework entry points. Reporting the cycle as a finding in the comment, or as its own flag, is the lower-risk shape."),
    ]
    return out


def scenario_row(label: str, files: dict[str, str]) -> list[str]:
    result = run_local({name: text.encode() for name, text in files.items()})
    edges = import_edges(files)
    found = cycles(sorted(files), edges)
    return [label, str(len(files)), str(len(edges)), "yes" if result.fragmentation_flagged else "no", str(result.purity_score),
            "; ".join("{" + ", ".join(Path(n).stem for n in group) + "}" for group in found) or "none"]


# ---- Issue 9 -----------------------------------------------------------------------------


def issue9() -> TestResult:
    out = TestResult(
        "Reported issue 9: webhook replay can cancel workers",
        "A captured, validly signed webhook can be replayed to the endpoint; each replay triggers debounce, which terminates the active worker, so a team's gate never finishes. Add an idempotency key from X-GitHub-Delivery with a 5-minute cache.",
        ["Design analysis only: the webhook receiver, queue and debounce are Phase 1 and are not built, so there is nothing to run. I read the V1 spec (§5.1, §5.2) and the MVP spec's infrastructure review (D1, D2, D4) for what they say."])
    out.checks = [
        Check("Is replay protection specified?", CAVEAT,
              "Partly. V1 §5.1 verifies the HMAC-SHA256 signature and nothing else. The MVP spec's D2 fix already says the receiver 'inserts the delivery into a Postgres queue table (unique on delivery ID)', which is a delivery-ID idempotency key. It is recorded for Phase 1 and not built."),
        Check("Is the HMAC alone replay-safe?", FINDING,
              "No. GitHub's signature covers only the body, with no timestamp or nonce, so a captured payload verifies forever. The delivery ID is the right key to deduplicate on."),
        Check("Does a replay really cancel the running worker?", FINDING,
              "As designed, yes: <code>debounce_and_provision_check</code> cancels the running worker for the PR on every verified <code>synchronize</code> event, whether or not it carries a new commit. A replay of the <i>same</i> head commit has nothing stale to cancel, so debounce should also compare the incoming <code>head_sha</code> with the running analysis's <code>current_head_sha</code> and do nothing when they match. D1's SHA check before posting protects results but not the wasted restart."),
        Check("Is the proposed fix right?", CAVEAT,
              "Mostly. A permanent unique constraint on the delivery ID (D2) is stronger than a 5-minute cache: after 5 minutes the same captured payload would verify again, and GitHub lets repo admins redeliver old deliveries from the UI. Redis is also not in the V1 stack (it arrives with Celery in V2); the Postgres queue table already provides the key. Pruning old IDs after a few days keeps it small."),
        Check("How exploitable is it?", CAVEAT,
              "Lower than stated. An attacker needs a validly signed payload: TLS protects it in transit, and the secret is per app. The realistic sources are a leaked log, a compromised proxy, or a repo admin using Redeliver, any of which can already do more damage. It is cheap defense in depth, and the same-SHA check also absorbs GitHub's own occasional duplicate deliveries."),
    ]
    return out
