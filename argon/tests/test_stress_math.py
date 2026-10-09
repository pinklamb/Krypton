"""Invariants behind the stress tests: the closed-form tax, the generators, and the cycle detector."""

import pytest

from argon.engine.run import run_local
from tools import stress_cases as cases
from tools.stress_issues_c import cycles
from tools.stress_measure import tree_metrics


@pytest.mark.parametrize("levels, depth", [(1, 1), (2, 1), (3, 1), (5, 2), (4, 3), (6, 2)])
def test_chain_matches_closed_form_in_both_languages(levels, depth):
    expected = cases.expected_chain_score(levels, depth)

    assert tree_metrics(cases.chain_js(levels, depth), "c.js").nesting_score == expected
    assert tree_metrics(cases.chain_py(levels, depth), "c.py").nesting_score == expected


def test_chain_saturates_at_the_cap():
    metrics = tree_metrics(cases.chain_js(25, 1), "c.js")

    assert metrics.nesting_score == 1_000_000
    assert metrics.module.saturated


def test_six_layer_callbacks_stay_far_from_the_cap():
    # The multiplier is 1 + definition depth per layer, compounded: 6 layers is nowhere near saturation.
    worst = max(tree_metrics(cases.callback_nest(6, wrap), "r.js").nesting_score for wrap in (0, 1, 2, 3))

    assert worst < 100_000


def test_tarjan_finds_cycles_and_ignores_chains():
    chain = {("a", "b"), ("b", "c")}
    loop = chain | {("c", "a")}
    two = {("a", "b"), ("b", "a")}

    assert cycles(["a", "b", "c"], chain) == []
    assert cycles(["a", "b", "c"], loop) == [["a", "b", "c"]]
    assert cycles(["a", "b", "c"], two) == [["a", "b"]]


def test_fragmentation_now_fires_at_four_small_cross_importing_files():
    fires = {n: run_local({k: v.encode() for k, v in cases.shattered_files(n).items()}).fragmentation_flagged for n in range(3, 8)}

    assert fires == {3: False, 4: True, 5: True, 6: True, 7: True}


def test_large_files_need_the_old_six_file_density_before_fragmentation_fires():
    padding = chr(10).join(f"x{i} = {i}" for i in range(70)) + chr(10)  # each file gets over 60 effective lines
    fires = {}
    for n in range(3, 8):
        files = {name: (text + padding).encode() for name, text in cases.shattered_files(n).items()}
        fires[n] = run_local(files).fragmentation_flagged

    assert fires == {3: False, 4: False, 5: False, 6: True, 7: True}


def test_packed_variants_keep_the_same_decisions():
    for loops in (1, 2, 3, 4):
        source = cases.billing_js(loops)
        flat = tree_metrics(source, "b.js").cyclomatic_branches

        assert tree_metrics(cases.one_line(source), "b.js").cyclomatic_branches == flat
        assert tree_metrics(cases.minified(source), "b.js").cyclomatic_branches == flat


def test_switch_generators_hold_the_same_decisions():
    nested = tree_metrics(cases.switch_source("nested"), "s.js").cyclomatic_branches
    twin = tree_metrics(cases.switch_source("flat_twin"), "s.js").cyclomatic_branches

    assert nested == twin == 40 + 12
