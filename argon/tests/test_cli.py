import subprocess

import pytest

from argon.cli import main
from argon.schemas import EngineResult

# rating_master = branches² × altered lines ÷ outputs: 6² × 13 ÷ 1 = 468, far over 40.
FAILING = """\
def tangle(a, b, c, d, e, f):
    if a:
        if b:
            if c:
                pass
    if d:
        pass
    if e:
        pass
    if f:
        pass
    print(a)
    print(b)
"""
PASSING = "def add(a, b):\n    return a + b\n"


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "argon@example.com")
    _git(root, "config", "user.name", "Krypton")


def test_exit_codes(tmp_path, capsys):
    _write(tmp_path, "bad/tangle.py", FAILING)
    _write(tmp_path, "good/add.py", PASSING)
    t40 = str(_write(tmp_path, "t40.toml", "semantic_path_flag_threshold = 40\n"))

    assert main([str(tmp_path / "bad"), "--top", "0", "--config", t40]) == 1
    assert main([str(tmp_path / "good"), "--top", "0", "--config", t40]) == 0
    assert main([str(tmp_path / "missing_folder")]) == 2
    assert main([]) == 2


def test_fit_thresholds_writes_calibrated_module(tmp_path, capsys, monkeypatch):
    import argon.cli

    monkeypatch.setattr(argon.cli, "CALIBRATED_MODULE", tmp_path / "calibrated.py")
    header = "filename,name,rating_master,rating_nesting\n"
    human = _write(tmp_path, "human.csv", header + "h.py,f,10,5\nh.py,g,20,8\n")
    agentic = _write(tmp_path, "agentic.csv", header + "a.py,f,500,90\na.py,g,30,9\n")

    assert main(["--fit-thresholds", str(human), str(agentic)]) == 0

    namespace = {}
    exec((tmp_path / "calibrated.py").read_text(), namespace)
    # Human tops out at 20 / 8 and every agentic function is above that: perfect separation.
    assert (namespace["SEMANTIC_PATH_FLAG_THRESHOLD"], namespace["NESTING_FLAG_THRESHOLD"]) == (20.0, 8.0)
    assert namespace["RATING_MODE"] == "master"


def test_json_round_trips_into_engine_result(tmp_path, capsys):
    _write(tmp_path, "add.py", PASSING)

    main([str(tmp_path), "--json"])
    result = EngineResult.model_validate_json(capsys.readouterr().out)

    assert result.purity_score == 100
    assert [finding.filename for finding in result.findings] == ["add.py"]


def test_ranking_shows_nested_flag(tmp_path, capsys):
    _write(tmp_path, "tangle.py", FAILING)

    main([str(tmp_path), "--top", "5"])
    out = capsys.readouterr().out

    assert "nested" in out
    assert "tangle.py:1  tangle" in out
    assert "### Krypton Structural Review -- Purity Score:" in out


def test_unknown_config_key_is_a_usage_error(tmp_path, capsys):
    _write(tmp_path, "add.py", PASSING)
    config = _write(tmp_path, "argon.toml", "line_treshold = 10\n")

    assert main([str(tmp_path), "--config", str(config)]) == 2
    assert "line_treshold" in capsys.readouterr().err


def test_config_overrides_engine_fields(tmp_path, capsys):
    _write(tmp_path, "tangle.py", FAILING)
    config = _write(tmp_path, "argon.toml", "semantic_path_flag_threshold = 100000\n")

    assert main([str(tmp_path), "--config", str(config), "--top", "0"]) == 0


def test_tsconfig_aliases_are_discovered(tmp_path, capsys):
    _write(tmp_path, "tsconfig.json", '{\n  // JSONC\n  "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"],},},\n}\n')
    _write(tmp_path, "src/a.ts", 'import { b } from "@/b";\n')
    _write(tmp_path, "src/b.ts", "export const b = 1;\n")

    main([str(tmp_path), "--json"])
    result = EngineResult.model_validate_json(capsys.readouterr().out)

    imports = {finding.filename: finding.relative_import_count for finding in result.findings}
    assert imports == {"src/a.ts": 1, "src/b.ts": 0}


