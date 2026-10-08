import tracemalloc
from pathlib import Path

import pytest

from argon.engine.ast_counters import _Walk, analyze_tree
from argon.engine.config import EngineConfig
from argon.engine.languages import parse_source
from argon.engine.run import run_local

NL = chr(10)
FIXTURES = Path(__file__).parent / "fixtures"
LAZY = EngineConfig(max_children=10**9)
BULK = EngineConfig(lazy_children_threshold=10**9, max_children=10**9)
FORCED_LAZY = EngineConfig(lazy_children_threshold=2)  # every node with 3+ children takes the sibling walk


def _array(elements: int) -> bytes:
    return ("const a = [" + NL + ("1," + NL) * elements + "];" + NL).encode()


def _walk_peak_bytes(elements: int, cfg: EngineConfig) -> int:
    """Peak Python-side allocation while the walk runs (the tree itself lives in Rust)."""
    walk = _Walk(parse_source("a.js", _array(elements)), cfg, "")
    tracemalloc.start()
    try:
        walk.run()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_walk_memory_is_flat_as_a_node_gets_wider():
    small, wide, wider = (_walk_peak_bytes(n, LAZY) for n in (1_000, 100_000, 300_000))

    assert wider < 500_000  # under half a megabyte for 300,000 elements
    assert wider < small + 100_000  # and no growth with width


def test_bulk_fetch_would_have_allocated_hundreds_of_times_more():
    # Guards the test above: it must be measuring the wrappers the sibling walk avoids.
    assert _walk_peak_bytes(100_000, BULK) > 50 * _walk_peak_bytes(100_000, LAZY)


def test_a_100k_element_array_is_scored_end_to_end():
    ternaries = ("const a = [" + NL + ("x?1:2," + NL) * 100_000 + "];" + NL).encode()

    result = run_local({"table.js": ternaries})

    assert result.errors == {}
    assert result.findings[0].cyclomatic_branches == 100_000


def test_sibling_walk_matches_bulk_walk_on_every_fixture():
    checked = 0
    for path in sorted(FIXTURES.rglob("*")):
        if path.suffix not in {".py", ".js", ".ts", ".tsx", ".jsx"} or not path.is_file():
            continue
        data = path.read_bytes()
        bulk = analyze_tree(parse_source(path.name, data), BULK, path.as_posix())
        lazy = analyze_tree(parse_source(path.name, data), FORCED_LAZY, path.as_posix())
        assert bulk == lazy, path
        checked += 1
    assert checked > 10


def test_dead_code_after_a_return_is_tracked_across_siblings():
    source = (
        "function f(a) {" + NL + "  if (a) { return 1; }" + NL + "  return 2;" + NL + "  if (a) { return 3; }" + NL + "}" + NL
    ).encode()

    bulk = analyze_tree(parse_source("a.js", source), BULK)
    lazy = analyze_tree(parse_source("a.js", source), FORCED_LAZY)

    assert bulk.functions == lazy.functions and bulk.functions[0].determinate_outputs == 2  # the third return is dead


@pytest.mark.parametrize("name", ["chaos.py", "chaos.js"])
def test_nested_scopes_keep_their_depths_in_the_sibling_walk(name):
    data = (FIXTURES / name).read_bytes()

    assert analyze_tree(parse_source(name, data), FORCED_LAZY).nesting_score == analyze_tree(parse_source(name, data), BULK).nesting_score == 3
