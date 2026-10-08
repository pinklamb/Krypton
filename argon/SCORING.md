# How Argon scores code

This is a plain-language guide to the scoring engine. The *Argon MVP Specification: Local Analysis Engine* is the source of truth, and its section numbers are cited throughout (§6.1 and so on). Loophole IDs such as A7 or B3 refer to its review table in §2.

Every file produces two numbers. On top of them sit per-function ratings, three flag rules and a 0–100 purity score.

| Number | What it is | Used by |
|---|---|---|
| **Cyclomatic branches** | A flat count of decisions, +1 each, with no +1 base per function. Matches radon 6.0.1 and ESLint's `complexity` rule. | `rating_master` |
| **Nesting score** | The same decisions, weighted by how deeply they're nested, with a tax on nested functions. | `rating_nesting`, the function ranking, the `nested` flag |

Everything comes from one iterative pass, `analyze_tree()`. Each rule lives in its own module:

| Module | What it does |
|---|---|
| [`branches.py`](argon/engine/branches.py) | decides what counts as a decision |
| [`nesting.py`](argon/engine/nesting.py) | decides what nests |
| [`scopes.py`](argon/engine/scopes.py) | handles functions and their tax |
| [`collectors.py`](argon/engine/collectors.py) | counts outputs, async boundaries and imports |
| [`lines.py`](argon/engine/lines.py) | counts effective lines |
| [`rules.py`](argon/engine/rules.py) | computes ratings, flags and the purity score |

---

## 1. What counts as a decision (§6)

| Counted (+1 each) | Not counted |
|---|---|
| `if`, `elif`, `else if` | plain `else` after an `if` |
| `for`, `for…in` / `of`, `while`, `do…while` | `try`, `finally`, `with` |
| a Python `else` on a `for`, `while` or `try` | `default:` in a `switch` |
| each `except` / `catch` (including `except*`) | the first catch-all `case _:` / `case name:` with no guard |
| each `case` in a `switch` or `match` | `match` guards (`case 1 if x:`) |
| ternaries: `a ? b : c`, `b if a else c` | `?.` and default parameters (`?.` calls can be switched on: `count_optional_calls`) |
| `and`, `or`, `&&`, `\|\|`, `??`, `&&=`, `\|\|=`, `??=` | `assert` and its `and`/`or` **in test files** (B9) |
| `assert` | |
| each `for` and `if` clause in a comprehension | |

Test files are still scored, so moving code under `tests/` doesn't hide it (A10).

**Deliberate differences from radon 6.0.1:**
- `except*` handlers count +1 each.
- `and`/`or` inside an `assert` count.
- A guarded catch-all such as `case _ if x:` counts, because §6.2 says a catch-all has no guard. radon counts it as free. The spec's §6.4 table doesn't list this one yet.

---

## 2. Weight

- **A structure at depth *d* costs *d* + 1.** Depth starts at 0, and a structure is any decision except a plain boolean operator.
- **A boolean operator costs a flat +1,** unless its value is thrown away.
- **Control-flow booleans (A7):** `ok && run()` written as a statement is an `if` in disguise, so it's scored as a structure and its right-hand side counts as its body. That makes `ok && (b && (c && run()))` cost 1 + 2 + 3 = 6, the same as three nested `if`s.

```python
if a:                # depth 0 → +1
    if b and c:      # depth 1 → +2, plus the `and` → +1
        if d:        # depth 2 → +3
            ...
# nesting score 7, flat count 4
```

---

## 3. What nests (§7.2)

**One level deeper:**
- the body of an `if`, `elif` or `else`
- a loop body
- an `except` / `catch` body
- a `case` body
- either side of a ternary
- the right side of a control-flow boolean

**The same level:**
- **Conditions,** `elif`/`else if` chains, and unparenthesized ternary chains (`a ? b : c ? d : e`). A parenthesized one, `a ? b : (c ? d : e)`, nests.
- **`try`, `with` and `finally`.**

---

## 4. Comprehensions are scored as their unrolled loop (§7.3)

```python
rows = [f(x) for a in b if c for x in a]      # 1 + 2 + 3 = 6, the same as:
for a in b:              # +1
    if c:                # +2
        for x in a:      # +3
            rows.append(f(x))
```

A comprehension in a loop header (`for r in (p for p in ps if p):`) runs once, before the loop, so it sits at the loop's own depth.

---

## 5. Functions, lambdas and classes (§8)

**The isolation tax.** Each function, method, lambda, arrow function and class is scored on its own scale, starting again from depth 0. The enclosing code pays **inner score × (1 + definition depth)**, and that's the only way the inner score is counted.

```python
def outer(a, b):
    if a:                # +1
        def inner():     # defined at depth 1 → outer pays inner × 2
            if b:        # inner: +1
                pass
# inner = 1;  outer = 1 + 1 × 2 = 3   (flat: 2)
```

