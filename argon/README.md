# [Argon](https://pinklamb.github.io/Argon/)

Docs and site: https://pinklamb.github.io/Argon/

Argon scores how structurally heavy code is: branches, nesting, bloated functions. It works on JavaScript, TypeScript and Python, runs locally with no network, and is built to sit next to a coding agent. It is not a linter. Run your linter too.

It answers one question for an agent: **"did my change make this code harder to read, edit and pay for in tokens?"**

```
$ argon src --top 5
   eff altered branches  nesting depth outputs async imports   rating  flag                 file
   241     241       17       19     2      11     0       0  13400.0  semantic_path_logic  components/SearchPanel.jsx
...
### Argon Structural Review -- Purity Score: 59/100
| File | Issue | Rule |
| components/SearchPanel.jsx | Logic complexity 13400 (threshold 648) in `SearchPanel()` | Semantic Path Logic |
```

## Install

```bash
pip install .            # from this folder; gives you the `argon` command (Python 3.12+)
# or
docker build -t argon . && docker run --rm -v "$PWD:/repo" argon .
```

Without installing: `python -m argon.cli <paths>` from this folder.

## The agent loop

Give your agent three rules (paste into `CLAUDE.md`, `AGENTS.md` or your tool's rules file):

```markdown
## Code structure (Argon)
- After editing source files, run: `argon <the files or folders you changed> --top 10 --history .argon/history.jsonl`
- If a file is flagged, or a function you wrote appears with a `nested` flag, refactor it
  before finishing: early returns, small named helpers, one job per function.
- Do not satisfy Argon by packing code onto fewer lines or by splitting one function into
  many trivial wrappers; the score counts characters and statements, not just newlines.
- If a function is marked STALLED, do what its stated reason says before trying anything else.
- Re-run Argon after refactoring and report the purity score.
```

A typical session: the agent edits, runs `argon`, reads the flagged functions, refactors, runs it again. Exit code `0` is fine; `1` means purity fell below the failure threshold (40); `2` is a usage error (no files, bad config).

### Only score what the agent changed

```bash
argon . --base HEAD            # lines changed since the last commit, including new files
argon . --base origin/main     # everything on this branch
```

Only changed lines count toward the ratings, so old code in the same file doesn't blame the agent.

### Machine-readable output

```bash
argon src --json
```

Prints the full result: `purity_score`, `check_conclusion`, and one finding per file with `logic_complexity_rating`, `flagged`, `flag_reason`, `nesting_score`, `max_depth`, `effective_lines` and a `functions` list. Pipe it into a hook or CI step.

### How far from clean, and how strict

The report ends its file count with how many functions are over the threshold and how far over the worst is (`11 of 267 functions are over the threshold (648); the worst is 5.8x over.`), and every hint starts with its own multiple. `--level` sets the bar:

```bash
argon src --level relaxed    # thresholds x3: clear the worst offenders first
argon src                    # standard: the fitted thresholds
argon src --level strict     # thresholds x0.5
```

### Refactoring hints

When a function is over the threshold or nested three levels deep, Argon says where and what to try, for example:

```
Refactoring hints (top 3):
  m.py:1 tangle: deepest decision at line 5 (depth 4), inside lines 4-8 which hold 79% of this function's decision weight; invert conditions with early returns, or move lines 4-8 into a helper
```

They appear under the ranking, in the PR comment, and in `--json` under `hints` (`{"file:line function": "text"}`, heaviest first). They are advice only and never change a score.

When a hint names a block it also predicts the ratings after extracting it (`predicted after extracting: this function 20, helper 54 ..., clears the flag`).

### Log progress and see why a score stalled

```bash
argon src --history .argon/history.jsonl
```

Each run is appended to the log and compared with the last one:

```
Progress since the last run:
  purity: run 4: purity 41 -> 55 -> 55 -> 60; flagged files 5 -> 4
  src/game.js:update: improved 4840 -> 2100 (57% lower)
  src/game.js:render: STALLED for 3 runs at 931: the hinted block (lines 14-32 last run) is still intact; the edits were elsewhere in the function
```

A function that did not improve says why: not edited, the block it was told to extract is untouched, the weight moved to another function, or the block changed without helping. The log keeps ratings, names and line hashes only, never source. Add the file to your agent's rules so every iteration passes `--history`, and cache it in CI.

### Order files for prompt caching

```bash
argon src --order-by-volatility        # JSON manifest, most stable file first, from git history
argon src --order-by-volatility --volatility counts.json   # or supply {"path": change_count}
```

If your tool re-sends the codebase every turn, put files in this order so the stable ones form a shared prefix a provider can cache. It is a hint from history, not a guarantee, and it does not change any score.

### Pre-commit or CI

```bash
argon . --base origin/main --top 0 || { echo "Argon: structure check failed"; exit 1; }
```

## Reading the output

| Column | Meaning |
|---|---|
| `branches` | decisions in the file (`if`, loops, `case`, `catch`, `&&`, ternaries...), counted like radon and ESLint |
| `nesting` | the same decisions weighted by depth: a nested `if` costs more than a flat one; three nested ifs score 6, not 3 |
| `depth` | deepest nesting. `nested` appears in a function's flags at depth 3 or more |
| `eff` | effective lines: long or multi-statement lines count more, so packing code doesn't help |
| `rating` | branches squared times effective lines, divided by distinct outputs. Over the threshold (648 by default) the file is flagged |
| Purity Score | 0 to 100 for the whole run. Below 40 fails |

A flagged file means "an agent will spend extra tokens reading this and is more likely to break it". The fix is almost always splitting a large function by job, not deleting logic.

The rating can be diluted (for example by giving every branch a different return value), so Argon also flags a file when any function has at least 30 decisions or a nesting score of 100, when the whole file's nesting score is at least 600 (spreading a heavy function across helpers does not lower it), and when four or more small files import each other heavily. These counts do not depend on return values or line packing. All limits are `--config` settings.

A **coupling hotspot** is a file wired to more than 12 other files (imports it makes, imports of it, and for JS/TS, members it reads from a shared global like `Racer.world`). It is flagged so wiring gets split (events, smaller interfaces) instead of piling into a few core files. Tune it with `coupling_weight_threshold` in `--config`.

The **Structural Overhead Tax** lowers the purity score (it never flags a file) when over 60% of a file's functions are trivial wrappers: at most 1 decision and 4 effective lines. The point is to discourage splitting code into many tiny functions just to stay under the branch limit.

Files over 1 MB, with a line over 5,000 characters, are **not checked** (huge arrays and statement lists are fine: they are read one child at a time). They are left out of the purity score and listed under `errors` in the JSON, in the warnings and in the PR comment, as `{error code: explanation}` with the setting that fixes it (for example `max_file_bytes = 3000000` in a `--config` TOML file). Minified bundles land here; add them to your ignore file. If every file is refused, the result says so, since a score of 100 would mean nothing.

## What it scans

- `.js .jsx .mjs .cjs .ts .tsx .mts .cts .py`
- Inside a git repo, anything `.gitignore` ignores is skipped. Vendored and generated paths (`node_modules`, `dist`...) are skipped by default.
- Override any setting with `--config argon.toml` (every field of `EngineConfig`, see `argon/engine/config.py`). Thresholds are fitted data in `engine/calibrated.py`.

## Limits worth knowing

- It measures structure, not correctness or style. It will not catch a bug.
- Token counts and dollar figures from `tools/demo_cost.py` are estimates; see that file's header.
- One long fluent chain such as `.on().on()` is counted as several async boundaries.
- No import-cycle detection yet.

## More

- [`SCORING.md`](SCORING.md): the full math and every rule decision
- `docs/`: build the audit page with `python -m tools.build_docs`
- Tests: `pip install -e ".[dev]" && pytest`
