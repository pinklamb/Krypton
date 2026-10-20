"""Tune the volatility ordering offline: score candidate file orders on many real repositories with the free cache predictor (no API calls).

    python -m tools.tune_volatility --repos repos.json --cache tune_cache             # build the data (once) and print the table
    python -m tools.tune_volatility --repos repos.json --cache tune_cache --folds 5   # also check the choice on repositories it was not chosen on

The predictor is the same one tools.cache_anthropic uses (it matched live Claude runs within about a point on two repositories). For every repository the replay is
the base commit's files followed by the next 12 commits that touched the source; an order is fixed from the history BEFORE the base commit, so the test asks whether
what changed before predicts what changes next. A rule turns a file's recent history into a score (higher means more likely to change); files are sent lowest score first.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import random
import statistics as st
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tools.cache_anthropic import anchors, blocks_for, breakpoints, predicted_read, prefix_hashes
from tools.cache_live import commits, git, place, tree

HISTORY_DEPTH = 400  # commits of history kept before the base commit
History = list[dict[str, tuple[int, int]]]  # newest first; per commit {file: (lines added, lines deleted)}
Rule = Callable[[History, list[str]], dict[str, float]]


def load_history(repo: Path, base: str, subdir: str) -> History:
    """The commits before (and including) `base` that touched `subdir`, newest first, with lines added and deleted per file."""
    text = git(repo, "log", base, f"-n{HISTORY_DEPTH}", "--numstat", "--format=@%H", f"--relative={subdir}", "--", subdir)
    out: History = []
    for line in text.splitlines():
        if line.startswith("@"):
            out.append({})
        elif line.strip() and out:
            added, deleted, name = line.split("\t", 2)
            out[-1][name] = (int(added) if added.isdigit() else 0, int(deleted) if deleted.isdigit() else 0)
    return out


def load_repo(repo: Path, subdir: str, cache: Path, turns: int = 12) -> dict:
    """The replay states and the history before them; built once and kept on disk."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{repo.name}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    chain = commits(repo, subdir, turns)
    data = {"states": [tree(repo, commit, subdir) for commit in chain], "history": load_history(repo, chain[0], subdir), "name": repo.name}
    path.write_bytes(pickle.dumps(data))
    return data


def load_windows(repo: Path, subdir: str, cache: Path, windows: int = 4, turns: int = 12) -> list[dict] | None:
    """Several consecutive replay windows, latest first, each with the history before its own base commit; None if the repository has too little history."""
    path = cache / f"{repo.name}_w{windows}.pkl"
    if path.exists():
        return pickle.loads(path.read_bytes())
    chain = git(repo, "rev-list", "--first-parent", f"-n{windows * turns + 1}", "HEAD", "--", subdir).split()[::-1]
    count = (len(chain) - 1) // turns
    if count < 2:
        return None
    trees = {commit: tree(repo, commit, subdir) for commit in chain}
    out = []
    for j in range(count):
        end = len(chain) - turns * j
        part = chain[end - turns - 1:end]
        out.append({"states": [trees[c] for c in part], "history": load_history(repo, part[0], subdir)})
    path.write_bytes(pickle.dumps(out))
    return out


# ---- rules: history -> score per file (the order sends the lowest score first)
def count(window: int) -> Rule:
    """Commits that touched the file among the last `window` (the current rule uses 200)."""
    return lambda history, names: {n: float(sum(1 for c in history[:window] if n in c)) for n in names}


def recency(half_life: float, window: int = HISTORY_DEPTH) -> Rule:
    """Each touch counts 0.5 ** (age / half_life): recent changes weigh more."""
    return lambda history, names: {n: sum(0.5 ** (age / half_life) for age, c in enumerate(history[:window]) if n in c) for n in names}


def churn(window: int, half_life: float | None = None) -> Rule:
    """Lines changed (added plus deleted), optionally with recency weighting."""
    def rule(history: History, names: list[str]) -> dict[str, float]:
        weight = (lambda age: 0.5 ** (age / half_life)) if half_life else (lambda age: 1.0)
        return {n: sum(weight(age) * math.log1p(sum(c[n])) for age, c in enumerate(history[:window]) if n in c) for n in names}
    return rule


def with_new_files(base: Rule, recent: int) -> Rule:
    """A file whose whole recorded history lies in the last `recent` commits is treated as the most volatile (new or just renamed)."""
    def rule(history: History, names: list[str]) -> dict[str, float]:
        scores = base(history, names)
        top = max(scores.values(), default=0.0) + 1.0
        for n in names:
            ages = [age for age, c in enumerate(history) if n in c]
            if ages and max(ages) < recent and len(history) > 2 * recent:
                scores[n] = top
        return scores
    return rule


