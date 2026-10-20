"""The volatility tuning harness: rules turn history into an order, the predictor scores it, and the held-out checks stay honest."""

from tools import tune_volatility as tv

HISTORY = [  # newest first; {file: (added, deleted)}
    {"hot.py": (10, 2)}, {"hot.py": (4, 4), "warm.py": (1, 1)}, {"warm.py": (30, 0)}, {"old.py": (5, 5)}, {"old.py": (2, 2)}, {"old.py": (9, 9)},
]
NAMES = ["cold.py", "hot.py", "old.py", "warm.py"]


def test_count_orders_by_how_often_a_file_changed():
    assert tv.order_for(tv.count(6), HISTORY, NAMES) == ["cold.py", "hot.py", "warm.py", "old.py"]   # 0, 2, 2 (tie broken by name), 3 changes
    assert tv.order_for(tv.count(2), HISTORY, NAMES)[-1] == "hot.py"          # in the last two commits only hot.py and warm.py changed, hot.py twice


def test_recency_weighs_recent_changes_more_than_old_ones():
    scores = tv.recency(2)(HISTORY, NAMES)
    assert scores["hot.py"] > scores["old.py"]                                    # old.py changed three times, but long ago
    assert tv.count(6)(HISTORY, NAMES)["old.py"] == 3 == tv.count(6)(HISTORY, NAMES)["hot.py"] + 1


def test_new_files_are_treated_as_the_most_volatile():
    history = [{"fresh.py": (50, 0)}] + [{"stable.py": (1, 1)}] * 80
    scores = tv.with_new_files(tv.count(200), 30)(history, ["fresh.py", "stable.py"])
    assert scores["fresh.py"] > scores["stable.py"]


def test_ties_are_broken_by_name_so_the_prefix_is_reproducible():
    assert tv.order_for(tv.count(10), [], ["b.py", "a.py", "c.py"]) == ["a.py", "b.py", "c.py"]


def test_the_predictor_rewards_putting_the_changing_file_last():
    big = "x = 1\n" * 400
    states = [{"stable.py": big, "busy.py": big + f"# {turn}\n"} for turn in range(6)]
    last = tv.predicted_reuse(states, ["stable.py", "busy.py"])
    first = tv.predicted_reuse(states, ["busy.py", "stable.py"])
    assert last > 0.3 and first < last


def test_cross_validation_scores_the_chosen_rule_only_on_held_out_repositories():
    repos = [f"r{i}" for i in range(10)]
    table = {"count 200 (current)": {r: 0.30 for r in repos}, "alphabetical": {r: 0.20 for r in repos}, "lucky": {r: (0.90 if i < 5 else 0.0) for i, r in enumerate(repos)}}
    result = tv.cross_validate(table, ["count 200 (current)", "lucky"], folds=5, repeats=5)
    assert result["current"] == 0.30 and result["alphabetical"] == 0.20
    assert result["chosen"] < 0.60                                               # a rule that only fits the training repositories does not look as good on the rest


def test_walk_forward_scores_the_pick_on_the_latest_window_only():
    big = "x = 1\n" * 300
    window = {"states": [{"a.py": big, "b.py": big + f"# {n}\n"} for n in range(5)], "history": [{"b.py": (1, 1)}] * 5 + [{"a.py": (1, 1)}]}
    rows = tv.walk_forward({"toy": [window, window, window]}, candidates=["count 200 (current)"], with_alphabetical=True)
    assert rows[0]["repo"] == "toy" and rows[0]["windows"] == 3 and 0 <= rows[0]["picked"] <= 1
    assert rows[0]["pick"] in ("count 200 (current)", "alphabetical")
