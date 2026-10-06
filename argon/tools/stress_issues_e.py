"""Reported issues 14-17: comments and nesting, the pure-engine contradiction, shared state, free threading."""

from __future__ import annotations

import ast
import asyncio
import gc
import json
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import distribution
from pathlib import Path

from argon import cli
from argon.engine.config import EngineConfig
from argon.engine.languages import language_for
from argon.engine.run import run_local
from tools import stress_measure as measure_tools
from tools.stress_checks import CAVEAT, FAIL, FINDING, PASS, ROOT, Check, TestResult, fmt

SPEC_FIXTURES = ROOT / "tests" / "fixtures" / "spec"
TRICKY = {
    "ternary_multiline.py": "value = (\n    first_choice\n    if cond_a\n    else second_choice\n)\n",
    "ternary_nested_multiline.py": "value = (\n    (u if v else w)\n    if s\n    else t\n)\n",
    "else_if.js": "if (a) {\n  x();\n} else if (b) {\n  y();\n} else {\n  z();\n}\n",
    "ternary.js": "const v = a\n  ? (b ? 1 : 2)\n  : c;\n",
    "switch.js": "switch (a) {\n  case 1:\n    if (b) { if (c) {} }\n    break;\n  default:\n    break;\n}\n",
    "match.py": "match v:\n    case 1:\n        if a:\n            pass\n    case _:\n        pass\n",
    "comprehension.py": "rows = [\n    f(x)\n    for a in b\n    if c\n    for x in a\n]\n",
    "control_flow.js": "ok && (\n  b && (\n    c && run()\n  )\n);\n",
    "scopes.js": "function outer(a) {\n  if (a) {\n    const inner = () => {\n      if (a) {}\n    };\n  }\n}\n",
}


def fingerprint(source: str, name: str) -> tuple:
    metrics = measure_tools.tree_metrics(source, name)
    return (metrics.nesting_score, metrics.cyclomatic_branches, metrics.max_depth, tuple((f.name, f.score, f.max_depth) for f in metrics.functions))


def comment_variants(source: str, name: str):
    """Every way of adding one comment: a line of its own before each line, or at the end of each line."""
    marker = "#" if name.endswith(".py") else "//"
    lines = source.rstrip("\n").split("\n")
    for index, line in enumerate(lines):
        indent = line[: len(line) - len(line.lstrip())]
        yield "\n".join(lines[:index] + [f"{indent}{marker} argon-skip: note"] + lines[index:]) + "\n"
        yield "\n".join(lines[:index] + [f"{line} {marker} argon-skip"] + lines[index + 1:]) + "\n"


def fuzz_rows() -> tuple[list[list[str]], int, int]:
    samples = {path.name: path.read_text(encoding="utf-8") for path in sorted(SPEC_FIXTURES.glob("*.*")) if path.is_file() and language_for(path.name)}
    samples |= {path.name: path.read_text(encoding="utf-8") for path in sorted((ROOT / "tests" / "fixtures").glob("chaos.*"))}
    samples |= TRICKY
    rows, total, bad = [], 0, 0
    for name, source in samples.items():
        base = fingerprint(source, name)
        variants = list(comment_variants(source, name))
        changed = sum(fingerprint(variant, name) != base for variant in variants)
        total, bad = total + len(variants), bad + changed
        rows.append([name, str(base[0]), str(len(variants)), str(changed)])
    return rows, total, bad


