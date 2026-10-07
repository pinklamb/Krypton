import pytest

from argon.engine.config import DEFAULT_CONFIG as CFG, EngineConfig
from argon.engine.rules import overhead_points
from argon.engine.run import run_local

NL = chr(10)


def _python(trivial: int, real: int) -> bytes:
    wrappers = [f"def w{i}(x):{NL}    return helper(x)" for i in range(trivial)]
    meaningful = [
        NL.join([f"def r{i}(x):", "    total = 0", "    for item in x:", "        if item > 1:", "            total += item", "        if item > 2:", "            total -= 1", "    return total"])
        for i in range(real)
    ]
    return (NL * 2).join(wrappers + meaningful).encode() + NL.encode()


def test_tax_is_zero_below_the_free_share_and_small_files():
    assert overhead_points(3, 5, CFG) == 0  # fewer than wrapper_min_functions functions
    assert overhead_points(6, 10, CFG) == 0  # exactly at the free share (0.6)


def test_tax_rises_linearly_to_the_cap():
    assert overhead_points(8, 10, CFG) == pytest.approx(7.5)  # (0.8 - 0.6) / 0.4 of 15 points
    assert overhead_points(10, 10, CFG) == 15


def test_file_of_wrappers_loses_purity_points_but_is_not_flagged():
    taxed = run_local({"w.py": _python(8, 0)}).findings[0]
    clean = run_local({"w.py": _python(1, 7)}).findings[0]

    assert (taxed.trivial_wrapper_count, taxed.overhead_tax) == (8, 15)
    assert (clean.trivial_wrapper_count, clean.overhead_tax) == (1, 0)
    assert not taxed.flagged


def test_purity_drops_by_the_weighted_tax():
    files = {"w.py": _python(8, 0)}

    off = run_local(files, cfg=EngineConfig(wrapper_tax_max_points=0))
    on = run_local(files)

    assert off.purity_score - on.purity_score == 15
