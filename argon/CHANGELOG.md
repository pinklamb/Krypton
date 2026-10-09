# Changelog

## 0.5.0

### Harder to game
- **Decision floor:** a file is flagged `function_overload` when any function has at least 30 decisions or a nesting score of 100, whatever its return values or size. Before, giving every branch a different return value divided the rating by up to 41 and hid the function; packing code onto fewer lines cannot hide it either.
- **File budget:** a file is flagged `file_overload` when its total nesting score is at least 600, so spreading one heavy function across helpers in the same file does not lower it.
- **Small tangles count as fragmentation:** four or more small files (under 60 effective lines each on average) that import each other more than twice per file now trigger the fragmentation penalty. Four cross-importing small files used to pass; larger files still need the old density (5 imports per file).
- New hints for both flags. The flags rank below `semantic_path_logic` and above `coupling_hotspot`, and all limits are config settings.
- Calibrated on 3,000 Python and 3,000 JS/TS library files: the decision floor flags 0.3% of Python and 1.0% of JS/TS functions, the score limit 0.1% and 0.4%, the file budget 0.5% of Python files, and the small-tangle rule none of 151 Python packages.

### Fixed
- The summary line `N of M functions are over the threshold` left out each file's top-level code, so a file whose worst part was module-level code (such as `app.js` in a real repo, 27.6x over) understated both counts.

### Verification
- 217 tests (208 in 0.4.0). The radon and ESLint parity corpora pass (0 mismatches): no counting or nesting rule changed.

## 0.4.0

### Hints and progress
- Refactoring hints: for each heavy or deeply nested function, the deepest decision, the block worth extracting with its share of the weight, and advice chosen by shape (early returns, lookup table, predicate helpers, split into steps). `EngineResult.hints`, the CLI and the PR comment. A hint that names a block also predicts the ratings after extracting it (it matched the real ratings exactly on the test fixture).
- `--history FILE.jsonl`: logs each run and reports progress since the last one (purity trend, per-function improvement). A function that did not improve gets a reason: not edited, the hinted block untouched, the weight moved to another function, or the block changed without help. Three runs without improvement are called STALLED. The log keeps ratings, names and line hashes only, never source.

### Distance to clean and levels
- The summary says how many functions are over the threshold and how far over the worst is; each hint leads with its own multiple. `EngineResult.threshold` carries the number in force.
- `--level relaxed|standard|strict` scales the flag thresholds by 3, 1 and 0.5, so teams can clear the worst offenders first and tighten later. No saved baseline is involved.

### Internal
- Several helpers were flattened so Argon's own code carries no `nested` flag.
- 208 tests (183 in 0.3.0).

## 0.3.0

### Accuracy
- Functions that share a physical line split its weight by their share of its characters. Before, an outer function got 0 effective lines and each sibling got the whole line.
- `--base` reads hunk counts from the diff. Files with spaces or non-ASCII names are no longer dropped, and an added line starting with `++ ` is no longer read as a file header.

### Large files
- Files over `max_file_bytes` (1 MB), with a line over `max_line_chars` (5,000), or with a node over `max_children` (50,000) are not analyzed and not scored. `EngineResult.errors` is a map of error code to explanation (`E_FILE_TOO_LARGE`, `E_LINE_TOO_LONG`, `E_NODE_TOO_WIDE`, `E_NOTHING_CHECKED`) that names the files and the setting that would include them. The `oversize` flag is removed.
- Nodes with more than 512 children (arrays, objects, argument lists, statement lists, class bodies) are walked one child at a time with constant memory. The walk allocated about 2 KB on a 300,000-element array, against about 300 MB before.

### New measurements
- `coupling_hotspot` flag: a file wired to more than 12 other files and at least 40 effective lines, counting imports and, as a prototype for JS/TS, members read from a shared global such as `Racer.world`.
- Structural Overhead Tax: lowers the purity score (never flags) when over 60% of a file's functions are trivial wrappers.
- `FileFinding.change_frequency` and `--order-by-volatility` / `--volatility FILE.json`: files from most stable to most volatile, from `git log` or supplied counts. Never changes a score.

### Packaging and docs
- `argon` console command and a Dockerfile.
- README, a GitHub Pages site and a generated audit page.

### Verification
- radon and ESLint parity corpora (about 16,500 files): 0 mismatches. The sibling walk matched the bulk walk on 34,216 files.
- 183 tests (145 in 0.2.0).

## 0.2.0
- First local engine: branch counting verified against radon and ESLint, nesting scores, effective lines, per-function ratings, flags and the purity score.
