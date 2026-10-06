"""Spec §12.1: scoring fixtures. Expected values come from the spec's table."""

from pathlib import Path

import pytest

from argon.engine.ast_counters import analyze_tree
from argon.engine.languages import parse_source

SPEC = Path(__file__).parent / "fixtures" / "spec"
FIXTURES = Path(__file__).parent / "fixtures"

# fixture, nesting score, flat count, reported max depth (None = not specified)
CASES = [
    ("nested_ifs.py", 7, 4, 3),
    ("isolation_1.py", 3, 2, None),
    ("isolation_2.py", 12, 4, None),
    ("comprehension.py", 6, 3, None),
    ("loop_header.py", 1 + 1 + 2, 3, None),
    ("elif_chain.js", 3, 3, 1),
    ("ternary_chain.js", 2, 2, None),
    ("ternary_nest.js", 3, 2, None),
    ("loop_else.py", 2, 2, None),
    ("match.py", 2, 2, None),
    ("except_nest.py", 1 + 1 + 2, 3, None),
    ("control_flow.js", 6, 3, None),
    ("guard_chain.js", 3, 3, None),
    ("closure_nest.js", 1 + 3 * 2, 3, 3),
    ("tests/test_asserts.py", 0, 0, None),
]


@pytest.mark.parametrize("name, score, flat, depth", CASES)
def test_spec_fixture(name, score, flat, depth):
    metrics = analyze_tree(parse_source(name, (SPEC / name).read_bytes()), filename=name)

    assert (metrics.nesting_score, metrics.cyclomatic_branches) == (score, flat)
    if depth is not None:
        assert metrics.max_depth == depth


def test_isolation_rows():
    rows_1 = _rows("isolation_1.py")
    rows_2 = _rows("isolation_2.py")

    assert rows_1 == {"outer": 3, "outer.inner": 1}
    assert rows_2 == {"outer": 12, "outer.inner": 3}


def test_nested_flag_fires_through_closures():
    # A6: a callback inside `if (a)` holding `if (b) { if (c) }` is 3 levels deep.
    metrics = analyze_tree(parse_source("closure_nest.js", (SPEC / "closure_nest.js").read_bytes()))

    assert metrics.max_depth == 3


def test_test_asserts_count_outside_test_paths():
    source = (SPEC / "tests" / "test_asserts.py").read_bytes()

    metrics = analyze_tree(parse_source("app/checks.py", source), filename="app/checks.py")

    assert metrics.cyclomatic_branches == 20  # 10 asserts + 10 `and`s


def test_deep_callbacks_saturate():
    """B3: 40 callbacks, each inside an `if`, would score far past 2^53."""
    source = "".join("if (a) { f(() => {\n" for _ in range(40)) + "}) }\n" * 40

    metrics = analyze_tree(parse_source("deep_callbacks.js", source.encode()))

    assert metrics.cyclomatic_branches == 40
    assert metrics.nesting_score == 1_000_000
    assert metrics.module.saturated


@pytest.mark.parametrize("name", ["chaos.py", "chaos.js"])
def test_chaos_fixture_scores_three(name):
    # Open question 4: def outer: if a: def inner: if b  ->  1 + 1 x (1 + 1) = 3.
    metrics = analyze_tree(parse_source(name, (FIXTURES / name).read_bytes()))

    assert (metrics.nesting_score, metrics.cyclomatic_branches) == (3, 2)


def _rows(name):
    metrics = analyze_tree(parse_source(name, (SPEC / name).read_bytes()))
    return {function.name: function.score for function in metrics.functions}
