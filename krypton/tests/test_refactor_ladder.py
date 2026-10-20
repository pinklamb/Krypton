"""The refactor ladder: which files the targeted cleanup takes, identical branching, and the whole flow against a scripted model."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import agent_cost as ac
from tools import refactor_ladder as rl
from tools.cache_anthropic import Budget


def heavy(name: str) -> str:
    lines, indent = [f"def {name}(items, flag):", "    out = []"], "    "
    for level in range(4):
        lines += [f"{indent}for x{level} in items:", f"{indent}    if x{level} and flag > {level}:"]
        indent += "        "
    return "\n".join(lines + [f"{indent}out.append(x0)", "    return out", ""])


@pytest.fixture
def world(tmp_path):
    """A git history where c.py is changed most but is clean, a.py next, b.py once; and a source tree to copy."""
    history = tmp_path / "history"
    (history / "src").mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", "-C", str(history), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True, capture_output=True)

    git("init", "-q")
    for number in range(5):
        (history / "src" / "c.py").write_text(f"VALUE = {number}\n", encoding="utf-8")
        if number < 3:
            (history / "src" / "a.py").write_text(heavy("a") + f"# {number}\n", encoding="utf-8")
        if number == 0:
            (history / "src" / "b.py").write_text(heavy("b"), encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", f"c{number}")
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    for name in ("a", "b", "c"):
        (source / "src" / f"{name}.py").write_text((history / "src" / f"{name}.py").read_text(encoding="utf-8"), encoding="utf-8")
    return SimpleNamespace(history=history, source=source, root=tmp_path)


def test_targeted_files_are_the_often_changed_ones_krypton_flags(world):
    workspace = ac.fresh_workspace(world.source, world.root / "ws", Path(sys.executable))
    assert rl.hot_files(world.history, "src", workspace, top=5) == ["src/a.py", "src/b.py"]  # c.py is changed most but is not flagged
    assert rl.hot_files(world.history, "src", workspace, top=1) == ["src/a.py"]


def test_a_branch_starts_from_identical_code_and_is_independent(world):
    base = ac.fresh_workspace(world.source, world.root / "base", Path(sys.executable))
    branch = rl.branch_from(base, world.root / "branch", "strict")
    assert branch.level == "strict" and (branch.root / "src" / "a.py").read_text(encoding="utf-8") == (base.root / "src" / "a.py").read_text(encoding="utf-8")
    branch.call("write_file", {"path": "src/a.py", "content": "x = 1\n"})
    assert "heavy" not in (base.root / "src" / "a.py").read_text(encoding="utf-8") and (base.root / "src" / "a.py").read_text(encoding="utf-8") != "x = 1\n"


def test_cleanup_instructions_differ_by_scope():
    assert rl.cleanup_ticket(rl.VARIANTS["standard"], []) == ac.REFACTOR_TICKET
    text = rl.cleanup_ticket(rl.VARIANTS["targeted"], ["src/a.py", "src/b.py"])
    assert "- src/a.py" in text and "- src/b.py" in text and "Do not touch other files" in text


def test_the_strict_variant_makes_the_krypton_tool_use_that_level(world):
    base = ac.fresh_workspace(world.source, world.root / "base", Path(sys.executable))
    strict = rl.branch_from(base, world.root / "strict", "strict")
    assert "Krypton Structural Review" in strict.call("run_krypton", {"paths": "src"})


class Reply:
    def __init__(self, blocks, stop):
        self.content, self.stop_reason = blocks, stop
        self.usage = SimpleNamespace(model_dump=lambda: {"input_tokens": 5, "output_tokens": 5, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 20})


class Scripted:
    """Every agent run: one read_file, then DONE. Remembers each system prompt it saw."""

    def __init__(self):
        self.runs, self.systems, self.messages, self._turn = 0, [], SimpleNamespace(create=self._create), 0

    def _create(self, **kwargs):
        if len(kwargs["messages"]) == 1:
            self.runs += 1
            self.systems.append(kwargs["system"])
            return Reply([SimpleNamespace(type="tool_use", name="read_file", id="t", input={"path": "src/c.py"})], "tool_use")
        return Reply([SimpleNamespace(type="text", text="DONE")], "end_turn")


def test_the_whole_ladder_runs_ticket_one_once_then_every_branch(world, monkeypatch):
    monkeypatch.setattr(ac, "check_work", lambda workspace, hidden: {"suite_ok": True})
    args = SimpleNamespace(tasks=[1, 2, 3], variants=["standard", "targeted"], workspaces=world.root / "ws", source=world.source, python=Path(sys.executable),
                           hidden=Path("unused"), history_repo=world.history, hot_top=5, out=world.root / "out.json", max_usd=5)
    client, budget, results = Scripted(), Budget(5), {}
    rl.run_rep(0, client, args, budget, results)
    rec = results["0"]
    assert rec["shared"]["label"] == "ticket 1"
    assert set(rec["branches"]) == {"control", "standard", "targeted"}
    labels = {name: [s["label"] for s in branch["stages"]] for name, branch in rec["branches"].items()}
    assert labels == {"control": ["ticket 2", "ticket 3"], "standard": ["cleanup", "ticket 2", "ticket 3"], "targeted": ["cleanup", "ticket 2", "ticket 3"]}
    assert client.runs == 1 + 2 + 3 + 3  # the shared ticket once, then each branch's own stages
    assert rec["branches"]["targeted"]["stages"][0]["targets"] == ["src/a.py", "src/b.py"]
    saved = json.loads(args.out.read_text(encoding="utf-8"))
    assert list(saved["reps"]) == ["0"] and saved["meta"]["spent_usd"] > 0
    assert "Krypton is available" in client.systems[3] and "Krypton" not in client.systems[1]  # cleanup agents get Krypton; ticket agents do not


def test_the_ladder_stops_when_the_budget_would_be_passed(world, monkeypatch):
    monkeypatch.setattr(ac, "check_work", lambda workspace, hidden: {})
    args = SimpleNamespace(tasks=[1, 2], variants=["standard"], workspaces=world.root / "ws", source=world.source, python=Path(sys.executable), hidden=Path("unused"),
                           history_repo=world.history, hot_top=5, out=world.root / "out.json", max_usd=0.0001)
    with pytest.raises(rl.BudgetExceeded):
        rl.run_rep(0, Scripted(), args, Budget(0.0001), {})


def test_task_lists_accept_single_digits_or_comma_separated_numbers():
    assert rl.parse_tasks("123456") == [1, 2, 3, 4, 5, 6]
    assert rl.parse_tasks("2,7,8,9,10,11") == [2, 7, 8, 9, 10, 11]


def test_the_feature_chain_tickets_exist_and_have_hidden_tests():
    chain = [2, 7, 8, 9, 10, 11]
    assert all(n in ac.TICKETS for n in chain)
    hidden = (Path(ac.__file__).parent / "garth_acceptance" / "hidden_tests.py").read_text(encoding="utf-8")
    assert all(f"def test_t{n}_" in hidden for n in chain)