RULES: dict[str, Rule] = {
    "count 200 (current)": count(200), "count 100": count(100), "count 50": count(50), "count 25": count(25), "count 400": count(400),
    "recency 100": recency(100), "recency 50": recency(50), "recency 25": recency(25), "recency 12": recency(12),
    "churn 200": churn(200), "churn 100": churn(100), "churn recency 50": churn(HISTORY_DEPTH, 50), "churn recency 25": churn(HISTORY_DEPTH, 25),
    "recency 25 + new files": with_new_files(recency(25), 30), "recency 50 + new files": with_new_files(recency(50), 30), "count 200 + new files": with_new_files(count(200), 30),
}


def order_for(rule: Rule, history: History, names: list[str]) -> list[str]:
    """Stable first: lowest score first, ties by name."""
    scores = rule(history, names)
    return sorted(names, key=lambda n: (scores[n], n))


def predicted_reuse(states: list[dict[str, str]], order: list[str]) -> float:
    """The mean predicted share of each prompt read from cache over the replay turns, with the live runs' breakpoints (three fixed anchors plus the last file)."""
    fixed, written, reads = anchors(states[0], order), set(), []
    for turn, files in enumerate(states):
        blocks = blocks_for(files, order, breakpoints("quartiles", files, order, fixed))
        if turn:
            reads.append(predicted_read(blocks, written))
        written.update(prefix_hashes(blocks)[i] for i, block in enumerate(blocks) if "cache_control" in block)
    return sum(reads) / len(reads) if reads else 0.0


def evaluate(repos: dict[str, dict], rules: dict[str, Rule]) -> dict[str, dict[str, float]]:
    """{rule: {repo: predicted reuse}}, plus the two reference orders."""
    table: dict[str, dict[str, float]] = {name: {} for name in rules}
    table["alphabetical"], table["reversed (most changed first)"] = {}, {}
    for repo, data in repos.items():
        names = sorted(data["states"][0])
        for name, rule in rules.items():
            table[name][repo] = predicted_reuse(data["states"], order_for(rule, data["history"], names))
        table["alphabetical"][repo] = predicted_reuse(data["states"], names)
        table["reversed (most changed first)"][repo] = predicted_reuse(data["states"], order_for(rules["count 200 (current)"], data["history"], names)[::-1])
    return table


def summarize(table: dict[str, dict[str, float]], baseline: str = "count 200 (current)") -> list[dict]:
    """One row per rule: mean reuse, and how it compares with the baseline repository by repository."""
    base = table[baseline]
    rows = []
    for name, scores in table.items():
        gaps = [scores[r] - base[r] for r in base]
        rows.append({"rule": name, "mean": st.mean(scores.values()), "median": st.median(scores.values()), "wins": sum(g > 0.005 for g in gaps), "losses": sum(g < -0.005 for g in gaps), "mean_gap": st.mean(gaps)})
    return sorted(rows, key=lambda r: -r["mean"])


def cross_validate(table: dict[str, dict[str, float]], rules: list[str], folds: int, repeats: int = 20, seed: int = 1) -> dict:
    """Choose the rule with the best mean on the training repositories and score it on the held-out ones, repeatedly; compared with the current rule and alphabetical."""
    repos = sorted(next(iter(table.values())))
    rng, out = random.Random(seed), {"chosen": [], "current": [], "alphabetical": [], "picks": {}}
    for _ in range(repeats):
        shuffled = repos[:]
        rng.shuffle(shuffled)
        for k in range(folds):
            test = shuffled[k::folds]
            train = [r for r in repos if r not in test]
            pick = max(rules, key=lambda rule: st.mean(table[rule][r] for r in train))
            out["picks"][pick] = out["picks"].get(pick, 0) + 1
            out["chosen"].append(st.mean(table[pick][r] for r in test))
            out["current"].append(st.mean(table["count 200 (current)"][r] for r in test))
            out["alphabetical"].append(st.mean(table["alphabetical"][r] for r in test))
    return {key: (st.mean(v) if isinstance(v, list) else v) for key, v in out.items()}


CANDIDATES = ["count 200 (current)", "recency 25", "count 50", "recency 100"]


