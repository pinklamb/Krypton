from argon.engine.config import DEFAULT_CONFIG, EngineConfig
from argon.engine.run import run_local

NL = chr(10)


def _switch(cases: int, distinct: bool = True) -> bytes:
    """A function with a `switch` of `cases` cases; every case returns a different value if `distinct`."""
    rows = ["function f(x) {", "  switch (x) {"]
    for i in range(cases):
        rows.append(f"    case {i}: " + (f"return {i};" if distinct else f"x += {i}; break;"))
    rows += ["  }", "  return x;", "}"]
    return NL.join(rows).encode()


def _flag(source: bytes, name: str = "m.js", cfg: EngineConfig = DEFAULT_CONFIG) -> tuple:
    finding = run_local({name: source}, cfg=cfg).findings[0]
    return finding.flagged, finding.flag_reason


def test_distinct_return_padding_cannot_hide_a_function_with_many_decisions():
    # 17 distinct-return cases dilute the rating under the threshold: the old rule let this through.
    assert _flag(_switch(17), cfg=EngineConfig(max_decisions_per_function=10**6, max_function_score=10**6, max_file_weight=10**6)) == (False, None)
    assert _flag(_switch(17)) == (False, None)  # under the floor of 30 too

    flagged, reason = _flag(_switch(30))
    assert flagged and reason in {"semantic_path_logic", "function_overload"}


def test_the_floor_flags_a_function_whatever_its_rating():
    lenient = EngineConfig(semantic_path_flag_threshold=10**9)  # the rating rule can never fire

    assert _flag(_switch(30), cfg=lenient) == (True, "function_overload")
    assert _flag(_switch(29), cfg=lenient) == (False, None)


def test_a_deeply_nested_function_with_few_decisions_hits_the_score_limit():
    nest = ["def deep(a):"]
    for depth in range(9):
        nest.append("    " * (depth + 1) + f"if a > {depth}:")
    nest.append("    " * 10 + "return a")
    lenient = EngineConfig(semantic_path_flag_threshold=10**9, max_function_score=40)

    assert _flag((NL.join(nest) + NL).encode(), "m.py", lenient) == (True, "function_overload")


def test_spreading_one_heavy_function_across_helpers_still_trips_the_file_budget():
    helpers = []
    for i in range(14):
        helpers += [f"def part{i}(x):"] + [f"    if x == {k}:{NL}        x += {k}" for k in range(8)] + ["    return x", ""]
    source = (NL.join(helpers) + NL).encode()
    lenient = EngineConfig(semantic_path_flag_threshold=10**9, max_decisions_per_function=10**6, max_function_score=10**6, max_file_weight=100)

    result = run_local({"m.py": source}, cfg=lenient)

    assert result.findings[0].nesting_score >= 100
    assert (result.findings[0].flagged, result.findings[0].flag_reason) == (True, "file_overload")
    assert "split the file by responsibility" in result.hints["m.py"]


def test_a_normal_function_is_not_flagged_by_any_new_rule():
    source = (NL.join(["def f(a, b):", "    if a:", "        return b", "    return a"]) + NL).encode()

    assert _flag(source, "m.py") == (False, None)


def test_hint_for_an_overloaded_function_names_the_limit_passed():
    lenient = EngineConfig(semantic_path_flag_threshold=10**9)

    text = run_local({"m.js": _switch(30)}, cfg=lenient).hints["m.js:1 f"]

    assert "30 decisions (limit 30)" in text


def test_priority_keeps_the_rating_flag_when_several_apply():
    flagged, reason = _flag(_switch(60, distinct=False))

    assert (flagged, reason) == (True, "semantic_path_logic")
