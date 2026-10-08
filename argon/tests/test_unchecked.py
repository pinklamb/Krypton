import json
import re

from argon.cli import main
from argon.engine.config import EngineConfig
from argon.engine.run import run_local
from tests.test_cli import PASSING, _write

BIG = b"const x = 1;\n" * 160_000  # ~2 MB, over the default 1 MB

FUNCTIONS = "".join(f"function f{i}() {{ return {i}; }}" + chr(10) for i in range(45_000)).encode()  # ~1.4 MB, 45k top-level nodes
BRANCHY = ("def tangle(x):" + chr(10) + "".join(f"    if x == {i}:" + chr(10) + f"        x += {i}" + chr(10) for i in range(30)) + "    return x" + chr(10)).encode()


def test_score_leaves_unchecked_files_out():
    with_big = run_local({"big.js": BIG, "ok.py": PASSING.encode()})
    without = run_local({"ok.py": PASSING.encode()})

    assert with_big.purity_score == without.purity_score == 100
    assert [f.filename for f in with_big.findings] == ["ok.py"]
    assert list(with_big.errors) == ["E_FILE_TOO_LARGE"] and without.errors == {}


def test_errors_are_a_code_to_explanation_map_one_entry_per_code():
    files = {f"big{i}.js": BIG for i in range(7)}

    errors = run_local(files).errors

    assert list(errors) == ["E_FILE_TOO_LARGE", "E_NOTHING_CHECKED"]
    assert "big0.js" in errors["E_FILE_TOO_LARGE"] and "and 2 more" in errors["E_FILE_TOO_LARGE"]  # five named, rest counted
    assert "means nothing" in errors["E_NOTHING_CHECKED"]


def test_other_files_still_fail_when_a_big_file_is_unchecked():
    result = run_local({"big.js": BIG, "bad.py": BRANCHY})

    assert result.flagged_file_count == 1 and result.purity_score < 100


def test_applying_the_stated_fix_checks_the_file():
    explanation = run_local({"fns.js": FUNCTIONS}).errors["E_FILE_TOO_LARGE"]
    limit = int(re.search(r"max_file_bytes = (\d+)", explanation).group(1))

    again = run_local({"fns.js": FUNCTIONS}, cfg=EngineConfig(max_file_bytes=limit))

    assert again.errors == {} and [f.filename for f in again.findings] == ["fns.js"]


def test_a_file_over_both_limits_gets_both_codes():
    both = b"var a=[" + b"1," * 600_000 + b"];\n"

    errors = run_local({"both.js": both}).errors

    assert {"E_FILE_TOO_LARGE", "E_LINE_TOO_LONG"} <= set(errors)


def test_summary_lists_the_codes_and_says_the_score_excludes_them():
    markdown = run_local({"big.js": BIG, "ok.py": PASSING.encode()}).summary_markdown

    assert "does not include these files" in markdown and "`E_FILE_TOO_LARGE`" in markdown


def test_cli_warns_and_json_has_the_map(tmp_path, capsys):
    # A tiny limit instead of a multi-megabyte file on disk.
    config = _write(tmp_path, "argon.toml", "max_file_bytes = 100" + chr(10))
    _write(tmp_path, "src/big.py", PASSING * 20)
    _write(tmp_path, "src/ok.py", "x = 1" + chr(10))

    main([str(tmp_path / "src"), "--top", "0", "--config", str(config)])
    err = capsys.readouterr().err
    main([str(tmp_path / "src"), "--json", "--config", str(config)])
    data = json.loads(capsys.readouterr().out)

    assert "WARN E_FILE_TOO_LARGE" in err and "big.py" in err
    assert list(data["errors"]) == ["E_FILE_TOO_LARGE"] and [f["filename"] for f in data["findings"]] == ["ok.py"]