**Which functions get a row (§8.1).** Only named functions get a row in the ranking: declarations, methods, and functions that are the direct value of a `const x =`, an object key, a class field or a Python `name = lambda`. Callbacks, IIFEs and lambdas passed as arguments are anonymous. They're still taxed into their parent, and their branch counts and depth merge into the parent's row. Classes get no row either; methods show as `Cart.total`.

**Effective depth (A6).** Scores restart at depth 0 inside each function, but the `nested` flag carries depth through closures. So a callback inside `if (a)` that holds `if (b) { if (c) }` is reported at depth 3, and wrapping each level in an arrow function doesn't hide a nest.

**Saturation (B3).** The tax multiplies at each level, so scores are capped at `score_cap` (1,000,000) and marked `saturated`.

---

## 6. Per-function ratings (§10.4)

Every named function, plus the module-level code as a `<module>` pseudo-function, gets two ratings:

```
rating_master  = branches² × effective_lines_altered ÷ outputs_effective
rating_nesting = score     × effective_lines_altered ÷ outputs_effective
```

- **Effective lines (A4, amended Oct 2026):** each non-blank, non-comment line weighs `max(1, statements starting on it, ceil(chars / 40))`. Packed code like `a(); b(); c();` counts 3, and normal formatted lines still count 1. Docstrings count as comments.
- **Outputs (A1, B7):** distinct returned / yielded values in the function's own scope. Code after an unconditional `return`, `throw` or `raise` is ignored. The count is capped at `branches + 1`, so padding with extra `return` lines can't shrink the rating.
- **The file's rating** is its worst function's rating in the active `rating_mode`, which is `"master"` until calibration.

---

## 7. Flags and the purity score (§10)

**Flags** (one reason per file, first match wins):

| Order | Flag | When it applies |
|---|---|---|
| 1 | **unparseable** | more than 5% of the file sits in syntax-error or missing nodes (C1, C2) |
| 2 | **concentration** | more than 800 effective lines **and** more than 3 async boundaries |
| 4 | **semantic path** | the file's rating is over the calibrated threshold for the active mode (see below) |

**Fragmentation** is a PR-wide flat −25 penalty. It fires when the largest group of files that import each other has 3 or more files and more than 4 imports per file. Imports are resolved however they're written: relative paths, tsconfig aliases, or Python absolute or relative imports.

**Purity score:**

```
score = 100 − 70 × flag_ratio − weighted_overshoot − 25 × fragmentation
```

- Both terms are weighted by each file's effective altered lines, so trivial padding files can't dilute them (A5).
- Overshoot is 10 points per 10× over the threshold, capped at 30 (B2).
- The result is rounded with `floor(x + 0.5)` (B6). A score below 40 means `failure`.

---

## Calibration

The thresholds aren't hard-coded. They live in [`engine/calibrated.py`](argon/engine/calibrated.py), which is generated from data:

```
python -m argon.cli <human-written code>  --calibrate-out calibration/human.csv
python -m argon.cli <AI-generated code>   --calibrate-out calibration/agentic.csv
python -m argon.cli --fit-thresholds calibration/human.csv calibration/agentic.csv
```

For each rating mode, the fit picks the threshold that best separates the two sets: the share of AI-generated functions flagged minus the share of human-written functions flagged. The mode with the better separation becomes the default, with ties going to `master`.

The current values (`rating_master` 648, `rating_nesting` 162) come from the repo's two synthetic fixtures. Refit on real repos before gating real pull requests.

---

## Verification (§12)

| Check | Result |
|---|---|
| Spec §12.1 fixtures and §12.2 loophole / score-math tests | all pass (A4 uses the amended target below) |
| Flat counts vs radon 6.0.1 (A7 off, outside test paths) | 14,051 files match, `pytest -m parity` |
| Nesting scores vs an independent `ast`-based reference | same Python corpus |
| Flat counts vs ESLint `complexity` | 2,505 JS/TS files, 0 mismatches |
| Engine does no I/O; 1,000 files under 30 s | ✓ (about 7.5 s) |

---

## Spec amendments (Oct 2026)

These override the MVP specification PDF. The PDF itself hasn't been edited.

