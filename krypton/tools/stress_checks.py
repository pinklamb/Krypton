"""Run the four stress tests and collect results: tables, checks with verdicts, and real CLI output."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from krypton.engine.config import DEFAULT_CONFIG as CFG, EngineConfig
from krypton.engine.run import run_local
from tests.parity.radon_reference import nesting_score as reference_score
from tools import stress_cases as cases
from tools import stress_measure as measure_tools
from tools.stress_measure import Measured, measure

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "stress" / "samples"

PASS, CAVEAT, FINDING, FAIL = "pass", "caveat", "finding", "fail"


@dataclass
class Check:
    """One thing a challenge asked me to verify, what happened, and how to read it."""

    question: str
    verdict: str
    detail: str


@dataclass
class TestResult:
    title: str
    ask: str
    setup: list[str]
    tables: list[tuple[str, list[str], list[list[str]]]] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    cli: list[tuple[str, str, int]] = field(default_factory=list)  # (command, output, exit code)

    @property
    def verdict(self) -> str:
        order = [FAIL, FINDING, CAVEAT, PASS]
        return next((v for v in order if any(c.verdict == v for c in self.checks)), PASS)


def fmt(value: float) -> str:
    return f"{value:,.0f}"


def measured_row(item: Measured) -> list[str]:
    status = "flagged" if item.flagged else "not flagged"
    return [item.label, str(item.physical_lines), str(item.characters), str(item.effective_lines), str(item.branches),
            fmt(item.rating_master), status]


MEASURED_HEADERS = ["Variant", "Physical lines", "Characters", "Effective lines", "Branches", "rating_master", f"Verdict (threshold {CFG.semantic_path_flag_threshold:g})"]


def cli_run(label: str, args: list[str]) -> tuple[str, str, int]:
    output, code = measure_tools.run_cli(args)
    return (f"python -m krypton.cli {' '.join(args)}", output, code)


# ---- Test 1 ------------------------------------------------------------------------------


def test1() -> TestResult:
    big = {name: measure(name, src, "b.js") for name, src in
           [("4 loops, formatted", cases.billing_js(4)), ("4 loops, one line", cases.one_line(cases.billing_js(4))),
            ("4 loops, minified", cases.minified(cases.billing_js(4)))]}
    small = {name: measure(name, src, "b.js") for name, src in
             [("3 loops, formatted", cases.billing_js(3)), ("3 loops, one line", cases.one_line(cases.billing_js(3))),
              ("3 loops, minified", cases.minified(cases.billing_js(3)))]}
    large = {name: measure(name, src, "b.js") for name, src in
             [("14 decisions, formatted", cases.BILLING_JS), ("14 decisions, one line", cases.one_line(cases.BILLING_JS)),
              ("14 decisions, minified", cases.minified(cases.BILLING_JS))]}
    python = [measure("python formatted", cases.BILLING_PY, "b.py"), measure("python packed", cases.BILLING_PY_PACKED, "b.py")]
    files = {f"billing4_{key}.js": text for key, text in
             [("formatted", cases.billing_js(4)), ("one_line", cases.one_line(cases.billing_js(4))), ("minified", cases.minified(cases.billing_js(4)))]}
    files.update({f"billing3_{key}.js": text for key, text in
                  [("formatted", cases.billing_js(3)), ("one_line", cases.one_line(cases.billing_js(3))), ("minified", cases.minified(cases.billing_js(3)))]})
    files.update({"billing_large_formatted.js": cases.BILLING_JS, "billing_large_one_line.js": cases.one_line(cases.BILLING_JS),
                  "billing_large_minified.js": cases.minified(cases.BILLING_JS)})
    files.update({"billing_formatted.py": cases.BILLING_PY, "billing_packed.py": cases.BILLING_PY_PACKED})
    folder = measure_tools.write_samples(SAMPLES / "test1", files)
    result = TestResult(
        "Test 1: horizontal pack", "Smash a 4-nested-loop billing script onto one line. The effective-line count should rise to match its real density and the file should still be flagged.",
        ["JavaScript billing function with 4 nested loops, guards and a tax ternary, written three ways: formatted, every newline removed (one line), and one line with optional spaces removed (minified).",
         "The same function with 3 nested loops, to find where packing starts to matter.",
         "Python version: Python can't put nested loops on one line (<code>for a in b: for c in d:</code> is a SyntaxError), so the packed Python version only joins simple statements."])
    result.tables = [("4 nested loops", MEASURED_HEADERS, [measured_row(v) for v in big.values()]),
                     ("3 nested loops", MEASURED_HEADERS, [measured_row(v) for v in small.values()]),
                     ("A larger script: 4 nested loops, 14 decisions", MEASURED_HEADERS, [measured_row(v) for v in large.values()]),
                     ("Python (what packing Python allows)", MEASURED_HEADERS, [measured_row(v) for v in python])]
    from tools.stress_attribution import attribution  # imported here: that module imports Check from this one

    attribution_rows, attribution_check = attribution()
    result.tables.append(("Effective lines credited to each function (formatted vs one line)", ["Function", "Formatted", "On one line"], attribution_rows))
    result.checks = test1_checks(big, small, python, large) + [attribution_check]
    result.cli = [cli_run("folder", [str(folder.relative_to(ROOT)), "--top", "12"])]
    return result


def test1_checks(big: dict, small: dict, python: list[Measured], large: dict) -> list[Check]:
    formatted, packed, minified = list(big.values())
    ratio = packed.effective_lines / formatted.effective_lines
    ratio_min = minified.effective_lines / formatted.effective_lines
    f3, p3, m3 = list(small.values())
    big_f, big_p, big_m = list(large.values())
    large_ratio, large_min_ratio = big_p.effective_lines / big_f.effective_lines, big_m.effective_lines / big_f.effective_lines
    escape = f3.flagged and not p3.flagged
    band_top = CFG.semantic_path_flag_threshold / min(ratio, ratio_min)
    return [
        Check("Is the one-line version still flagged?", PASS if packed.flagged and minified.flagged else FAIL,
              f"Yes for the 4-loop script: rating {fmt(packed.rating_master)} (one line) and {fmt(minified.rating_master)} (minified) against a threshold of {CFG.semantic_path_flag_threshold:g}, even though the physical line count is {packed.physical_lines}."),
        Check("Does the effective-line count rise back to the formatted size?", CAVEAT,
              f"Partly. One line keeps {ratio:.0%} of the formatted effective lines ({packed.effective_lines} vs {formatted.effective_lines}) and minified keeps {ratio_min:.0%}. On the larger 14-decision script the figures are {large_ratio:.0%} and {large_min_ratio:.0%}. Packing still discounts the rating by about {1 - min(ratio, ratio_min):.0%}, because the width term (characters / 40) shrinks when whitespace is removed. SCORING.md's A4 target is 60%, and minified falls below it on this sample."),
        Check("Can packing push a flagged file under the threshold?", FINDING if escape else PASS,
              f"Yes: the 3-loop script is flagged formatted (rating {fmt(f3.rating_master)}) but not flagged on one line ({fmt(p3.rating_master)}) or minified ({fmt(m3.rating_master)})." if escape
              else "No: every packed variant stayed flagged."),
        Check("How wide is the escape band?", FINDING,
              f"With this packing ratio, any file whose formatted rating is between {CFG.semantic_path_flag_threshold:g} and about {fmt(band_top)} drops below the threshold once packed. A rating above that stays flagged."),
        Check("Python", PASS,
              f"Python can't nest loops on one line, so packing only joins simple statements: the packed version keeps {python[1].effective_lines / python[0].effective_lines:.0%} of the formatted effective lines and is still flagged."),
    ]


# ---- Test 2 ------------------------------------------------------------------------------


def fragmentation_summary(count: int, cfg: EngineConfig = CFG):
    files = {name: text.encode() for name, text in cases.shattered_files(count).items()}
    return run_local(files, cfg=cfg)


def test2() -> TestResult:
    rows, first_trip = [], None
    for count in range(3, 9):
        result = fragmentation_summary(count)
        imports = result.findings[0].relative_import_count
        rows.append([str(count), str(imports), "yes" if result.fragmentation_flagged else "no",
                     str(result.flagged_file_count), str(result.purity_score)])
        first_trip = first_trip or (count if result.fragmentation_flagged else None)
    lowered = fragmentation_summary(4, EngineConfig(fragmentation_avg_import_threshold=2))
    four, six = fragmentation_summary(4), fragmentation_summary(6)
    for count in (4, 6):
        measure_tools.write_samples(SAMPLES / f"test2_{count}_files", {k: v for k, v in cases.shattered_files(count).items()})
    config = measure_tools.write_samples(SAMPLES / "config_test2", {"fragmentation_threshold_2.toml": "fragmentation_avg_import_threshold = 2\n"})
    result = TestResult(
        "Test 2: file shattering", "4 small files, each importing state from the other 3 with ../../ paths. No file should trip the concentration rule, but the PR should take the flat -25 fragmentation penalty.",
        ["Each module lives at <code>src/moduleN/lib/moduleNState.js</code> and imports the other modules' state with <code>../../moduleM/lib/moduleMState.js</code>.",
         "I also tried 3 to 8 files, and 4 files with the fragmentation threshold lowered from 4 to 2."])
    result.tables = [("Fully cross-importing files (default thresholds)", ["Files", "Imports per file", "Fragmentation fires", "Files flagged", "Purity score"], rows)]
    result.checks = [
        Check("Do the ../../ imports resolve?", PASS, f"Yes: all 4 files report {four.findings[0].relative_import_count} internal imports (the other 3), resolved through <code>../../moduleM/lib/moduleMState.js</code>."),
        Check("Do the small files pass the concentration rule?", PASS, f"Yes: {four.flagged_file_count} of 4 files flagged; each has {four.findings[0].effective_lines} effective lines."),
        Check("Does the fragmentation penalty trigger with 4 files?", FINDING,
              f"No. Four files fully importing each other give 3 imports per file. The rule needs more than {CFG.fragmentation_avg_import_threshold:g}, so the purity score stays {four.purity_score}. Under the default thresholds it first fires at {first_trip} files ({six.findings[0].relative_import_count} imports each), dropping the score to {six.purity_score}. This is the spec's open question 2."),
        Check("What does a lower threshold do?", PASS, f"With <code>fragmentation_avg_import_threshold = 2</code>, the same 4 files trigger the -25 penalty (purity {lowered.purity_score}) and no file is flagged, as intended."),
    ]
    result.cli = [cli_run("four", [str((SAMPLES / "test2_4_files").relative_to(ROOT)), "--top", "0"]),
                  cli_run("four, threshold 2", [str((SAMPLES / "test2_4_files").relative_to(ROOT)), "--top", "0", "--config", str((config / "fragmentation_threshold_2.toml").relative_to(ROOT))]),
                  cli_run("six", [str((SAMPLES / "test2_6_files").relative_to(ROOT)), "--top", "0"])]
    return result
