# Argon

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
- After editing source files, run: `argon <the files or folders you changed> --top 10`
- If a file is flagged, or a function you wrote appears with a `nested` flag, refactor it
  before finishing: early returns, small named helpers, one job per function.
- Do not satisfy Argon by packing code onto fewer lines or by splitting one function into
  many trivial wrappers; the score counts characters and statements, not just newlines.
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

Files over 1 MB, with a line over 5,000 characters, or with one node holding over 50,000 children (generated data) are flagged `oversize` and not parsed. Minified bundles land here; add them to your ignore file.

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