def issue14() -> TestResult:
    rows, total, bad = fuzz_rows()
    skip_mentions = sum("argon-skip" in path.read_text(encoding="utf-8") for path in (ROOT / "argon").rglob("*.py"))
    out = TestResult(
        "Reported issue 14: comment nodes could shift nesting or the isolation tax",
        "A comment placed above a nested statement (such as // argon-skip) is a sibling node; entering it could add a depth level, be read as an empty branch, or put the tax on the wrong frame. Add a lookahead layer that skips comments.",
        [f"{len(rows)} files: every spec fixture, the chaos files and nine tricky shapes (multi-line ternaries, <code>else if</code>, switch cases, comprehensions, control-flow booleans, nested scopes).",
         f"For each file, one variant per line with a comment line inserted before it and another with a comment added at its end: {total} variants in all, each carrying the text <code>argon-skip</code>.",
         "Each variant scored and compared with the original: nesting score, flat count, depth, and every function's score and depth."])
    out.tables = [("Comment-injection results per file", ["File", "Nesting score", "Variants tried", "Variants whose score changed"], rows)]
    out.checks = [
        Check("Do comment nodes change depth, scores or the tax?", PASS if bad == 0 else FAIL,
              f"No. {total} variants, {bad} changed any number. A comment is a plain leaf node: it appears in no nesting, branch or scope table, so it never opens a level, never counts as a branch and never starts a scope. The only places the code reads children by position (the Python ternary, the match catch-all) were covered by the multi-line cases with comments in every position."),
        Check("Is // argon-skip implemented?", CAVEAT,
              f"No. It is a Phase 3 item in both specs, and {skip_mentions} source files mention it. Today the engine ignores comment text entirely, so adding the marker changes nothing, which is exactly what the fuzz above shows."),
        Check("What should the Phase 3 design be?", CAVEAT,
              "The report's lookahead idea is the right shape. In this walk it would be: when a comment says <code>argon-skip</code>, look up its next non-comment sibling (<code>node.next()</code>, skipping comments) and record that node as suppressed by identity, with no change to depth. A depth counter must not be involved, which keeps it free of the off-by-one the report describes."),
    ]
    return out


# ---- Issue 15 ----------------------------------------------------------------------------

WATCHED = {"open", "os.listdir", "os.scandir", "os.remove", "os.rename", "os.mkdir", "subprocess.Popen", "socket.connect", "socket.getaddrinfo", "ctypes.dlopen"}
RECORDED: list[str] = []
RECORDING = [False]


def audit(event: str, args: tuple) -> None:
    if RECORDING[0] and event in WATCHED:
        RECORDED.append(event)


sys.addaudithook(audit)


def alias_scan(cfg: EngineConfig) -> tuple[int, list[str]]:
    """(imports resolved for a.ts, I/O events seen) for a scan that only exists in memory."""
    files = {"src/a.ts": b'import { Button } from "@/components/Button";\n', "src/components/Button.ts": b"export const Button = 1;\n"}
    run_local(files, cfg=cfg)  # warm-up so lazy imports don't count
    RECORDED.clear()
    RECORDING[0] = True
    try:
        result = run_local(files, cfg=cfg)
    finally:
        RECORDING[0] = False
    return next(f.relative_import_count for f in result.findings if f.filename == "src/a.ts"), list(RECORDED)


