"""Spec §12.2 score math: build FileFindings directly, threshold T = 40, no parsing."""

from krypton.engine.config import EngineConfig
from krypton.engine.rules import (
    check_concentration_rule,
    check_fragmentation_rule,
    compute_purity_score,
    determine_check_conclusion,
    round_half_up,
)
from krypton.engine.run import run_local
from krypton.schemas import FileFinding

# The spec's worked examples use T = 40, whatever calibration has since chosen.
T40 = EngineConfig(semantic_path_flag_threshold=40, nesting_flag_threshold=40)


def finding(name="a.py", rating=0.0, weight=10, flagged=None, effective_lines=10, async_boundaries=0):
    flagged = rating > 40 if flagged is None else flagged
    return FileFinding(
        filename=name,
        line_count=effective_lines,
        async_boundary_count=async_boundaries,
        cyclomatic_branches=0,
        lines_altered=weight,
        determinate_outputs=0,
        relative_import_count=0,
        logic_complexity_rating=rating,
        flagged=flagged,
        flag_reason="semantic_path_logic" if flagged else None,
        language="python",
        effective_lines=effective_lines,
        effective_lines_altered=weight,
        nesting_score=0,
        max_depth=0,
        parse_error=False,
        error_coverage=0.0,
        functions=[],
    )


def test_equal_weights():
    findings = [finding("a", 75), finding("b", 10), finding("c", 20)]

    score = compute_purity_score(findings, False, T40)

    assert score == 76  # 75.757
    assert determine_check_conclusion(score, T40) == "success"
    assert compute_purity_score(findings, True, T40) == 51


def test_overshoot_caps_at_thirty():
    assert compute_purity_score([finding(rating=40_000_000)], False, T40) == 0


def test_no_findings_scores_100():
    assert compute_purity_score([], False, T40) == 100


def test_round_half_up():
    assert round_half_up(46.5) == 47  # Python's round() would give 46 (B6)


def test_a5_dilution_is_weighted():
    findings = [finding("big", 4000, weight=200)] + [finding(f"s{i}", 5, weight=1) for i in range(19)]

    assert compute_purity_score(findings, False, T40) == 18


def test_concentration_rule_boundaries():
    assert check_concentration_rule(finding(effective_lines=801, async_boundaries=4), T40)
    assert not check_concentration_rule(finding(effective_lines=800, async_boundaries=4), T40)
    assert not check_concentration_rule(finding(effective_lines=801, async_boundaries=3), T40)


def test_fragmentation_never_flags_files():
    names = [f"m{index}.js" for index in range(6)]
    files = {name: "".join(f'import "./{other}";\n' for other in names if other != name).encode() for name in names}

    result = run_local(files, cfg=T40)

    assert result.fragmentation_flagged
    assert all(not item.flagged and item.flag_reason is None for item in result.findings)
    assert result.purity_score == 75


def test_fragmentation_needs_min_files_and_density():
    findings = [finding(name) for name in ("a", "b")]
    edges = {("a", "b"), ("b", "a")}

    assert not check_fragmentation_rule(findings, edges, T40).triggered


def test_empty_run_is_success():
    result = run_local({}, cfg=T40)

    assert (result.purity_score, result.check_conclusion) == (100, "success")