def test_git_repo_respects_gitignore(tmp_path, capsys):
    _repo(tmp_path)
    _write(tmp_path, ".gitignore", "ignored/\n")
    _write(tmp_path, "src/app.py", PASSING)
    _write(tmp_path, "ignored/junk.py", FAILING)

    main([str(tmp_path), "--json"])
    result = EngineResult.model_validate_json(capsys.readouterr().out)

    assert [finding.filename for finding in result.findings] == ["src/app.py"]


def test_non_git_folder_warns_and_scans_everything(tmp_path, capsys):
    _write(tmp_path, "a.py", PASSING)
    _write(tmp_path, "sub/b.py", PASSING)

    main([str(tmp_path), "--json"])
    captured = capsys.readouterr()

    assert ".gitignore not applied" in captured.err
    assert len(EngineResult.model_validate_json(captured.out).findings) == 2


def test_base_scores_only_changed_lines(tmp_path, capsys):
    _repo(tmp_path)
    _write(tmp_path, "keep.py", PASSING)
    _write(tmp_path, "edit.py", PASSING)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _write(tmp_path, "edit.py", PASSING + "\ndef sub(a, b):\n    return a - b\n")
    _write(tmp_path, "new.py", PASSING)

    main([str(tmp_path), "--base", "HEAD", "--json"])
    result = EngineResult.model_validate_json(capsys.readouterr().out)

    altered = {finding.filename: finding.lines_altered for finding in result.findings}
    assert altered == {"edit.py": 3, "new.py": 2}  # keep.py is unchanged; new.py is fully altered


def test_symlink_leaving_the_root_is_dropped(tmp_path, capsys):
    outside = _write(tmp_path, "outside/secret.py", PASSING)
    _write(tmp_path, "root/app.py", PASSING)
    try:
        (tmp_path / "root" / "link.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks need extra privileges on this system")

    main([str(tmp_path / "root"), "--json"])
    result = EngineResult.model_validate_json(capsys.readouterr().out)

    assert [finding.filename for finding in result.findings] == ["app.py"]


def test_added_line_starting_with_plus_plus_is_not_a_header():
    from argon.cli import _added_lines

    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "@@ -1,0 +2,3 @@\n+x = 1\n+++ step one\n+y = 2\n"
        "@@ -9 +11 @@\n-old\n+new\n\\ No newline at end of file\n"
    )

    assert _added_lines(diff) == {"a.py": {2, 3, 4, 11}}


def test_diff_paths_with_spaces_and_quotes_are_decoded():
    from argon.cli import _added_lines

    diff = (
        "diff --git a/my file.py b/my file.py\n--- a/my file.py\t\n+++ b/my file.py\t\n@@ -0,0 +1 @@\n+x\n"
        'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n--- "a/caf\\303\\251.py"\n+++ "b/caf\\303\\251.py"\n@@ -0,0 +1 @@\n+x\n'
        "diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
    )

    assert _added_lines(diff) == {"my file.py": {1}, "café.py": {1}}


def test_base_keeps_files_with_spaces_and_non_ascii_names(tmp_path, capsys):
    _repo(tmp_path)
    for name in ("space name.py", "café.py", "plain.py"):
        _write(tmp_path, name, PASSING)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    for name in ("space name.py", "café.py"):
        _write(tmp_path, name, PASSING + "\ndef sub(a, b):\n    return a - b\n")

    main([str(tmp_path), "--base", "HEAD", "--json"])
    result = EngineResult.model_validate_json(capsys.readouterr().out)

    altered = {finding.filename: finding.lines_altered for finding in result.findings}
    assert altered == {"space name.py": 3, "café.py": 3}
