"""Waivers, --no-worse and --handoff-stalled: ways the gate lets a function through instead of looping an agent on it."""

import re

from krypton.cli import main
from krypton.engine.config import DEFAULT_CONFIG
from krypton.engine.run import run_local
from krypton.schemas import EngineResult
from tests.test_cli import FAILING, _git, _repo, _write

CFG = DEFAULT_CONFIG.model_copy(update={"semantic_path_flag_threshold": 40.0})
T40 = "semantic_path_flag_threshold = 40\n"
RENAMED = re.sub(r"\b([a-f])\b", lambda match: "pqrstu"["abcdef".index(match.group(1))], FAILING)   # same structure, every line different
WORSE = FAILING + "    if a and b:\n        pass\n    if c and d:\n        pass\n"


def score(source: str, name: str = "t.py", **kwargs):
    return run_local({name: source.encode()}, None, kwargs.pop("cfg", CFG), **kwargs)


def exempt(result):
    return {function.name: function.exempt_reason for finding in result.findings for function in finding.functions if function.exempt_reason}


def test_a_function_over_the_limit_is_flagged_without_a_waiver():
    assert score(FAILING).flagged_file_count == 1


def test_a_waiver_comment_above_the_function_lets_it_through_and_says_why():
    result = score("# krypton: ignore -- legacy parser, rewrite planned\n" + FAILING)
    assert result.flagged_file_count == 0 and result.check_conclusion == "success"
    assert exempt(result) == {"tangle": "waived: legacy parser, rewrite planned"}
    assert "Exempt from the gate: t.py:2 tangle (waived: legacy parser, rewrite planned)" in result.summary_markdown


def test_a_waiver_without_a_reason_still_works_and_says_so():
    assert exempt(score("# krypton: ignore\n" + FAILING)) == {"tangle": "waived (no reason given)"}


def test_a_waiver_on_the_definition_line_and_in_javascript_both_count():
    assert exempt(score(FAILING.replace("f):", "f):  # krypton: ignore -- generated")))["tangle"] == "waived: generated"
    js = "// krypton: ignore -- vendor shim\nfunction tangle(a, b, c, d, e, f) {\n" + "".join(f"  if ({v}) {{ console.log({v}); }}\n" for v in "abcdef") + "}\n"
    assert exempt(score(js, "t.js"))["tangle"] == "waived: vendor shim"


def test_a_waiver_that_is_not_next_to_the_function_does_not_count():
    assert exempt(score("# krypton: ignore\n\n" + FAILING)) == {}                                   # a blank line separates it
    assert exempt(score("x = 1  # krypton: ignore\n" + FAILING)) == {}                              # a trailing comment on another statement


def test_waivers_can_be_turned_off():
    off = CFG.model_copy(update={"allow_waivers": False})
    assert score("# krypton: ignore -- reason\n" + FAILING, cfg=off).flagged_file_count == 1


def test_exemptions_given_by_the_caller_are_recorded_and_not_scored():
    result = score(FAILING, exemptions={("t.py", "tangle"): "no worse than main"})
    assert exempt(result) == {"tangle": "no worse than main"} and result.flagged_file_count == 0


def _committed(tmp_path, text):
    _repo(tmp_path)
    _write(tmp_path, "t.py", text)
    _write(tmp_path, "t40.toml", T40)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")


def _run(tmp_path, capsys, *flags):
    code = main([str(tmp_path), "--base", "HEAD", "--config", str(tmp_path / "t40.toml"), "--json", *flags])
    captured = capsys.readouterr()
    assert captured.out, captured.err                                                              # a usage error prints nothing on stdout
    return code, EngineResult.model_validate_json(captured.out)


def test_no_worse_lets_an_untouched_in_substance_function_through(tmp_path, capsys):
    _committed(tmp_path, FAILING)
    _write(tmp_path, "t.py", RENAMED)                                                              # every line changed, same structure
    assert _run(tmp_path, capsys)[0] == 1
    code, result = _run(tmp_path, capsys, "--no-worse")
    assert code == 0 and exempt(result) == {"tangle": "no worse than HEAD"}


def test_no_worse_does_not_let_a_heavier_function_through(tmp_path, capsys):
    _committed(tmp_path, FAILING)
    _write(tmp_path, "t.py", WORSE)
    code, result = _run(tmp_path, capsys, "--no-worse")
    assert code == 1 and exempt(result) == {}


def test_a_function_that_is_new_has_no_base_and_is_not_let_through(tmp_path, capsys):
    _committed(tmp_path, "x = 1\n")
    _write(tmp_path, "t.py", FAILING)
    assert _run(tmp_path, capsys, "--no-worse")[0] == 1


def test_the_gate_flags_name_the_option_they_need(tmp_path, capsys):
    _write(tmp_path, "t.py", FAILING)
    assert main([str(tmp_path), "--no-worse"]) == 2
    assert "--base" in capsys.readouterr().err
    assert main([str(tmp_path), "--handoff-stalled"]) == 2
    assert "--history" in capsys.readouterr().err


def test_a_stalled_function_is_handed_off_instead_of_blocking_again(tmp_path, capsys):
    _write(tmp_path, "t.py", FAILING)
    config, log = str(_write(tmp_path, "t40.toml", T40)), str(tmp_path / "runs.jsonl")
    common = [str(tmp_path), "--config", config, "--history", log, "--json"]
    codes = [main(common) for _ in range(4)]                                                       # nothing improves: stalled on the fourth run
    capsys.readouterr()
    assert codes == [1, 1, 1, 1]
    code = main([*common, "--handoff-stalled"])
    result = EngineResult.model_validate_json(capsys.readouterr().out)
    assert code == 0 and "handed off" in exempt(result)["tangle"]
