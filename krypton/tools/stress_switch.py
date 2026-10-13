"""Test 3: the switch-case game. Does outputs dilution hide nested logic inside a wide switch?"""

from __future__ import annotations

from krypton.engine.config import DEFAULT_CONFIG as CFG
from tools import stress_cases as cases
from tools import stress_measure as measure_tools
from tools.stress_checks import FINDING, PASS, SAMPLES, ROOT, Check, TestResult, cli_run, fmt
from tools.stress_measure import Measured, measure

MASTER_T, NESTING_T = CFG.semantic_path_flag_threshold, CFG.nesting_flag_threshold
HEADERS = ["Variant", "Branches", "Outputs (effective)", "Effective lines", "Nesting score", "Depth", "rating_master", "rating_nesting", "Flagged (master)", "Flagged (nesting)"]
SWEEP_HEADERS = ["Cases", "Variant", "Branches", "Outputs", "rating_master", "rating_nesting", "Flagged (master)", "Flagged (nesting)"]


def yes(flag: bool) -> str:
    return "flagged" if flag else "not flagged"


def row(item: Measured) -> list[str]:
    return [item.label, str(item.branches), str(item.effective_outputs), str(item.effective_lines), str(item.nesting_score),
            str(item.max_depth), fmt(item.rating_master), fmt(item.rating_nesting), yes(item.rating_master > MASTER_T), yes(item.rating_nesting > NESTING_T)]


def sweep_row(count: int, item: Measured) -> list[str]:
    return [str(count), item.label, str(item.branches), str(item.effective_outputs), fmt(item.rating_master), fmt(item.rating_nesting),
            yes(item.rating_master > MASTER_T), yes(item.rating_nesting > NESTING_T)]


def smallest_flagged_flat_switch(mode: str) -> int:
    """The fewest cases at which a plain flat switch (no nesting at all) is flagged in `mode`."""
    def flagged(count: int) -> bool:
        item = measure("flat", cases.switch_source("flat", count), "s.js")
        return item.rating_master > MASTER_T if mode == "master" else item.rating_nesting > NESTING_T

    return next(count for count in range(1, 400) if flagged(count))


def test3() -> TestResult:
    flat, nested, twin = (measure(label, cases.switch_source(style), "s.js") for label, style in
                          [("flat switch (40 cases)", "flat"), ("40 cases, 3 hide nested ifs", "nested"), ("flat twin (same decisions, un-nested)", "flat_twin")])
    only = measure("the 3 nested blocks alone", cases.nested_only_source(), "s.js")
    sweep = [(n, measure(style, cases.switch_source(style, n), "s.js")) for n in (5, 10, 20, 40, 80, 160) for style in ("flat", "nested")]
    files = {"switch_flat.js": cases.switch_source("flat"), "switch_nested.js": cases.switch_source("nested"),
             "switch_flat_twin.js": cases.switch_source("flat_twin"), "nested_only.js": cases.nested_only_source()}
    folder = measure_tools.write_samples(SAMPLES / "test3", files)
    config = measure_tools.write_samples(SAMPLES / "config_test3", {"nesting_mode.toml": 'rating_mode = "nesting"\n'})
    result = TestResult(
        "Test 3: switch-case game", "A flat 40-case switch with deep if/else inside 3 cases. The squared-branch factor should dominate and the 40 outputs must not dilute the nested structures.",
        ["<code>switch_flat.js</code>: 40 distinct cases, each returning its own value.",
         "<code>switch_nested.js</code>: the same switch, but cases 6, 20 and 33 each hold 4 nested ifs with a distinct return on every path (score 10 each).",
         "<code>switch_flat_twin.js</code>: the same 12 extra decisions in those cases, written one after another instead of nested.",
         "<code>nested_only.js</code>: the three nested blocks on their own, with no switch around them.",
         "Then the switch size swept from 5 to 160 cases."])
    result.tables = [("40-case switches, both rating modes", HEADERS, [row(item) for item in (flat, nested, twin, only)]),
                     ("Sweep: nested blocks hidden in a switch of N cases", SWEEP_HEADERS, [sweep_row(n, item) for n, item in sweep])]
    result.checks = test3_checks(flat, nested, twin, only, sweep)
    rel = str(folder.relative_to(ROOT))
    result.cli = [cli_run("master", [rel, "--top", "6"]), cli_run("nesting", [rel, "--top", "6", "--config", str((config / "nesting_mode.toml").relative_to(ROOT))])]
    return result


def test3_checks(flat: Measured, nested: Measured, twin: Measured, only: Measured, sweep: list) -> list[Check]:
    undiluted = nested.branches**2 * nested.effective_lines
    hidden_ok = {n: item for n, item in sweep if item.label == "nested" and item.rating_nesting <= NESTING_T}
    escapes = ", ".join(str(n) for n in sorted(hidden_ok)) or "none"
    first_master, first_nesting = smallest_flagged_flat_switch("master"), smallest_flagged_flat_switch("nesting")
    return [
        Check("Does the squared-branch factor dominate?", FINDING,
              f"Not in practice. The master rating divides by the output count, and a switch returns a distinct value per case, so outputs are about branches + 1. For the nested switch that is {nested.branches}² × {nested.effective_lines} lines = {fmt(undiluted)} divided by {nested.effective_outputs} outputs = {fmt(nested.rating_master)}. The A1 cap (branches + 1) can't help: every case's return is legitimate, so the divisor is allowed to be that large. The rating behaves like branches × lines, not branches²."),
        Check("Does the high output count dilute the nested structures?", FINDING,
              f"Yes. The three nested blocks alone score {fmt(only.rating_nesting)} in nesting mode ({only.branches} branches, 1 output). Hidden inside the 40-case switch the same blocks rate {fmt(nested.rating_nesting)}, {only.rating_nesting / nested.rating_nesting:.1f}× lower, and in switches of {escapes} cases the whole function falls below the nesting threshold ({NESTING_T:g}) and is not flagged, although the same blocks on their own are."),
        Check("Does master mode see nesting at all?", FINDING,
              f"No. Master counts decisions, not depth. The nested switch rates {fmt(nested.rating_master)} but its un-nested twin (the same {twin.branches} decisions) rates {fmt(twin.rating_master)}, higher, because the twin has fewer distinct outputs ({twin.effective_outputs} vs {nested.effective_outputs}). In nesting mode the order is right but the gap is small ({fmt(nested.rating_nesting)} vs {fmt(twin.rating_nesting)})."),
        Check("Is a plain flat switch flagged?", FINDING,
              f"In master mode yes, from {first_master} cases up: the flat 40-case switch rates {fmt(flat.rating_master)} against a threshold of {MASTER_T:g}, with no nesting anywhere. In nesting mode a flat switch first flags at {first_nesting} cases. The thresholds were fitted on two synthetic files, so this may just be calibration."),
    ]
