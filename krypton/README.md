# [Krypton](https://pinklamb.github.io/Krypton/)

Docs and site: https://pinklamb.github.io/Krypton/

Krypton scores how structurally heavy code is: decisions, nesting, bloated functions. It works on JavaScript, TypeScript and Python, runs locally with no network, and is built to sit next to a coding agent. It is not a linter. Run your linter too.

It answers one question for an agent: **"did my change make this code harder to read, edit and pay for in tokens?"**

```
$ krypton src --top 5
   eff altered branches  nesting depth outputs async imports   rating  flag                 file
   241     241       17       19     2      11     0       0  13400.0  semantic_path_logic  components/SearchPanel.jsx
...
### Krypton Structural Review -- Purity Score: 59/100
| File | Issue | Rule |
| components/SearchPanel.jsx | Logic complexity 13400 (threshold ...) in `SearchPanel()` | Semantic Path Logic |
```

## What Krypton is

Krypton is a command-line program that reads your source code and scores how structurally heavy it is. It gives every function and file a number, flags the ones that are too tangled to read or change safely, and tells you which block to pull out. It never edits or runs your code, uses no AI, and needs no network.

It exists because AI coding agents write quickly but tend to pile up deeply nested, branch-heavy functions, which are harder to change and cost tokens every time the code is read again.

**Workflow:** scan (`krypton src` gives a 0 to 100 purity score and a ranked list), fix (hand the flagged functions to your agent; hints name the block to split), gate (run it in CI so a touched function over the limit fails the check).

**Three ways to use it:**

- **Keep code clean.** In a 20-revision agent test a CI gate kept the worst function near 12 times the limit instead of about 1,900, for about 62% more cost per run.
- **Cut the prompt bill.** Order files from most stable to most volatile so providers can cache a long shared prefix. 35% cheaper on garth; a typical gain is about 4 points of cache reuse.
- **Measure the gap.** Compare AI-assisted code with the rest of your repository in the same size bands. A measurement of difference, not a verdict.

You choose how clean: `--level relaxed`, `standard` or `strict` sets how much tangle is tolerated (only `standard` has been tested with agents). Krypton is not a linter, not an AI, and not a formatter.

## Install

```bash
pip install .            # from this folder; gives you the `krypton` command (Python 3.12+)
# or
docker build -t krypton . && docker run --rm -v "$PWD:/repo" krypton .
```

Without installing: `python -m krypton.cli <paths>` from this folder.

## The agent loop

