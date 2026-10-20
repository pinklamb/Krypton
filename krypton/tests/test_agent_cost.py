"""The agent harness: the tools stay inside the workspace, edits are exact, subprocesses never see the key, and the loop respects its budget."""

import sys
from types import SimpleNamespace

import pytest

from tools import agent_cost as ac
from tools.cache_anthropic import Budget


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text("def f():\n    return 1\n\n\ndef g():\n    return 1\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ignored.md").write_text("secret docs", encoding="utf-8")
    return ac.Workspace(tmp_path, sys.executable)


def test_paths_cannot_leave_the_workspace(ws):
    assert "outside" in ws.call("read_file", {"path": "../../etc/passwd"})
    assert "outside" in ws.call("write_file", {"path": "/tmp/x.py", "content": "x"})
    assert "outside" in ws.call("list_files", {"path": ".."})


def test_edit_needs_exactly_one_match(ws):
    assert "2 times" in ws.call("edit_file", {"path": "src/pkg/mod.py", "old": "return 1", "new": "return 2"})
    assert ws.call("edit_file", {"path": "src/pkg/mod.py", "old": "def f():\n    return 1", "new": "def f():\n    return 2"}) == "edited"
    assert "return 2" in ws.call("read_file", {"path": "src/pkg/mod.py"})
    assert "0 times" in ws.call("edit_file", {"path": "src/pkg/mod.py", "old": "absent", "new": "x"})


def test_listing_and_search_skip_ignored_folders(ws):
    assert "docs" not in ws.call("list_files", {})
    assert "no matches" in ws.call("grep", {"pattern": "secret"})
    assert "mod.py:1" in ws.call("grep", {"pattern": "def f"})


def test_read_numbers_lines_and_can_slice(ws):
    assert ws.call("read_file", {"path": "src/pkg/mod.py", "start_line": 5, "end_line": 6}).splitlines()[0].strip().startswith("5")


def test_subprocesses_never_see_the_key(ws, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    assert "sk-ant" not in ws.run([sys.executable, "-c", "import os; print(dict(os.environ))"], "src")


def test_unknown_tool_and_bad_arguments_are_reported_not_raised(ws):
    assert ws.call("read_file", {"nope": 1}).startswith("error")
    assert ws.call("no_such_tool", {}).startswith("error")


class Reply:
    def __init__(self, blocks, stop):
        self.content, self.stop_reason = blocks, stop
        self.usage = SimpleNamespace(model_dump=lambda: {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 50})


class ScriptedClient:
    """Asks for one tool call, then says DONE."""

    def __init__(self):
        self.calls, self.messages = 0, SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return Reply([SimpleNamespace(type="tool_use", name="read_file", id="t1", input={"path": "src/pkg/mod.py"})], "tool_use")
        return Reply([SimpleNamespace(type="text", text="DONE: read the file")], "end_turn")


def test_agent_loop_runs_tools_and_totals_usage(ws):
    run = ac.agent_run(ScriptedClient(), ws, "system", "ticket", ac.TOOLS, Budget(5), "A/test", 10)
    totals = ac.usage_total(run)
    assert run.finished and run.tool_calls == {"read_file": 1} and totals["requests"] == 2
    assert totals["cache_read_input_tokens"] == 200 and totals["cached_share"] == pytest.approx(200 / 320)


def test_agent_loop_stops_before_the_budget_is_passed(ws):
    run = ac.agent_run(ScriptedClient(), ws, "system", "ticket", ac.TOOLS, Budget(0.0001), "A/test", 10)
    assert run.stop.startswith("budget") and run.requests == []


def test_turn_limit_ends_a_run_that_never_finishes(ws):
    class Endless(ScriptedClient):
        def _create(self, **kwargs):
            return Reply([SimpleNamespace(type="tool_use", name="list_files", id="t", input={})], "tool_use")

    run = ac.agent_run(Endless(), ws, "system", "ticket", ac.TOOLS, Budget(5), "A/test", 3)
    assert run.stop == "turn limit" and len(run.requests) == 3


def test_neutral_krypton_arm_gets_the_tool_without_refactoring_instructions():
    assert "run_krypton" in ac.KRYPTON_NEUTRAL
    for word in ("modular", "helper", "refactor", "early return", "nesting"):
        assert word not in ac.KRYPTON_NEUTRAL.lower()