| Spec section | Original | Amended | Why |
|---|---|---|---|
| §9.4 effective lines (A4) | each line counts `ceil(chars / 100)` | `max(1, statements starting on the line, ceil(chars / 40))` | At width 100, a function packed onto one line kept 31% of its effective lines, because lines average about 30 characters. |
| §12.2 A4 test | packed code keeps ≥ 70% of the formatted version | packed code keeps **≥ 60%** | The 70% target was unreachable: packing keeps every statement but sheds the extra weight of long formatted lines. A 16-line function keeps 63%, against 31% before the change. |
| §6.4 differences from radon | `except*`, `and`/`or` in `assert` | also: a guarded catch-all `case _ if x:` counts +1 (radon treats it as free) | §6.2 already says a catch-all has no guard, so a guarded one is a real decision. |
| §10.4 `<module>` rating | not defined | rates top-level statements, anonymous closures and class bodies only; named functions are rated on their own | A module score that included every function would always be the worst "function" in the file. |
| §5.1 parsing code | imports `pathlib` | `posixpath` only | §3's rule forbids `pathlib` in `engine/`. |
| §10.1 thresholds | placeholders at 40 | fitted values in `engine/calibrated.py` | See Calibration above. |
| §9.4 per-function lines | a function's lines are its physical lines minus nested named functions' lines | a function owns the **characters** of its span minus nested named functions; a line shared by several functions is split by their share of its code characters | Whole-line attribution gave an outer function 0 lines and every one of 50 siblings on a line the full line. |
| §10.1 / C4 oversize | only `max_file_bytes` (1,000,000) | also `max_line_chars` (5,000): any longer line is not analyzed (see *Files too large to check*) | ast-grep's `range()` slows with the column, so one long line was quadratic in time, and a wide node needs hundreds of MB. |
| §10.1 / C4 oversize | also `max_line_chars` | also `max_children` (50,000): a node with more children, found during the walk, makes the file not analyzed (see *Files too large to check*); later narrowed by the sibling walk below, probed with `child(n)` so no wrappers are built | One-element-per-line arrays passed the line guard and needed ~600 MB, because `children()` makes a Python wrapper per child. |
| §11 CLI / §3.2 schema | no change-frequency data | `FileFinding.change_frequency` (0.0 stable to 1.0 volatile, `None` = not measured) and `--order-by-volatility` / `--volatility FILE.json`; see *Volatility index* below | Modularity only helps a prompt cache when stable files come first. The index never changes a score. |
| §10.2 flag rules | concentration, semantic path | also `coupling_hotspot` (lowest priority, never replaces another flag): coupling weight over `coupling_weight_threshold` (12) and at least `coupling_min_lines` (40) effective lines; see *Coupling hot spots* below | Version B of the racing-game audit passed 100/100 while `world.js` and `config.js` were touched by every feature. |
| §10.3 purity score | flag ratio and overshoot only | also subtracts the **Structural Overhead Tax**, weighted by altered lines like overshoot; see below | Version B of the racing-game audit was 56% larger in tokens, partly from many tiny functions. A tax that lowers purity, never a flag. |
| §10.1 / C4 fail closed | a file over a size limit is flagged `oversize` and scored as a failure | it is not analyzed, not scored, and listed in `EngineResult.errors` as `{code: explanation}`; the purity score is computed without it | A legitimate large file should not drag the score down, and must never pass silently. |
| §10.2 `--base` diff | hunk headers matched anywhere in the diff | hunk bodies are consumed by the counts in each `@@` header; git runs with `core.quotepath=off` and explicit prefixes, trailing tabs and quoted paths are decoded | An added line starting `++ ` read as a file header, and files with spaces or non-ASCII names were silently dropped. |

---

## Volatility index (`engine/volatility.py`)

Not part of the score. It orders files so a tool that builds prompts can put stable files first and keep a long shared prefix for the provider's prompt cache.

- **Source.** `--order-by-volatility` counts the commits touching each scanned file among the last `volatility_window_commits` (default 200) from `git log --name-only`. `--volatility FILE.json` replaces git with `{"path": change_count}`. Reading history is the CLI's job (`argon/history.py`); the engine only receives counts.
- **Index.** `count / busiest scanned file's count`, rounded to 4 places, so 0.0 is "never changed in the window" and 1.0 is the busiest. A file that is modified, staged or untracked right now is 1.0 regardless of history, because it is in flux by definition. In a repo with no commits yet everything is 1.0.
- **Manifest.** `--order-by-volatility` prints JSON instead of the report: `files` from most stable to most volatile (ties broken by name), each with `change_frequency`, `flagged` and `tokens_estimate` (characters ÷ 3.5, a rough estimate). Scores and the exit code are unchanged.
- **What it is not.** History predicts what will change next only roughly, and it helps only workflows that re-send the whole file set each turn. In an append-only agent session edits do not invalidate the cache. Treat the manifest as a hint, not a guarantee of cache hits.

---

## Coupling hot spots (`engine/coupling.py`)

A file's **coupling weight** is the number of distinct scanned files it depends on plus the number of distinct scanned files that depend on it. Two kinds of dependency count, each pair once:

