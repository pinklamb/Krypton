# Changelog

## Unreleased

### Renamed to Krypton
- The product is now **Krypton**. The command is `krypton`; the old `argon` command still works and runs the same program.
- Reports and PR comments are headed "Krypton Structural Review". The Python package is still imported as `argon` for now.

### Documentation
- New documentation site (getting started, how it works, command reference, integrations, prompt caching, FAQ).
- Public documentation describes what Krypton measures and why. The scoring and ordering algorithms are not published.

### Hosted demo
- A small web app shows what a repository costs to read and what a refactor could save. Visitors sign in with GitHub and pick a repository; it is read in memory, scored and discarded, and nothing from it is run or stored.

## 0.5.0

### Harder to game
- **Decision floor:** a file is flagged `function_overload` when any function makes an unusually large number of decisions or carries an unusually heavy nesting score, whatever its return values or size. Before, giving every branch a different return value hid the function; packing code onto fewer lines cannot hide it either.
- **File budget:** a file is flagged `file_overload` when its total nesting score is over budget, so spreading one heavy function across helpers in the same file does not lower it.
- **Small tangles count as fragmentation:** several small files that import each other heavily now trigger the fragmentation penalty. Cross-importing small files used to pass.
- New hints for both flags. The flags rank below `semantic_path_logic` and above `coupling_hotspot`, and all limits are config settings.
- Calibrated on 3,000 Python and 3,000 JS/TS library files so that the new flags are rare on well-written code.

### Fixed
- The summary line `N of M functions are over the threshold` left out each file's top-level code, so a file whose worst part was module-level code understated both counts.

### Verification
- 217 tests. The radon and ESLint parity corpora pass with no mismatches: no counting or nesting rule changed.

## 0.4.0

### Hints and progress
- Refactoring hints: for each heavy or deeply nested function, the deepest decision, the block worth extracting with its share of the weight, and advice chosen by shape (early returns, lookup table, predicate helpers, split into steps). `EngineResult.hints`, the CLI and the PR comment. A hint that names a block also predicts the ratings after extracting it.
- `--history FILE.jsonl`: logs each run and reports progress since the last one (purity trend, per-function improvement). A function that did not improve gets a reason: not edited, the hinted block untouched, the weight moved to another function, or the block changed without help. Three runs without improvement are called STALLED. The log keeps ratings, names and line hashes only, never source.

### Distance to clean and levels
- The summary says how many functions are over the threshold and how far over the worst is; each hint leads with its own multiple. `EngineResult.threshold` carries the number in force.
- `--level relaxed|standard|strict` scales the flag thresholds, so teams can clear the worst offenders first and tighten later. No saved baseline is involved.

### Internal
- Several helpers were flattened so Krypton's own code carries no `nested` flag.

## 0.3.0

### Accuracy
- Functions that share a physical line split its weight by their share of its characters. Before, an outer function got no effective lines and each sibling got the whole line.
- `--base` reads hunk counts from the diff. Files with spaces or non-ASCII names are no longer dropped, and an added line starting with `++ ` is no longer read as a file header.

### Large files
- Very large files, files with extremely long lines, and files with extremely wide nodes are not analyzed and not scored. `EngineResult.errors` is a map of error code to explanation (`E_FILE_TOO_LARGE`, `E_LINE_TOO_LONG`, `E_NODE_TOO_WIDE`, `E_NOTHING_CHECKED`) that names the files and the setting that would include them. The `oversize` flag is removed.
- Wide nodes (arrays, objects, argument lists, statement lists, class bodies) are walked one child at a time with constant memory.

### New measurements
- `coupling_hotspot` flag: a file wired to many other files, counting imports and, as a prototype for JS/TS, members read from a shared global object.
- Structural Overhead Tax: lowers the purity score (never flags) when most of a file's functions are trivial wrappers.
- `FileFinding.change_frequency` and `--order-by-volatility` / `--volatility FILE.json`: files from most stable to most volatile, from `git log` or supplied counts. Never changes a score.

### Packaging and docs
- Console command and a Dockerfile.
- README, a GitHub Pages site and a generated audit page.

### Verification
- radon and ESLint parity corpora (about 16,500 files): no mismatches.

## 0.2.0
- First local engine: branch counting verified against radon and ESLint, nesting scores, effective lines, per-function ratings, flags and the purity score.
