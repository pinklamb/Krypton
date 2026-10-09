import json

import pytest

from argon.cli import main
from argon.engine.config import DEFAULT_CONFIG, LEVELS, apply_level
from argon.engine.run import run_local
from tests.test_hints import DEEP

NL = chr(10)


def _branchy(count: int) -> bytes:
    """One function with `count` flat decisions: rating = count squared times its lines."""
    body = "".join(f"    if x == {i}:{NL}        x += {i}{NL}" for i in range(count))
    return ("def f(x):" + NL + body + "    return x" + NL).encode()


def test_levels_scale_both_fitted_thresholds():
    relaxed, strict = apply_level(DEFAULT_CONFIG, "relaxed"), apply_level(DEFAULT_CONFIG, "strict")

    assert apply_level(DEFAULT_CONFIG, "standard") is DEFAULT_CONFIG
    assert relaxed.semantic_path_flag_threshold == DEFAULT_CONFIG.semantic_path_flag_threshold * LEVELS["relaxed"]
    assert strict.nesting_flag_threshold == DEFAULT_CONFIG.nesting_flag_threshold * LEVELS["strict"]


def _flagged(source: bytes, level: str) -> bool:
    return run_local({"m.py": source}, cfg=apply_level(DEFAULT_CONFIG, level)).findings[0].flagged


def test_a_file_between_the_bars_flips_with_the_level():
    nine, six = _branchy(9), _branchy(6)  # ratings about 1,600 and 500: bars are 324 / 648 / 1,944

    assert [_flagged(nine, level) for level in ("strict", "standard", "relaxed")] == [True, True, False]
    assert [_flagged(six, level) for level in ("strict", "standard", "relaxed")] == [True, False, False]


def test_result_carries_the_threshold_in_force():
    result = run_local({"m.py": _branchy(2)}, cfg=apply_level(DEFAULT_CONFIG, "strict"))

    assert result.threshold == DEFAULT_CONFIG.active_threshold * 0.5


def test_summary_says_how_far_over_the_worst_function_is():
    result = run_local({"m.py": _branchy(12)})
    worst = result.findings[0].logic_complexity_rating

    assert f"the worst is {worst / result.threshold:.1f}x over." in result.summary_markdown
    assert "1 of 1 functions are over the threshold" in result.summary_markdown


def test_summary_says_all_clear_when_nothing_is_over():
    result = run_local({"m.py": DEEP})

    assert "All 1 functions are under the threshold" in result.summary_markdown


def test_hint_leads_with_how_many_times_over():
    text = run_local({"m.py": _branchy(12)}).hints["m.py:1 f"]

    assert " over the threshold; deepest decision" in text and text[0].isdigit()


def test_cli_level_changes_flags_and_rejects_unknown_levels(tmp_path, capsys):
    (tmp_path / "m.py").write_bytes(_branchy(12))
    main([str(tmp_path), "--json"])
    standard = json.loads(capsys.readouterr().out)
    main([str(tmp_path), "--json", "--level", "relaxed"])
    relaxed = json.loads(capsys.readouterr().out)

    assert standard["threshold"] * 3 == pytest.approx(relaxed["threshold"])
    assert standard["flagged_file_count"] == 1
    with pytest.raises(SystemExit):
        main([str(tmp_path), "--level", "extreme"])
