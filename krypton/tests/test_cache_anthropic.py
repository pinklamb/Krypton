"""The Anthropic cache replay: key lookup, breakpoint placement, the cost guard and the prediction, all without a network."""

from types import SimpleNamespace

import pytest

from tools import cache_anthropic as ca

FILES = {"a.py": "a" * 4000, "b.py": "b" * 4000, "c.py": "c" * 4000, "d.py": "d" * 4000, "e.py": "e" * 4000}
ORDER = ["a.py", "b.py", "c.py", "d.py", "e.py"]


class FakeClient:
    """Stands in for anthropic.Anthropic: replies OK and reports a usage object shaped like the real one."""

    def __init__(self, usage):
        self.calls, self.messages = [], SimpleNamespace(create=self._create)
        self._usage = usage

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text="OK")], usage=SimpleNamespace(model_dump=lambda: dict(self._usage)))


USAGE = {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def test_key_is_found_by_any_name_and_capitalisation(tmp_path):
    (tmp_path / ".env").write_text("OPEN_ROUTE_API_KEY=other\nTest_KEY=sk-ant-secret\n", encoding="utf-8")
    assert ca.find_key(tmp_path, env={}) == "sk-ant-secret"
    assert ca.find_key(tmp_path, env={"ANTHROPIC_API_KEY": "from-env"}) == "from-env"
    assert ca.find_key(tmp_path / "nowhere", env={}) is None


def test_request_cost_uses_the_published_rates():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}
    assert ca.request_cost(usage) == pytest.approx(2.00 + 10.00 + 0.20 + 2.50)


def test_anchors_split_the_code_into_quarters_and_blocks_carry_at_most_four_breakpoints():
    fixed = ca.anchors(FILES, ORDER)
    assert len(fixed) == 3 and fixed == sorted(fixed)
    marks = ca.breakpoints("quartiles", FILES, ORDER, fixed)
    blocks = ca.blocks_for(FILES, ORDER, marks)
    assert sum("cache_control" in b for b in blocks) <= 4
    assert "cache_control" not in blocks[-1] and blocks[-1]["text"] == ca.QUESTION


def test_the_control_strategy_has_a_single_breakpoint_on_the_last_file():
    marks = ca.breakpoints("end", FILES, ORDER, ca.anchors(FILES, ORDER))
    assert marks == ["e.py"]


def test_anchors_stay_on_the_same_files_and_a_new_file_goes_last():
    fixed = ca.anchors(FILES, ORDER)
    later = {**FILES, "z.py": "z" * 100}
    assert ca.breakpoints("quartiles", later, ORDER, fixed)[:-1] == fixed
    assert ca.breakpoints("quartiles", later, ORDER, fixed)[-1] == "z.py"


def test_prediction_reads_the_unchanged_prefix_only():
    fixed = ca.anchors(FILES, ORDER)
    first = ca.blocks_for(FILES, ORDER, ca.breakpoints("quartiles", FILES, ORDER, fixed))
    written = {h for h, b in zip(ca.prefix_hashes(first), first) if "cache_control" in b}
    assert ca.predicted_read(first, written) > 0.99 * (len("".join(b["text"] for b in first[:-1])) / sum(len(b["text"]) for b in first)) - 0.01
    changed = {**FILES, "e.py": "E" * 4000}
    second = ca.blocks_for(changed, ORDER, ca.breakpoints("quartiles", changed, ORDER, fixed))
    assert 0.5 < ca.predicted_read(second, written) < 0.8  # everything before the changed last file is still read
    reordered = ca.blocks_for(FILES, ORDER[::-1], ca.breakpoints("quartiles", FILES, ORDER[::-1], fixed))
    assert ca.predicted_read(reordered, written) == 0


def test_a_run_stops_before_the_budget_would_be_passed():
    client = FakeClient(USAGE)
    budget = ca.Budget(cap=0.0001)
    run = ca.run_one(client, [FILES, FILES], "volatility", ORDER, "quartiles", 0, budget, pause=0)
    assert run["complete"] is False and "cap" in run["stopped"] and client.calls == []


def test_a_run_records_the_raw_usage_of_every_turn_and_sends_the_unique_run_line():
    client = FakeClient(USAGE)
    budget = ca.Budget(cap=50)
    run = ca.run_one(client, [FILES, {**FILES, "e.py": "E" * 10}], "alphabetical", ORDER, "end", 1, budget, pause=0)
    assert run["complete"] and [t["usage"] for t in run["turns"]] == [USAGE, USAGE]
    assert len(client.calls) == 2 and client.calls[0]["system"][0]["text"] == f"run {run['nonce']}"
    assert client.calls[0]["thinking"] == {"type": "between_tools"} and client.calls[0]["max_tokens"] == ca.MAX_TOKENS
    assert budget.spent == pytest.approx(2 * ca.request_cost(USAGE))


def test_measured_share_skips_the_cold_start_turn():
    cold = {"usage": {"input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 1000}}
    warm = {"usage": {"input_tokens": 100, "cache_read_input_tokens": 700, "cache_creation_input_tokens": 200}}
    assert ca.measured_share({"turns": [cold, warm]}) == pytest.approx(0.7)
