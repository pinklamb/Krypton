"""The ladder summary: only complete repetitions count, savings and payback come from the paired control."""

from tools import ladder_summary as ls


def stage(kind, cost, stop="end_turn", purity=50.0):
    totals = {"cost": cost, "requests": 10, "input_tokens": 100, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0}
    return {"kind": kind, "stop": stop, "totals": totals, "after": {"purity": purity, "nested_functions": 2, "flagged_files": 1, "tokens_est": 1000}, "check": {"suite_ok": True}}


def rep(control_cost, clean_cost, cleanup=0.4, stop="end_turn"):
    return {"shared": stage("ticket", 0.1),
            "branches": {"control": {"stages": [stage("ticket", control_cost)] * 2},
                         "standard": {"stages": [stage("cleanup", cleanup, stop)] + [stage("ticket", clean_cost, purity=80.0)] * 2}}}


def test_saving_payback_and_incomplete_reps_are_skipped():
    data = {"meta": {"variants": {"standard": {}}, "tasks": [1, 2, 3]}, "reps": {"0": rep(0.10, 0.06), "1": rep(0.10, 0.06), "2": rep(0.10, 0.06, stop="budget: x")}}
    summary = ls.summarize(data)
    row = summary["branches"]["standard"]
    assert summary["reps"] == 2
    assert round(row["saving_per_ticket"], 3) == 0.04 and round(row["payback_tickets"]) == 10 and row["cheaper_in"] == 2
    assert row["purity_final"] == 80.0 and summary["branches"]["control"]["payback_tickets"] is None


def test_no_complete_reps_gives_an_empty_summary():
    assert ls.summarize({"meta": {"variants": {}, "tasks": [1, 2]}, "reps": {}}) == {"reps": 0, "branches": {}}


def test_dashboard_renders_every_branch_and_survives_an_empty_file():
    from tools import ladder_report as lr
    data = {"meta": {"variants": {"standard": {}}, "tasks": [1, 2, 3], "model": "m", "spent_usd": 1.0}, "reps": {"0": rep(0.10, 0.06)}}
    page = lr.render(data, note="sample")
    assert "<svg" in page and "standard" in page and "control" in page and "pays back" in page
    assert "No complete" in lr.render({"meta": {"variants": {}, "tasks": [1, 2]}, "reps": {}})