| Edge | Where it comes from |
|---|---|
| import | resolved `import`, `require`, dynamic `import()` and Python imports (the same edges the fragmentation rule uses) |
| shared global *(prototype, JS/TS only)* | a name that at least `namespace_min_files` (2) files assign members on at top level, such as `Racer.world = {...}` and `Racer.player = {...}`. Any file that reads `Racer.world` depends on the file that assigns it. |

A file is flagged `coupling_hotspot` when its weight is **over 12** and it has **at least 40 effective lines**, so a tiny barrel file that re-exports many modules is not flagged. It only applies when no other rule flagged the file, and it counts like any flag in the purity score. `relative_import_count` and the fragmentation rule still use import edges only.

Calibration: on the racing game's final Version B (23 files, no imports at all) the weights were `config.js` 19, `world.js` 18, then 6 or less for everything else, so 12 sits in a wide gap. Version A is one file (weight 0). Raise `coupling_weight_threshold` in a `--config` file if your project is legitimately more connected.

Limits of the shared-global detector: it recognises `Name.member` as a plain identifier plus property, and it cannot tell a real global from a local object that happens to be assigned in two files. It does not see `window.x`, `globalThis.x`, `<script>` order, or HTML files, and it does not exist for Python.

---

## Structural Overhead Tax (`engine/rules.py: overhead_points`)

Lowers a file's purity contribution when most of its named functions are trivial wrappers. It never flags a file.

- **Trivial wrapper:** a named function with at most `wrapper_max_branches` (1) decisions and at most `wrapper_max_lines` (4) effective lines. This is an approximation: it does not check that the body only delegates to another function, so a short getter counts.
- **Density:** trivial wrappers ÷ named functions in the file. Files with fewer than `wrapper_min_functions` (6) functions are never taxed.
- **Tax:** 0 up to `wrapper_free_density` (0.6); above that it rises linearly to `wrapper_tax_max_points` (15) at density 1.0. `FileFinding.overhead_tax` holds the points and `trivial_wrapper_count` the count.
- **Purity:** `100 − 70 × flag_ratio − overshoot − overhead`, where overhead is the file taxes weighted by effective altered lines, like overshoot.

Calibration (1,500 random Python files from site-packages, files with 6 or more functions): the share of files with density above 0.5 / 0.6 / 0.7 was 15.6% / 9.3% / 5.4%. At 0.6 the racing game's Version B still has 3 taxed files (`health.js` 6 of 6 wrappers, `input.js` and `weather.js` 5 of 7) and its purity moves 86 to 85; Version A has none. So roughly 1 in 11 ordinary files would lose some points, up to 15 for a file made only of wrappers. Raise `wrapper_free_density` or lower `wrapper_tax_max_points` in a `--config` file if that is too many for your code.

---

## Files too large to check

Three limits keep a run fast and bounded. A file that passes one is **not analyzed and not scored**: it is absent from `findings`, and the purity score is computed from the other files. It is never silent. `EngineResult.errors` is a map of error code to a one-sentence explanation, one entry per code, with up to five filenames, the limit passed and the exact setting that fixes it; the report prints the same text as `WARN` lines and in the PR comment.

| Code | Limit (default) | Fix |
|---|---|---|
| `E_FILE_TOO_LARGE` | `max_file_bytes` (1,000,000) | raise it to the next whole megabyte above the largest file |
| `E_LINE_TOO_LONG` | `max_line_chars` (5,000) | raise it, or list minified code in `generated_path_globs` |
| `E_NODE_TOO_WIDE` | `max_children` (50,000) | raise it; only nodes whose children each need their own depth rule (a `case` body, an `if` or loop body) with that many statements |
| `E_NOTHING_CHECKED` | (no limit) | present when every file was refused, since a score of 100 then means nothing |

A file over both the byte and the line limit gets both codes. Put the settings in a TOML file and pass it with `--config`. Time and memory grow roughly in a straight line with file size: on Python, about 2 seconds and 55 MB per extra megabyte (a 3 MB file took 5.5 s and 207 MB; 12 MB took 21 s and 728 MB). Unparseable files are different: broken syntax still fails closed and counts against the score.

### Wide nodes are walked one child at a time

A node with more than `lazy_children_threshold` (512) children, such as a long array, object, argument list, class body or statement list, is no longer fetched as a list of child objects. The walk keeps one pending child and steps to each next sibling (`child.next()`), so memory stays flat however wide the node is: on a 300,000-element array the walk allocated about 2 KB, against about 300 MB when the children were fetched together. Depths, the unreachable-code rule after a `return`, and scope handling are identical; it applies only to node kinds whose children all sit at the node's own depth. Nodes that need a per-child depth rule (`switch` cases, `if` and loop bodies, `else`, `except`, comprehensions, boolean control flow) are still read together and keep the `max_children` cap. The per-line effective-line tables still grow with the number of lines (about 160 bytes per line).