def walk_forward(repos: dict[str, list[dict]], candidates: list[str] = CANDIDATES, with_alphabetical: bool = True) -> list[dict]:
    """For each repository: pick the best rule on its older windows (including alphabetical), score the pick on its latest window, next to the fixed current rule."""
    rows = []
    for name, windows in repos.items():
        reuse: dict[str, list[float]] = {c: [] for c in candidates + (["alphabetical"] if with_alphabetical else [])}
        for w in windows:
            names = sorted(w["states"][0])
            for c in candidates:
                reuse[c].append(predicted_reuse(w["states"], order_for(RULES[c], w["history"], names)))
            if with_alphabetical:
                reuse["alphabetical"].append(predicted_reuse(w["states"], names))
        older = {c: st.mean(v[1:]) for c, v in reuse.items()}
        pick = max(older, key=older.get)
        rows.append({"repo": name, "windows": len(windows), "pick": pick, "picked": reuse[pick][0], "current": reuse["count 200 (current)"][0], "alphabetical": reuse["alphabetical"][0] if with_alphabetical else None,
                     "best_in_hindsight": max(v[0] for v in reuse.values())})
    return rows


def main(args: argparse.Namespace) -> int:
    """Load or build every repository's data, evaluate the rules, print the table."""
    repos = {}
    for entry in json.loads(args.repos.read_text(encoding="utf-8")):
        path = Path(entry["path"])
        repos[path.name] = load_repo(path, entry["subdir"], args.cache)
    if args.walk:
        multi = {}
        for entry in json.loads(args.repos.read_text(encoding="utf-8")):
            path = Path(entry["path"])
            found = load_windows(path, entry["subdir"], args.cache)
            if found:
                multi[path.name] = found
        rows = walk_forward(multi)
        print(f"{len(rows)} repositories with at least 2 windows of history; predicted reuse on the latest window\n")
        print(f"{'repo':<20}{'windows':>8}  {'picked on older windows':<24}{'picked':>8}{'current':>9}{'alphabet.':>10}{'hindsight':>10}")
        for r in rows:
            print(f"{r['repo'].replace('survey_', ''):<20}{r['windows']:>8}  {r['pick']:<24}{100 * r['picked']:>7.1f}%{100 * r['current']:>8.1f}%{100 * r['alphabetical']:>9.1f}%{100 * r['best_in_hindsight']:>9.1f}%")
        mean = lambda key: 100 * st.mean(r[key] for r in rows)  # noqa: E731
        print(f"\nmean: picked {mean('picked'):.1f}%  current {mean('current'):.1f}%  alphabetical {mean('alphabetical'):.1f}%  best in hindsight {mean('best_in_hindsight'):.1f}%")
        print(f"picked beats current in {sum(r['picked'] > r['current'] + 0.005 for r in rows)}, loses in {sum(r['picked'] < r['current'] - 0.005 for r in rows)}, of {len(rows)}; picked beats alphabetical in {sum(r['picked'] > r['alphabetical'] + 0.005 for r in rows)}, loses in {sum(r['picked'] < r['alphabetical'] - 0.005 for r in rows)}")
        return 0
    table = evaluate(repos, RULES)
    print(f"{len(repos)} repositories; mean predicted share of each prompt read from cache\n")
    print(f"{'rule':<28}{'mean':>7}{'median':>8}{'beats current':>15}{'loses to it':>13}{'mean gap':>10}")
    for row in summarize(table):
        print(f"{row['rule']:<28}{100 * row['mean']:>6.1f}%{100 * row['median']:>7.1f}%{row['wins']:>15}{row['losses']:>13}{100 * row['mean_gap']:>+9.1f}")
    if args.folds:
        cv = cross_validate(table, list(RULES), args.folds)
        print(f"\nchosen on {args.folds - 1}/{args.folds} of the repositories, scored on the rest (average of {20 * args.folds} splits): chosen rule {100 * cv['chosen']:.1f}%, current {100 * cv['current']:.1f}%, alphabetical {100 * cv['alphabetical']:.1f}%")
        print("rules picked:", cv["picks"])
    if args.out:
        args.out.write_text(json.dumps(table, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Score candidate volatility orderings with the free cache predictor.")
    parser.add_argument("--repos", type=Path, required=True, help='JSON list of {"path": ..., "subdir": ...}')
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--folds", type=int, default=0)
    parser.add_argument("--walk", action="store_true", help="walk-forward test: pick a rule per repository on its older windows, score it on its latest")
    parser.add_argument("--out", type=Path)
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(parser.parse_args()))