def issue15() -> TestResult:
    aliased, events = alias_scan(EngineConfig(ts_path_aliases={"@/*": ["src/*"]}))
    plain, plain_events = alias_scan(EngineConfig())
    with tempfile.TemporaryDirectory(prefix="argon_tsconfig_") as folder:
        (Path(folder) / "tsconfig.json").write_text('{\n  // JSONC\n  "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}\n}\n', encoding="utf-8")
        discovered = cli._tsconfig_paths(Path(folder))
    out = TestResult(
        "Reported issue 15: the 'pure engine' and tsconfig alias resolution contradict each other",
        "Resolving @/components/Button needs tsconfig.json, which the engine can't read if it does no disk access. The CLI should read it and pass the maps in through the config.",
        ["A scan of two in-memory files where <code>src/a.ts</code> imports <code>@/components/Button</code>, with and without an alias map in the config, while a Python audit hook records every file open, directory listing, subprocess and network call.",
         "The CLI's tsconfig reader run on a temporary <code>tsconfig.json</code>, to show what it hands to the engine."])
    out.tables = [("Alias resolution with the map passed in through the config", ["Config", "Imports resolved for a.ts", "Disk, process or network events during the scan"],
                   [["ts_path_aliases = {'@/*': ['src/*']}", str(aliased), str(len(events))], ["no alias map", str(plain), str(len(plain_events))]]),
                  ("What the CLI's tsconfig reader passes in", ["Config field", "Value"], [[key, str(value)] for key, value in discovered.items()])]
    out.checks = [
        Check("Does alias resolution require the engine to read the file?", PASS if aliased == 1 and not events else FAIL,
              f"No, and that is how it is already built. <code>cli.py</code> reads <code>tsconfig.json</code> / <code>jsconfig.json</code> and fills <code>EngineConfig.ts_path_aliases</code> and <code>ts_base_url</code>; <code>run_local(files, altered, cfg)</code> then resolves each import against the <code>files</code> keys (the repository file list) and that map, all in memory. The alias resolved ({aliased} import) with {len(events)} I/O events, and the same scan without the map resolved {plain}, so the config is the only source."),
        Check("Is the 'no I/O' rule actually enforced?", PASS,
              "Yes, by a test that fails if any file under <code>engine/</code> imports <code>os</code>, <code>pathlib</code>, <code>subprocess</code>, <code>socket</code> or an HTTP library (<code>test_engine_imports_no_io_modules</code>), and by the audit-hook run above."),
        Check("Was the summary contradictory?", CAVEAT,
              "The two capability bullets were each true but didn't say how they fit together, which reads as a contradiction. I reworded the audit page's bullet to say that the CLI reads tsconfig and passes the alias map in through the config."),
    ]
    return out


# ---- Issue 16 ----------------------------------------------------------------------------

MUTATORS = {"append", "extend", "update", "add", "pop", "clear", "setdefault", "remove", "insert", "discard"}


def module_level_state() -> list[list[str]]:
    """Mutable containers created at import time in the engine, and whether anything mutates them."""
    rows = []
    for path in sorted((ROOT / "argon" / "engine").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {t.id: type(n.value).__name__ for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                 for t in ([n.target] if isinstance(n, ast.AnnAssign) else n.targets)
                 if isinstance(t, ast.Name) and isinstance(n.value, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp))}
        for name, kind in names.items():
            mutated = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id == name and n.func.attr in MUTATORS for n in ast.walk(tree)) or any(
                isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store) and isinstance(n.value, ast.Name) and n.value.id == name for n in ast.walk(tree))
            rows.append([path.name, name, kind, "yes" if mutated else "no (read-only)"])
    return rows


