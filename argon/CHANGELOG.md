# Changelog

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