Give your agent these rules (paste into `CLAUDE.md`, `AGENTS.md` or your tool's rules file):

```markdown
## Code structure (Krypton)
- After editing source files, run: `krypton <the files or folders you changed> --top 10 --history .krypton/history.jsonl`
- If a file is flagged, or a function you wrote appears with a `nested` flag, refactor it
  before finishing: early returns, small named helpers, one job per function.
- Do not satisfy Krypton by packing code onto fewer lines or by splitting one function into
  many trivial wrappers.
- If a function is marked STALLED, do what its stated reason says before trying anything else.
- Re-run Krypton after refactoring and report the purity score.
```

A typical session: the agent edits, runs `krypton`, reads the flagged functions, refactors, runs it again. Exit code `0` is fine; `1` means the purity score fell below the failure line; `2` is a usage error (no files, bad config).

### Only score what the agent changed

```bash
krypton . --base HEAD            # lines changed since the last commit, including new files
krypton . --base origin/main     # everything on this branch
```

Only changed lines count toward the ratings, so old code in the same file doesn't blame the agent.

### Machine-readable output

```bash
krypton src --json
```

Prints the full result: `purity_score`, `check_conclusion`, and one finding per file with `logic_complexity_rating`, `flagged`, `flag_reason`, `nesting_score`, `max_depth`, `effective_lines` and a `functions` list. Pipe it into a hook or CI step.

### How far from clean, and how strict

The report ends its file count with how many functions are over the limit and how far over the worst is, and every hint starts with its own multiple. `--level` sets the bar:

```bash
krypton src --level relaxed    # a looser bar: clear the worst offenders first
krypton src                    # standard
krypton src --level strict     # a tighter bar
```

### Refactoring hints

When a function is over the limit or nested three levels deep, Krypton says where and what to try, for example:

```
Refactoring hints (top 3):
  m.py:1 tangle: deepest decision at line 5 (depth 4), inside lines 4-8 which hold most of this function's decision weight; invert conditions with early returns, or move lines 4-8 into a helper
```

They appear under the ranking, in the PR comment, and in `--json` under `hints` (`{"file:line function": "text"}`, heaviest first). They are advice only and never change a score. When a hint names a block it also predicts the ratings after extracting it.

### Log progress and see why a score stalled

```bash
krypton src --history .krypton/history.jsonl
```

Each run is appended to the log and compared with the last one:

```
Progress since the last run:
  purity: run 4: purity 41 -> 55 -> 55 -> 60; flagged files 5 -> 4
  src/game.js:update: improved 4840 -> 2100 (57% lower)
  src/game.js:render: STALLED for 3 runs at 931: the hinted block (lines 14-32 last run) is still intact; the edits were elsewhere in the function
```

A function that did not improve says why: not edited, the block it was told to extract is untouched, the weight moved to another function, or the block changed without helping. The log keeps ratings, names and line hashes only, never source. Add the flag to your agent's rules so every iteration passes `--history`, and cache the file in CI.

### Order files for prompt caching

```bash
krypton src --order-by-volatility        # JSON manifest, most stable file first, from git history
krypton src --order-by-volatility --volatility counts.json   # or supply {"path": change_count}
```

If your tool re-sends the codebase every turn, put files in this order so the stable ones form a shared prefix a provider can cache. It is a hint from history, not a guarantee, and it does not change any score. How much it saves depends on your repository: on garth it made the same prompts 35% cheaper, but across 20 open-source repositories it beat alphabetical order on 12 and lost on 7 (a typical gain of about 4 points of cache reuse). To predict your own result without spending anything, replay your repository's recent commits with the research tool in `tools/`: `python -m tools.cache_anthropic REPO --dry --no-control`.

### Projected savings for a team

A model, not a measurement of your bill: 50 developers, 100 agent requests each per day, 22 days a month, a 25,000-line repository, list prices, counting only the cost of re-reading the code. Plan on the typical row; the garth-like row is a best case.

| Model | Repository | Monthly, alphabetical | Monthly, Krypton order | Saved a month | Saved a year |
|---|---|---|---|---|---|
| Claude Sonnet 5.5 | Typical repository | $61.5K | $58.3K | $3.2K (5%) | $38.4K |
| Claude Sonnet 5.5 | garth-like best case | $45.2K | $27.3K | $17.9K (40%) | $214.7K |
| Claude Opus 5.5 | Typical repository | $121.5K | $114.9K | $6.7K (5%) | $80.2K |
| Claude Opus 5.5 | garth-like best case | $87.4K | $50.0K | $37.3K (43%) | $448.1K |

### Pre-commit or CI

```bash
krypton . --base origin/main --top 0 || { echo "Krypton: structure check failed"; exit 1; }
```

## Reading the output

| Column | Meaning |
|---|---|
| `branches` | decisions in the file (`if`, loops, `case`, `catch`, `&&`, ternaries...), counted like radon and ESLint |
| `nesting` | how deeply those decisions are stacked, found by a depth-first walk; a nested decision costs more than a flat one |
| `depth` | deepest nesting. `nested` appears in a function's flags at depth 3 or more |
| `eff` | effective lines: long or multi-statement lines count more, so packing code doesn't help |
| `rating` | Krypton's overall structural rating. Over the limit, the file is flagged |
| Purity Score | 0 to 100 for the whole run. Higher is lighter |

### How the score is calculated

Krypton is modeled after **radon**: every decision in a function (`if`, a loop, `case`, `catch`, a boolean operator, a ternary) is counted the way radon counts it, and for JavaScript and TypeScript the same rules are checked against ESLint's complexity rule. Radon stops at the count. Krypton adds where each decision sits.

To find that, it walks the syntax tree depth first, once, with an explicit stack of the places still to visit, each tagged with its depth. It takes the newest item off the stack, records it if it is a decision, and pushes its children with the depth they will sit at: the body of an `if`, a loop, a `catch` or a `case` is one level deeper, neighbouring statements stay level. Because the newest item always comes off first, it follows one branch all the way down, then backs up to the next waiting sibling. A marker on the stack closes each function's scope, so every function gets its own totals.

```python
def pay(order):
    if order.paid:                 # decision at depth 1
        for item in order.items:   # decision at depth 2
            if item.taxable:       # decision at depth 3
                add_tax(item)
```

Radon reports complexity 4 here, and 4 again if the three `if`s sat one after another. Krypton records the same three decisions and that they are stacked three deep. The exact weights and the rating formula are not published. The [site](https://pinklamb.github.io/Krypton/how-it-works.html#score) walks through this example step by step.

A flagged file means "an agent will spend extra tokens reading this and is more likely to break it". The fix is almost always splitting a large function by job, not deleting logic.

The rating cannot be diluted by giving every branch a different return value, packing code onto fewer lines, or spreading one heavy function across helpers in the same file: Krypton also flags those directly. A **coupling hotspot** is a file wired to many other files (imports it makes, imports of it, and for JS/TS, members it reads from a shared global object); it is flagged so wiring gets split instead of piling into a few core files. The **Structural Overhead Tax** lowers the purity score (it never flags a file) when most of a file's functions are trivial wrappers, to discourage splitting code into many tiny functions just to stay under a limit.

Files that are too large, contain extremely long lines or are extremely wide are **not checked**. They are left out of the purity score and listed under `errors` in the JSON, in the warnings and in the PR comment, as `{error code: explanation}` with the setting that fixes it (for example `max_file_bytes = 3000000` in a `--config` TOML file). Minified bundles land here; add them to your ignore file. If every file is refused, the result says so, since a score of 100 would mean nothing.

The scoring and ordering algorithms are not published; the [site](https://pinklamb.github.io/Krypton/how-it-works.html) explains what they measure and what you can rely on.

## What it scans

- `.js .jsx .mjs .cjs .ts .tsx .mts .cts .py`
- Inside a git repo, anything `.gitignore` ignores is skipped. Vendored and generated paths (`node_modules`, `dist`...) are skipped by default.
- Override any setting with `--config krypton.toml` (see `krypton/engine/config.py` for the fields).

## Limits worth knowing

- It measures structure, not correctness or style. It will not catch a bug.
- Token counts and dollar figures from `tools/demo_cost.py` are estimates; see that file's header.
- One long fluent chain such as `.on().on()` is counted as several async boundaries.
- No import-cycle detection yet.

## More

- Documentation site: getting started, how it works, command reference, integrations, prompt caching, FAQ (in [`site/`](site/)).
- Tests: `pip install -e ".[dev]" && pytest`