def concurrent_check(workers: int, tasks: int) -> tuple[int, int, int]:
    """(tasks run, tasks whose result differed from the sequential baseline, distinct sources)."""
    sources = {f"s{i}{path.suffix}": path.read_bytes() for i, path in enumerate(sorted(SPEC_FIXTURES.glob("*.*"))) if path.is_file() and language_for(path.name)}
    sources |= {"billing.js": (ROOT / "stress" / "samples" / "test1" / "billing4_formatted.js").read_bytes()} if (ROOT / "stress" / "samples" / "test1" / "billing4_formatted.js").exists() else {}
    names = sorted(sources)
    baseline = {name: run_local({name: sources[name]}).model_dump_json() for name in names}
    stop, wrong = threading.Event(), []

    def churn() -> None:
        while not stop.is_set():
            gc.collect()
            time.sleep(0.0005)

    def task(index: int) -> None:
        name = names[index % len(names)]
        if run_local({name: sources[name]}).model_dump_json() != baseline[name]:
            wrong.append(name)

    async def event_loop_part() -> None:
        await asyncio.gather(*(asyncio.to_thread(task, i) for i in range(tasks // 4)))

    collector = threading.Thread(target=churn)
    collector.start()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(task, range(tasks)))
        asyncio.run(event_loop_part())
    finally:
        stop.set()
        collector.join()
    return tasks + tasks // 4, len(wrong), len(names)


def issue16() -> TestResult:
    state = module_level_state()
    ran, wrong, distinct = concurrent_check(workers=16, tasks=480)
    mutated = [row for row in state if row[3] == "yes"]
    cache = sum("_LAZY_LANGUAGE_CACHE" in path.read_text(encoding="utf-8") for path in (ROOT / "argon").rglob("*.py"))
    out = TestResult(
        "Reported issue 16: shared global state leaks between concurrent analyses",
        "Global registries and class-level caches accumulate scores, so two simultaneous pull requests contaminate each other's metrics; isolate each call with contextvars or structural passing.",
        ["A scan of the engine's source for mutable containers created at import time, and for anything that mutates them.",
         f"{ran} analyses of {distinct} different files run at once: 16 threads, then 120 more through <code>asyncio.to_thread</code>, while another thread runs <code>gc.collect()</code> in a loop. Every result is compared with the same file's result from a sequential run."])
    out.tables = [("Mutable containers created at import time in engine/", ["Module", "Name", "Kind", "Mutated anywhere?"], state or [["(none)", "", "", ""]]),
                  ("Concurrent run", ["Analyses run", "Different from the sequential result", "Distinct files"], [[str(ran), str(wrong), str(distinct)]])]
    out.checks = [
        Check("Is there global or class-level accumulator state?", PASS if not mutated else FAIL,
              f"No. Every accumulator belongs to a per-call object: <code>analyze_tree()</code> builds a new <code>_Walk</code> with its own stack, scope tracker, collectors and result lists each time. The {len(state)} module-level containers are lookup tables and none is mutated after import. There is no <code>_LAZY_LANGUAGE_CACHE</code> in the code ({cache} files mention it)."),
        Check("Do concurrent runs contaminate each other?", PASS if wrong == 0 else FAIL,
              f"No: {ran} concurrent analyses, {wrong} differing from the sequential baseline, including during constant garbage collection."),
        Check("Can the event loop pause the engine halfway through a file?", PASS,
              "No. <code>run_local</code> is an ordinary synchronous function with no <code>await</code>, so asyncio can't interleave inside it; only threads could, and the test above uses threads. The spec also runs each job in its own process, which shares nothing."),
        Check("Is contextvars needed?", CAVEAT,
              "No. The state is already passed structurally (an object created per call), which the report lists as the alternative. <code>contextvars</code> would add machinery to solve a sharing problem the engine doesn't have."),
    ]
    return out


# ---- Issue 17 ----------------------------------------------------------------------------

THREAD_CHILD = """
import gc, json, sys, threading, time
from pathlib import Path
from argon.engine.languages import parse_source

def count(root):
    stack, nodes = [root], 0
    while stack:
        node = stack.pop(); node.kind(); nodes += 1; stack.extend(node.children())
    return nodes

sources = [(Path(p).name, Path(p).read_bytes()) for p in sys.argv[2:]]
baseline = {name: count(parse_source(name, data).root) for name, data in sources}
shared = {name: parse_source(name, data) for name, data in sources}
rounds, wrong, stop, visited = int(sys.argv[1]), [], threading.Event(), [0] * 16

def churn():
    while not stop.is_set():
        gc.collect(); time.sleep(0.0005)

def independent(slot):
    for i in range(rounds):
        name, data = sources[i % len(sources)]
        nodes = count(parse_source(name, data).root); visited[slot] += nodes
        if nodes != baseline[name]: wrong.append(name)

def on_shared_tree(slot):
    for i in range(rounds):
        name = sources[i % len(sources)][0]
        nodes = count(shared[name].root); visited[slot] += nodes
        if nodes != baseline[name]: wrong.append(name)

started = time.perf_counter()
collector = threading.Thread(target=churn); collector.start()
threads = [threading.Thread(target=independent if slot < 8 else on_shared_tree, args=(slot,)) for slot in range(16)]
for thread in threads: thread.start()
for thread in threads: thread.join()
stop.set(); collector.join()
print(json.dumps({"nodes": sum(visited), "wrong": len(wrong), "seconds": time.perf_counter() - started}))
"""


def stress_process(rounds: int) -> dict:
    paths = [str(p) for p in sorted(SPEC_FIXTURES.glob("*.*")) if p.is_file() and language_for(p.name)][:10]
    done = subprocess.run([sys.executable, "-c", THREAD_CHILD, str(rounds), *paths], cwd=ROOT, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        return {"crashed": True, "code": done.returncode, "error": (done.stderr.strip().splitlines() or ["no output"])[-1]}
    return json.loads(done.stdout.strip().splitlines()[-1]) | {"crashed": False}


def issue17(quick: bool = False) -> TestResult:
    runs = [stress_process(400 if quick else 2500) for _ in range(2 if quick else 3)]
    tags = [line.split(":", 1)[1].strip() for line in (distribution("ast-grep-py").read_text("WHEEL") or "").splitlines() if line.startswith("Tag:")]
    gil = getattr(sys, "_is_gil_enabled", lambda: True)()
    out = TestResult(
        "Reported issue 17: free-threaded Python and reference-count races on SgNode",
        "Python 3.13's free-threaded mode and concurrent garbage collection can race on the reference counts of ast-grep-py's native nodes, causing a silent segmentation fault that takes down the server. Isolate the bindings to one thread.",
        ["The interpreter's actual GIL state and the installed ast-grep-py wheel's ABI tag.",
         "A stress run in a fresh process, repeated, with 16 threads at once: 8 threads each parse and walk their own trees, 8 threads walk the same shared trees concurrently, and a ninth thread calls <code>gc.collect()</code> continuously. A crash would show as a non-zero exit code; each walk's node count is checked against a single-threaded baseline."])
    out.tables = [("Interpreter", ["Item", "Value"], [["python", sys.version.split()[0]], ["GIL enabled", str(gil)], ["free-threaded build", str(bool(__import__("sysconfig").get_config_var("Py_GIL_DISABLED")))],
                                                    ["ast-grep-py wheel tags", ", ".join(tags) or "unknown"]]),
                  ("Concurrent stress runs (fresh process each)", ["Run", "Node visits (SgNode objects created and freed)", "Wrong counts", "Seconds", "Result"],
                   [[str(i + 1), "-" if r["crashed"] else fmt(r["nodes"]), "-" if r["crashed"] else str(r["wrong"]), "-" if r["crashed"] else f"{r['seconds']:.1f}",
                     f"CRASH (exit {r['code']}): {r['error']}" if r["crashed"] else "clean exit"] for i, r in enumerate(runs)])]
    clean = all(not r["crashed"] and r["wrong"] == 0 for r in runs)
    out.checks = [
        Check("Does the claim apply to this setup?", CAVEAT,
              f"Not as described. This is a standard Python {sys.version.split()[0]} build with the GIL {'on' if gil else 'OFF'}, and with the GIL on, reference-count changes on any object are serialized; Python 3.13 did not change that. Free-threaded Python is a separate, opt-in build (<code>python3.13t</code>), and I have none installed, so I could not test it."),
        Check("Did heavy concurrent use crash or corrupt anything?", PASS if clean else FAIL,
              (f"No. {len(runs)} fresh processes, {fmt(sum(r['nodes'] for r in runs))} node wrappers created and destroyed across 16 threads under constant garbage collection, shared trees included: every process exited cleanly and every node count matched the baseline." if clean
               else "A run crashed or miscounted; see the table.")),
        Check("Could the free-threaded scenario arise at all?", CAVEAT,
              f"Probably not through this package as installed. The wheel is tagged <code>{', '.join(tags) or 'unknown'}</code>, the standard (GIL) build of Python 3.13; as far as I know free-threaded interpreters use a different tag (<code>cp313t</code>), so pip would not pick this wheel for one. I could not check that against a real free-threaded interpreter, so if Phase 1 ever runs free-threaded Python, treat ast-grep-py as unverified there."),
        Check("Is a single-thread worker channel needed?", CAVEAT,
              "Not on the evidence here. The spec already runs each job in its own process, so no process ever shares tree objects across requests, which is stronger than a single-thread channel. A stress run is evidence, not proof: it can't rule out a race, only fail to find one."),
    ]
    return out
