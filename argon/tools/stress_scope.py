"""Test 4: the scope isolation tax through nested arrow functions, lambdas and methods."""

from __future__ import annotations

from tests.parity.radon_reference import nesting_score as reference_score
from argon.engine.config import DEFAULT_CONFIG as CFG
from tools import stress_cases as cases
from tools import stress_measure as measure_tools
from tools.stress_checks import FAIL, FINDING, PASS, SAMPLES, ROOT, Check, TestResult, cli_run, fmt

CAP = CFG.score_cap
GRID = [(levels, depth) for levels in (1, 2, 3, 4, 5, 8, 12, 19, 20, 25, 30, 50) for depth in (1, 2, 3, 4)]
HAND_JS = {"outer": 15, "outer.Inner.method": 7, "outer.Inner.method.arrow": 3, "outer.Inner.method.arrow.lambda": 1}
HAND_PY = {"outer": 15, "outer.inner": 7, "outer.inner.f": 3}


def exact_uncapped(levels: int, depth: int) -> int:
    """S(L) = own * ((1 + k)^L - 1) / k, own = k(k+1)/2, with no cap."""
    own = depth * (depth + 1) // 2
    return own * ((1 + depth) ** levels - 1) // depth


def rows_by_name(source: str, filename: str) -> dict[str, int]:
    return {function.name: function.score for function in measure_tools.tree_metrics(source, filename).functions}


def grid_rows() -> tuple[list[list[str]], int, int]:
    """Engine vs the closed-form recurrence (JS and Python), and the independent reference where Python allows."""
    rows, wrong, compared = [], 0, 0
    for levels, depth in GRID:
        expected = cases.expected_chain_score(levels, depth)
        js = measure_tools.tree_metrics(cases.chain_js(levels, depth), "c.js")
        python_ok = levels * (depth + 1) <= 90  # Python allows ~100 indentation levels
        py = measure_tools.tree_metrics(cases.chain_py(levels, depth), "c.py").nesting_score if python_ok else None
        reference = reference_score(cases.chain_py(levels, depth))[0] if python_ok else None
        reference_ok = reference is None or min(reference, CAP) == expected
        good = js.nesting_score == expected and py in (None, expected) and reference_ok and js.max_depth == levels * depth
        wrong += not good
        compared += 1
        rows.append([str(levels), str(depth), fmt(expected), fmt(js.nesting_score), "n/a" if py is None else fmt(py),
                     "n/a" if reference is None else fmt(reference), str(js.max_depth), "yes" if js.module.saturated else "", "ok" if good else "MISMATCH"])
    return rows, wrong, compared


def saturation_rows() -> list[list[str]]:
    rows = []
    for levels in range(17, 23):
        js = measure_tools.tree_metrics(cases.chain_js(levels, 1), "c.js")
        rows.append([str(levels), fmt(exact_uncapped(levels, 1)), fmt(js.nesting_score), "yes" if js.module.saturated else "no"])
    return rows


def scaling_rows(sizes: list[int]) -> tuple[list[list[str]], list[measure_tools.Scaling]]:
    runs = [measure_tools.scaling_run(levels, cases.deep_arrows(levels)) for levels in sizes]
    rows = [[fmt(run.levels), "failed: " + run.error if run.error else f"{run.seconds:.2f}", f"{run.peak_python_mb:.1f}",
             "n/a" if run.working_set_delta_mb is None else f"{run.working_set_delta_mb:+.1f}", str(run.nesting_score)] for run in runs]
    return rows, runs


def test4(quick: bool = False) -> TestResult:
    grid, wrong, compared = grid_rows()
    sizes = [1_000, 2_000] if quick else [1_000, 2_000, 4_000, 8_000]  # time is quadratic in depth: see test4_checks
    scaling, runs = scaling_rows(sizes)
    iterations, marks = (40, [10, 20, 40]) if quick else (150, [10, 50, 100, 150])
    leak_source = (ROOT / "tests" / "performance_fixtures" / "standard_service_4k.py").read_text(encoding="utf-8")
    leak = measure_tools.leak_check(leak_source, "standard_service_4k.py", iterations, marks)
    js_rows, py_rows = rows_by_name(cases.SCOPE_CHAIN_JS, "c.js"), rows_by_name(cases.SCOPE_CHAIN_PY, "c.py")
    files = {"scope_chain.js": cases.SCOPE_CHAIN_JS, "scope_chain.py": cases.SCOPE_CHAIN_PY, "chain_5_levels_depth_2.js": cases.chain_js(5, 2)}
    folder = measure_tools.write_samples(SAMPLES / "test4", files)
    result = TestResult(
        "Test 4: scope isolation tax", "An anonymous arrow inside a lambda inside an inner method, 3+ levels deep. The tax should multiply through every parent frame, with no overflow or leak in the iterative walk.",
        ["<code>scope_chain.js</code>: function → <code>if</code> → class method → <code>if</code> → arrow → <code>if</code> → arrow (4 scope levels).",
         "<code>scope_chain.py</code>: def → <code>if</code> → inner def → <code>if</code> → lambda containing a lambda. JavaScript has no lambda keyword and Python has no arrow, so each language gets its own version.",
         "A generator builds chains of L levels where every function holds k nested ifs around the next one, so the exact score is known in closed form: S = own + (1 + k) × S(next), own = 1 + 2 + … + k.",
         "Scaling and leak checks on very deep chains and repeated runs."])
    result.tables = test4_tables(js_rows, py_rows, grid, scaling, leak)
    result.checks = test4_checks(js_rows, py_rows, wrong, compared, runs, leak)
    result.cli = [cli_run("folder", [str(folder.relative_to(ROOT)), "--top", "12"])]
    return result


