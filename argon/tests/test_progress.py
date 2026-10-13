import json

from argon.cli import main
from argon.engine.config import EngineConfig
from argon.progress import compare
from argon.runlog import Snapshot
from tests.test_hints import DEEP, _src

NL = chr(10)


def _run(tmp_path, capsys, source: bytes) -> dict:
    """One agent iteration: the file is edited, then Krypton runs with --history."""
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "m.py").write_bytes(source)
    main([str(tmp_path / "src"), "--json", "--history", str(tmp_path / "log" / "history.jsonl")])
    return json.loads(capsys.readouterr().out)["progress"]


def test_first_run_starts_the_history(tmp_path, capsys):
    progress = _run(tmp_path, capsys, DEEP)

    assert list(progress) == ["purity"] and "history started" in progress["purity"]
    assert (tmp_path / "log" / "history.jsonl").exists()


def test_an_untouched_function_is_reported_as_not_edited(tmp_path, capsys):
    _run(tmp_path, capsys, DEEP)

    progress = _run(tmp_path, capsys, DEEP)

    assert progress["m.py:tangle"].startswith("unchanged 225 -> 225")
    assert "not edited since the last run" in progress["m.py:tangle"]


def test_edits_outside_the_hinted_block_are_called_out_and_the_stall_is_counted(tmp_path, capsys):
    _run(tmp_path, capsys, DEEP)
    elsewhere = DEEP.replace(b"    return a", b"    return a + 0")
    _run(tmp_path, capsys, elsewhere)

    progress = _run(tmp_path, capsys, elsewhere + NL.encode())
    progress = _run(tmp_path, capsys, elsewhere.replace(b"a + 0", b"a + 1"))

    text = progress["m.py:tangle"]
    assert text.startswith("STALLED for 3 runs at 225")
    assert "still intact; the edits were elsewhere" in text


def _block(var: str, indent: str = "    ") -> list[str]:
    """A loop with three conditions nested two deep (seven decisions, nine lines)."""
    lines = [
        f"for {var} in items:",
        f"    if {var} > a:",
        f"        for part in {var}:",
        "            if part > b:",
        "                print(part)",
        "            if part < 0:",
        "                print(-part)",
        "            if part == 3:",
        "                print(3)",
    ]
    return [indent + line for line in lines]


def _heavy(blocks: list[list[str]], tail: list[str] | None = None) -> bytes:
    """tangle() made of the given blocks, plus optional extra top-level lines."""
    return _src("def tangle(items, a, b):", *[line for block in blocks for line in block], "    return a", *(tail or []), "")


THREE = [_block("x"), _block("y"), _block("z")]


def test_applying_the_hint_shows_the_improvement_and_the_purity_trend(tmp_path, capsys):
    first = _run(tmp_path, capsys, _heavy(THREE))
    helper = ["", "def helper(items, a, b):", *[line[4:] for line in THREE[0]]]
    second = _run(tmp_path, capsys, _heavy([[*THREE[1]], [*THREE[2]], ["    helper(items, a, b)"]], helper))

    assert "history started" in first["purity"]
    assert second["m.py:tangle"].startswith("improved") and "lower" in second["m.py:tangle"]
    assert second["purity"].startswith("run 2: purity")


def test_nested_named_function_counts_as_real_progress(tmp_path, capsys):
    # A named inner function gets its own row, so the parent's rating drops (the isolation rule).
    _run(tmp_path, capsys, _heavy(THREE))
    nested = ["    def inner():", "        nonlocal a", *["    " + line for line in THREE[0]], "    inner()"]

    progress = _run(tmp_path, capsys, _heavy([nested, THREE[1], THREE[2]]))

    assert progress["m.py:tangle"].startswith("improved")


def test_block_found_in_another_function_is_reported_as_moved():
    hashes = ["a1", "b2", "c3", "d4"]
    before = {"rating": 900.0, "body": "old"}
    current = {"rating": 880.0, "body": "new"}
    previous = {"functions": {"m.py:f": before}, "regions": {"m.py:f": {"lines": [4, 8], "hashes": hashes}}, "purity": 50, "flagged": 1}
    now = {"purity": 50, "flagged": 1, "functions": {"m.py:f": current, "m.py:g": {"rating": 700.0, "body": "x"}}, "regions": {}}
    snapshot = Snapshot(now, {"m.py:f": {"z9"}, "m.py:g": set(hashes) | {"q"}})

    text = compare(snapshot, [previous], 2, EngineConfig())["m.py:f"]

    assert text.startswith("unchanged 900 -> 880")
    assert "moved into g" in text and "weight moved instead of shrinking" in text


def test_a_changed_block_that_did_not_help_points_at_the_new_hint():
    before = {"rating": 900.0, "body": "old"}
    previous = {"functions": {"m.py:f": before}, "regions": {"m.py:f": {"lines": [4, 8], "hashes": ["a1", "b2"]}}, "purity": 50, "flagged": 1}
    now = {"purity": 50, "flagged": 1, "functions": {"m.py:f": {"rating": 950.0, "body": "new"}}, "regions": {}}

    text = compare(Snapshot(now, {"m.py:f": {"z9"}}), [previous], 2, EngineConfig())["m.py:f"]

    assert text.startswith("unchanged 900 -> 950") and "changed but the rating did not drop" in text


def test_the_log_keeps_names_and_hashes_but_no_source(tmp_path, capsys):
    _run(tmp_path, capsys, DEEP)

    log = (tmp_path / "log" / "history.jsonl").read_text(encoding="utf-8")

    assert "tangle" in log and "print(part)" not in log


def test_a_damaged_log_line_is_skipped(tmp_path, capsys):
    _run(tmp_path, capsys, DEEP)
    path = tmp_path / "log" / "history.jsonl"
    path.write_text("not json" + NL + path.read_text(encoding="utf-8"), encoding="utf-8")

    progress = _run(tmp_path, capsys, DEEP)

    assert "run 2" in progress["purity"]
