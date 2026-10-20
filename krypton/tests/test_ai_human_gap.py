"""The AI/rest structure comparison: attribution comes from commit trailers and blame, and the comparison is made inside size bands."""

import subprocess

from tools import ai_human_gap as gap

TANGLED = "def human(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(12)) + "    return None\n"
SIMPLE = "def helper(x):\n    y = x + 1\n    z = y * 2\n    w = z - 3\n    return w\n"


def run(repo, *args, env=None):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True, text=True, env=env)


def make_repo(tmp_path):
    run(tmp_path, "init", "-q")
    (tmp_path / "human.py").write_text(TANGLED, encoding="utf-8")
    run(tmp_path, "add", "-A")
    run(tmp_path, "commit", "-q", "-m", "hand written")
    (tmp_path / "ai.py").write_text(SIMPLE, encoding="utf-8")
    run(tmp_path, "add", "-A")
    run(tmp_path, "commit", "-q", "-m", "add helper\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>")
    return tmp_path


def test_commits_that_credit_an_assistant_are_found_and_others_are_not(tmp_path):
    repo = make_repo(tmp_path)
    found = gap.ai_commits(repo)
    assert len(found) == 1


def test_blame_attributes_each_line_to_the_commit_that_last_changed_it(tmp_path):
    repo = make_repo(tmp_path)
    ai = gap.ai_commits(repo)
    assert set(gap.blame_flags(repo, "ai.py", ai)) == {True}
    assert set(gap.blame_flags(repo, "human.py", ai)) == {False}


def test_collect_gives_each_function_its_ai_share_and_skips_tests(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "test_x.py").write_text(SIMPLE.replace("helper", "t"), encoding="utf-8")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "a test")
    rows = {r["fn"]: r for r in gap.collect(repo, ".")}
    assert set(rows) == {"human", "helper"}
    assert rows["helper"]["ai"] == 1 and rows["human"]["ai"] == 0


def test_the_report_compares_inside_size_bands():
    rows = [{"ai": 1.0, "lines": 10, "branches": 0, "over": False}] * 3 + [{"ai": 0.0, "lines": 70, "branches": 12, "over": True}] * 2
    rep = gap.report(rows)
    assert rep["ai_n"] == 3 and rep["rest_n"] == 2 and rep["ai_over"] == 0 and rep["rest_over"] == 100
    small, large = rep["bands"][0], rep["bands"][3]
    assert (small["ai_n"], small["rest_n"]) == (3, 0) and (large["ai_n"], large["rest_n"]) == (0, 2)
    assert "functions" in gap.render(rep)


def test_the_gap_interval_is_wide_when_there_are_few_functions():
    few = gap.bootstrap_gap([{"over": True}, {"over": False}], [{"over": False}, {"over": True}])
    many = gap.bootstrap_gap([{"over": i % 2 == 0} for i in range(200)], [{"over": i % 2 == 0} for i in range(200)])
    assert few[1] - few[0] > many[1] - many[0]