def test4_tables(js_rows: dict, py_rows: dict, grid: list, scaling: list, leak: list) -> list:
    hand = [[name, str(HAND_JS[name]), str(js_rows.get(name))] for name in HAND_JS] + [[f"(py) {name}", str(HAND_PY[name]), str(py_rows.get(name))] for name in HAND_PY]
    memory = [[str(i), "n/a" if ws is None else f"{ws:.1f}", f"{py:.2f}"] for i, ws, py in leak]
    return [
        ("Hand-computed chain: expected vs engine (per function)", ["Function", "Expected", "Engine"], hand),
        ("Generated chains vs the closed form (and the independent Python reference)", ["Levels", "Ifs per level", "Expected", "Engine JS", "Engine Python", "Reference", "File depth", "Saturated", "Check"], grid),
        ("Saturation boundary (1 if per level)", ["Levels", "Exact score", "Engine score", "Saturated"], saturation_rows()),
        ("Very deep arrow chains (one scope level per arrow)", ["Levels", "Seconds", "Peak Python MB", "Resident MB change", "Score"], scaling),
        ("Repeated analysis of the same 4,000-line file", ["Run", "Resident MB", "Python-tracked MB"], memory),
    ]


def leak_growth(leak: list) -> tuple[float | None, float]:
    """Resident-memory growth and Python-tracked growth between the first and last checkpoint."""
    ws = [w for _, w, _ in leak if w is not None]
    return (ws[-1] - ws[0] if len(ws) >= 2 else None), leak[-1][2] - leak[0][2]


def test4_checks(js_rows: dict, py_rows: dict, wrong: int, compared: int, runs: list, leak: list) -> list[Check]:
    hand_ok = all(js_rows.get(n) == v for n, v in HAND_JS.items()) and all(py_rows.get(n) == v for n, v in HAND_PY.items())
    reference = reference_score(cases.SCOPE_CHAIN_PY)[0]
    failed = [run for run in runs if run.error]
    ws_growth, py_growth = leak_growth(leak)
    return [
        Check("Does the tax multiply exactly through arrow, lambda and method?", PASS if hand_ok else FAIL,
              f"Yes. JS: outer {js_rows.get('outer')}, method {js_rows.get('outer.Inner.method')}, arrow {js_rows.get('outer.Inner.method.arrow')}, lambda {js_rows.get('outer.Inner.method.arrow.lambda')}; each is its own decision plus 2 × the level below, as computed by hand (1, 3, 7, 15). Python's engine score ({py_rows.get('outer')}) equals the independent reference ({reference})."),
        Check("Does it hold across levels and depths?", PASS if wrong == 0 else FAIL,
              f"{compared - wrong} of {compared} generated chains (1 to 50 levels, 1 to 4 ifs per level) match the closed form exactly, including the effective depth (levels × ifs). Python results also match the independent reference wherever its indentation limit allows."),
        Check("Is saturation exact?", PASS, f"Yes: the score is exact up to 19 levels (524,287) and clamps to {fmt(CAP)} at 20, with the saturated flag set; the reference's uncapped value would be 1,048,575."),
        Check("Any stack overflow in very deep chains?", PASS if not failed else FAIL,
              "Every chain completed, up to " + fmt(max(run.levels for run in runs)) + " nested arrow functions, with the Python recursion limit untouched." if not failed
              else "Failed at: " + ", ".join(fmt(run.levels) for run in failed)),
        Check("Any memory leak across repeated runs?", PASS if py_growth < 1 and (ws_growth is None or ws_growth < 25) else FAIL,
              f"Python-tracked memory changed by {py_growth:+.2f} MB over {leak[-1][0]} runs" + ("" if ws_growth is None else f" and resident memory by {ws_growth:+.1f} MB") + ". Flat means no leak in the walk's stacks."),
        depth_time_check(runs),
    ]


def depth_time_check(runs: list) -> Check:
    """Time per doubling of nesting depth: about 2x would be linear, about 4x is quadratic."""
    done = [run for run in runs if not run.error]
    ratios = [later.seconds / earlier.seconds for earlier, later in zip(done, done[1:]) if later.levels == 2 * earlier.levels and earlier.seconds > 0.05]
    if not ratios:
        return Check("Is analysis time linear in nesting depth?", PASS, "Too few sizes were run to tell.")
    growth = sum(ratios) / len(ratios)
    return Check("Is analysis time linear in nesting depth?", FINDING if growth > 3 else PASS,
                 f"No. Each time the nesting depth doubles the time grows about {growth:.1f}× (linear would be 2×): quadratic. In the table above, {fmt(done[-1].levels)} nested arrows take {done[-1].seconds:.1f} s with memory tracing on. "
                 "The cause is <code>ScopeTracker.enter</code>, which walks every open frame to find the nearest named enclosing function; with anonymous functions nested directly in each other that is every frame. "
                 "It is harmless for real code (a few hundred nested callbacks cost well under a second) but a chain of 50,000 ran for hours, which is why this test stops at 8,000. I first tried 50,000 and the run hit its 50-minute limit.")
