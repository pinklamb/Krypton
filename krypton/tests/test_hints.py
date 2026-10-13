import json
import re

from krypton.cli import main
from krypton.engine.run import run_local
from tests.test_coupling import _hub_imports, _modules

NL = chr(10)


def _src(*lines: str) -> bytes:
    return (NL.join(lines) + NL).encode()


# Lines 2-8 are one loop holding every decision.
DEEP = _src(
    "def tangle(items, a, b):",
    "    for item in items:",
    "        if item > a:",
    "            for part in item:",
    "                if part > b:",
    "                    print(part)",
    "                if part < 0:",
    "                    print(-part)",
    "    return a",
    "",
)


def _hints(source: bytes, name: str = "m.py") -> dict[str, str]:
    return run_local({name: source}).hints


def test_deep_nest_names_the_block_and_suggests_early_returns():
    (key, text), = _hints(DEEP).items()

    assert key == "m.py:1 tangle"
    assert "deepest decision at line 5 (depth 4)" in text
    # The whole `for item` loop (2-8) is the function, so the hint points at the inner loop, 79% of the weight.
    assert "inside lines 4-8 which hold 79%" in text and "early returns" in text and "move lines 4-8 into a helper" in text


def test_a_long_run_of_conditions_suggests_a_table():
    chain = ["def pick(x):"] + [f"    if x == {i}:{NL}        x += {i}" for i in range(20)] + ["    return x", ""]

    text = next(iter(_hints(_src(*chain)).values()))

    assert "20 sequential conditions" in text and "lookup table" in text


def test_a_clean_file_gets_no_hints():
    assert _hints(_src("def add(a, b):", "    return a + b", "")) == {}


def test_hints_are_listed_heaviest_function_first():
    light = ["def light(x):", "    if x:", "        if x > 1:", "            if x > 2:", "                return 3", "    return 0"]
    source = _src(*light, "", *DEEP.decode().splitlines())

    keys = list(_hints(source))

    assert keys[0].endswith("tangle") and keys[1].endswith("light")


def test_applying_the_hint_lowers_the_rating():
    refactored = _src(
        "def inner(item, a, b):",
        "    if item > a:",
        "        for part in item:",
        "            if part > b:",
        "                print(part)",
        "            if part < 0:",
        "                print(-part)",
        "",
        "def tangle(items, a, b):",
        "    for item in items:",
        "        inner(item, a, b)",
        "    return a",
        "",
    )

    before = run_local({"m.py": DEEP}).findings[0]
    after = run_local({"m.py": refactored}).findings[0]

    tangle = next(f for f in after.functions if f.name == "tangle")
    assert after.logic_complexity_rating < before.logic_complexity_rating and not tangle.nested


def test_coupling_hint_names_the_partners():
    files = {**_modules(13), "hub.js": _hub_imports(13, 50)}

    text = run_local(files).hints["hub.js"]

    assert "tied to 13 files" in text and "m0.js" in text


def test_wrapper_hint_counts_the_wrappers():
    wrappers = NL.join(f"def w{i}(x):{NL}    return helper(x){NL}" for i in range(8))

    text = run_local({"w.py": wrappers.encode()}).hints["w.py"]

    assert "8 of 8 functions are trivial wrappers" in text


def test_cli_prints_hints_and_json_carries_them(tmp_path, capsys):
    (tmp_path / "m.py").write_bytes(DEEP)

    main([str(tmp_path), "--top", "5"])
    shown = capsys.readouterr().out
    main([str(tmp_path), "--json"])
    data = json.loads(capsys.readouterr().out)

    assert "Refactoring hints (top 1):" in shown and "m.py:1 tangle" in shown
    assert re.search(r"lines 4-8", data["hints"]["m.py:1 tangle"])
    assert "Refactoring hints" in data["summary_markdown"]


REFACTORED = _src(
    "def inner(item, b):",
    "    for part in item:",
    "        if part > b:",
    "            print(part)",
    "        if part < 0:",
    "            print(-part)",
    "",
    "def tangle(items, a, b):",
    "    for item in items:",
    "        if item > a:",
    "            inner(item, b)",
    "    return a",
    "",
)
INNER_BLOCK_MOVED = _src(
    "def tangle(items, a, b):",
    "    for item in items:",
    "        if item > a:",
    "            handle(item, a, b)",
    "    return a",
    "",
    "def handle(item, a, b):",
    "    for part in item:",
    "        if part > b:",
    "            print(part)",
    "        if part < 0:",
    "            print(-part)",
    "",
)


def test_prediction_matches_the_rating_after_the_extraction():
    text = _hints(DEEP)["m.py:1 tangle"]
    predicted = re.search(r"this function (\d+), helper (\d+)", text)
    actual = {f.name: f.rating_master for f in run_local({"m.py": REFACTORED}).findings[0].functions}

    assert (int(predicted.group(1)), int(predicted.group(2))) == (actual["tangle"], actual["inner"]) == (20, 54)
    assert "clears the flag" in text
