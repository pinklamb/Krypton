## Code structure (Argon)
- After editing source files, run: `argon <the files or folders you changed> --top 10`
- If a file is flagged, or a function you wrote appears with a `nested` flag, refactor it
  before finishing: early returns, small named helpers, one job per function.
- Do not satisfy Argon by packing code onto fewer lines or by splitting one function into
  many trivial wrappers; the score counts characters and statements, not just newlines.
- Re-run Argon after refactoring and report the purity score.

Run from the repo root: `argon version_b --top 10` (exit 0 = purity >= 40; target here is 100/100).
Modules are classic scripts sharing the `Racer` namespace; keep `index.html` script order in sync when adding files.
