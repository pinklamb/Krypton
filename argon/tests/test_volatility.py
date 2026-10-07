import json

import pytest

from argon.cli import main
from argon.engine.volatility import change_frequency, manifest, order_by_volatility
from argon.engine.run import run_local
from tests.test_cli import PASSING, _git, _repo, _write


def test_frequency_is_relative_to_the_busiest_scanned_file():
    result = change_frequency({"a.py": 1, "b.py": 4, "gone.py": 99}, ["a.py", "b.py", "c.py"])

    assert result == {"a.py": 0.25, "b.py": 1.0, "c.py": 0.0}  # gone.py is not scanned, so it does not set the scale


def test_nothing_changed_is_all_zero_and_in_flux_is_one():
    assert change_frequency({}, ["a.py"]) == {"a.py": 0.0}
    assert change_frequency({"a.py": 3}, ["a.py", "new.py"], in_flux={"new.py"}) == {"a.py": 1.0, "new.py": 1.0}


def test_order_is_stable_first_with_name_tiebreak():
    assert order_by_volatility({"z.py": 0.0, "a.py": 0.5, "b.py": 0.0}) == ["b.py", "z.py", "a.py"]


def test_run_local_records_frequency_without_changing_scores():
    files = {"a.py": PASSING.encode()}

    plain = run_local(files)
    tagged = run_local(files, change_frequency={"a.py": 0.4})

    assert tagged.findings[0].change_frequency == 0.4 and plain.findings[0].change_frequency is None
    assert tagged.purity_score == plain.purity_score
    assert manifest(tagged.findings, {"a.py": 70})["files"][0]["tokens_estimate"] == 20


def test_cli_manifest_orders_files_by_git_history(tmp_path, capsys):
    _repo(tmp_path)
    _write(tmp_path, "stable.py", PASSING)
    _write(tmp_path, "busy.py", PASSING)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    for turn in range(3):
        _write(tmp_path, "busy.py", PASSING + f"x = {turn}\n")
        _git(tmp_path, "commit", "-q", "-am", f"edit {turn}")
    _write(tmp_path, "fresh.py", PASSING)  # untracked: in flux

    assert main([str(tmp_path), "--order-by-volatility"]) == 0
    files = json.loads(capsys.readouterr().out)["files"]

    assert [f["filename"] for f in files] == ["stable.py", "busy.py", "fresh.py"]
    assert [f["change_frequency"] for f in files] == [0.25, 1.0, 1.0]


def test_cli_volatility_file_replaces_git(tmp_path, capsys):
    _write(tmp_path, "src/a.py", PASSING)
    _write(tmp_path, "src/b.py", PASSING)
    counts = _write(tmp_path, "counts.json", json.dumps({"a.py": 9, "b.py": 3}))

    main([str(tmp_path / "src"), "--order-by-volatility", "--volatility", str(counts)])
    files = json.loads(capsys.readouterr().out)["files"]

    assert [f["filename"] for f in files] == ["b.py", "a.py"]


def test_cli_manifest_without_history_is_a_usage_error(tmp_path, capsys):
    _write(tmp_path, "a.py", PASSING)

    assert main([str(tmp_path), "--order-by-volatility"]) == 2
    assert "volatility" in capsys.readouterr().err
