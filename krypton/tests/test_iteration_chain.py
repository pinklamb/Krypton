"""The iteration chain: what the CI gate flags, the fix rounds it asks for, and the whole flow against a scripted model."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import agent_cost as ac
from tools import iteration_chain as ic
from tools.cache_anthropic import Budget


def test_the_gate_flags_only_touched_functions_over_the_limit():
    before = {("a.py", "old_tangle"): 5000.0, ("a.py", "small"): 10.0, ("a.py", "edited"): 700.0}
    after = {("a.py", "old_tangle"): 5000.0, ("a.py", "small"): 10.0, ("a.py", "edited"): 1296.0, ("a.py", "brand_new"): 900.0, ("a.py", "new_but_fine"): 50.0}
    flagged = ic.gate_offenders(before, after, 648.0)
    assert [(name, rating) for _, name, rating in flagged] == [("edited", 1296.0), ("brand_new", 900.0)]   # worst first; the old untouched tangle is not blamed


def test_the_gate_stays_quiet_when_nothing_touched_is_over_the_limit():
    assert ic.gate_offenders({("a.py", "f"): 100.0}, {("a.py", "f"): 120.0}, 648.0) == []


def test_the_failure_report_names_the_function_and_how_far_over_it_is():
    text = ic.gate_text([("src/a.py", "Thing.get", 1296.0)], 648.0)
    assert "src/a.py: Thing.get (2.0x over the limit)" in text and "Every existing test must keep passing" in text


def test_the_worst_function_can_be_read_for_the_whole_tree_or_one_file():
    found = {("a.py", "x"): 648.0, ("b.py", "y"): 6480.0}
    assert ic.worst_over(found, 648.0) == 10.0 and ic.worst_over(found, 648.0, "a.py") == 1.0 and ic.worst_over({}, 648.0) == 0.0


def test_the_default_chain_is_twenty_revisions_that_all_have_tickets_and_hidden_tests():
    assert len(ic.CHAIN) == 20 and ic.CHAIN[:3] == [2, 7, 8] and ic.CHAIN[-1] == 25
    hidden = (Path(ac.__file__).parent / "garth_acceptance" / "hidden_tests.py").read_text(encoding="utf-8")
    assert all(n in ac.TICKETS and f"def test_t{n}_" in hidden for n in ic.CHAIN)


class Reply:
    def __init__(self, blocks, stop):
        self.content, self.stop_reason = blocks, stop
        self.usage = SimpleNamespace(model_dump=lambda: {"input_tokens": 5, "output_tokens": 5, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 20})


class Scripted:
    """Every agent run: one read_file, then DONE. Remembers the first message of every run."""

    def __init__(self):
        self.prompts, self.messages = [], SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        if len(kwargs["messages"]) == 1:
            self.prompts.append(kwargs["messages"][0]["content"])
            return Reply([SimpleNamespace(type="tool_use", name="read_file", id="t", input={"path": "src/c.py"})], "tool_use")
        return Reply([SimpleNamespace(type="text", text="DONE")], "end_turn")


@pytest.fixture
def world(tmp_path):
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "src" / "c.py").write_text("VALUE = 1\n", encoding="utf-8")
    return SimpleNamespace(source=source, root=tmp_path)


def args_for(world, tasks, arms=("plain", "gate"), **extra):
    return SimpleNamespace(tasks=tasks, arms=list(arms), workspaces=world.root / "ws", source=world.source, python=Path(sys.executable), hidden=Path("unused"), max_fixes=2,
                           out=world.root / "out.json", max_usd=5, **extra)


def script_ratings(monkeypatch, sequence):
    """ratings() returns the next scripted tree each call (the last one repeats)."""
    calls = iter(sequence)
    last = {}

    def fake(workspace):
        nonlocal last
        last = next(calls, last)
        return last, 648.0
    monkeypatch.setattr(ic, "ratings", fake)


def test_the_gate_arm_sends_an_agent_back_until_the_function_is_fixed(world, monkeypatch):
    monkeypatch.setattr(ac, "check_work", lambda workspace, hidden: {"suite_ok": True})
    f = ("src/c.py", "grow")
    # run_chain: start, then per ticket: before, after the ticket, after fix 1, after fix 2 ...
    script_ratings(monkeypatch, [{f: 100.0}, {f: 100.0}, {f: 2000.0}, {f: 1500.0}, {f: 300.0}])
    client, results = Scripted(), {}
    ic.run_chain("gate", 0, client, args_for(world, [2]), Budget(5), results)
    ticket = results["gate0"]["tickets"][0]
    assert ticket["gate"] == {"offenders_after_ticket": 1, "fixes": 2, "offenders_left": 0}
    assert [s["kind"] for s in ticket["stages"]] == ["ticket", "ci_fix", "ci_fix"] and len(client.prompts) == 3
    assert "CI check failed" in client.prompts[1] and "grow" in client.prompts[1]
    assert ticket["totals"]["cost"] == pytest.approx(sum(s["totals"]["cost"] for s in ticket["stages"]))


def test_the_plain_arm_never_gets_fix_rounds(world, monkeypatch):
    monkeypatch.setattr(ac, "check_work", lambda workspace, hidden: {})
    f = ("src/c.py", "grow")
    script_ratings(monkeypatch, [{f: 100.0}, {f: 100.0}, {f: 5000.0}])
    client, results = Scripted(), {}
    ic.run_chain("plain", 0, client, args_for(world, [2]), Budget(5), results)
    ticket = results["plain0"]["tickets"][0]
    assert ticket["gate"]["fixes"] == 0 and ticket["gate"]["offenders_left"] == 1 and len(client.prompts) == 1


def test_the_gate_gives_up_after_the_most_fix_rounds(world, monkeypatch):
    monkeypatch.setattr(ac, "check_work", lambda workspace, hidden: {})
    f = ("src/c.py", "grow")
    script_ratings(monkeypatch, [{f: 100.0}, {f: 100.0}, {f: 9000.0}])   # never improves
    client, results = Scripted(), {}
    ic.run_chain("gate", 0, client, args_for(world, [2]), Budget(5), results)
    assert results["gate0"]["tickets"][0]["gate"] == {"offenders_after_ticket": 1, "fixes": 2, "offenders_left": 1}


def test_every_ticket_is_saved_with_its_measurements_and_the_file_has_no_secrets(world, monkeypatch):
    monkeypatch.setattr(ac, "check_work", lambda workspace, hidden: {"hidden_t2": True, "suite_ok": True})
    script_ratings(monkeypatch, [{("src/c.py", "f"): 10.0}])
    results = {}
    ic.run_chain("plain", 0, Scripted(), args_for(world, [2, 7]), Budget(5), results)
    ic.save(args_for(world, [2, 7]), results, Budget(5))
    saved = json.loads((world.root / "out.json").read_text(encoding="utf-8"))
    tickets = saved["chains"]["plain0"]["tickets"]
    assert [t["ticket"] for t in tickets] == [2, 7] and {"after", "check", "worst_x", "feature_worst_x", "totals"} <= set(tickets[0])
    assert "sk-ant" not in json.dumps(saved)


def test_the_chain_stops_when_the_budget_would_be_passed(world, monkeypatch):
    monkeypatch.setattr(ac, "check_work", lambda workspace, hidden: {})
    script_ratings(monkeypatch, [{}])
    with pytest.raises(ic.BudgetExceeded):
        ic.run_chain("plain", 0, Scripted(), args_for(world, [2, 7]), Budget(0.0001), {})
