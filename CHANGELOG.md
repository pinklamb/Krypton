# Changelog

## 2.1.0

A review step for the work of coding agents, and the pull request pieces around it.

### The client
- `krypton scan --log [--ticket TEXT]` adds each run to `.krypton/runs.jsonl` (purity and, for every function at or over the limit, how many times over it is, under its real name) and saves the state of the files as a patch in `.krypton/attempts/`. The `.krypton/` folder writes its own `.gitignore`, so the log and the saved code cannot be committed by accident.
- `krypton review [--json]` names the functions that are still over the limit after the start and three runs and never got 10% better, with their score at every run, the best run, and the purity if the best run is put back. This is the old stalled hand-off, rebuilt.
- `krypton restore --run N` puts the files back to run N from its saved patch. It refuses when the files changed since the last logged run, `--dry-run` only checks, and a patch that does not apply cleanly leaves everything as it was.
- `krypton scan --summary FILE`, `krypton comment BEFORE AFTER [--badge FILE] [--fail-if-worse]` and `krypton badge SUMMARY OUT` write the pull request comment (one comment, edited in place) and the README badge.

### Around it
- `action.yml`: a GitHub Action that scores a pull request against its base, posts or updates the comment, and refreshes the badge on a push.
- `themes.md` and `agent/review-brief.md`: your coding agent builds a local review page from them (the outline is fixed; `themes.md` only styles it). Krypton itself builds no page. See `site/review.html` and `site/review-preview.html`.

## 2.0.0 (Rust)

Krypton is now written in Rust, in two parts.

### The client (`client/`, public)
- `krypton scan REPO` parses every Python, JavaScript, TypeScript and TSX file on your machine with tree-sitter, sends **only the syntax tree** (node kinds and positions, line shapes, fingerprints of names) and prints the server's review. `--level`, `--base REF` (only the lines a branch changed), `--names` (real names put back locally), `--json`, `--save`.
- `krypton order REPO` reads git history locally and asks the server whether a different file order would keep more of a prompt cache; only file names, token counts and which files each commit touched are sent. `--model` counts file sizes in that model's own tokens: offline with the real `o200k_base` / `cl100k_base` vocabularies for OpenAI models, or with Anthropic's token counter for Claude models (using your own `ANTHROPIC_API_KEY`; that sends file text to Anthropic, never to Krypton). Without a model it estimates and says so.
- `krypton wire` prints the exact request body instead of sending it, and `--stdin-json` builds it from source held in memory, so a program never needs to write a repository to disk.
- No fallback anywhere: a file that cannot be read, parsed or sent is an error with a reason and a non-zero exit code.

### The server (private)
- An axum server: `POST /v1/score`, `POST /v1/order`, `GET /v1/capabilities`, `GET /v1/health`. It has no accounts or API keys, limits each address to a few requests a minute, refuses `detail: "full"` unless started to allow it, and stores nothing.
- The scoring engine is a Rust port that gives the same answers as the Python engine it replaces on every check: 976 source files, 388 requests, 6,000 damaged trees, 2,516 ordering requests, and the garth repository at all three levels.
- Memory is budgeted: each request reserves its share of the container's memory from the size its gzip stream declares, before anything is decompressed, so an oversized or lying upload is refused with a 413 (a 300 MB gzip bomb peaks at 5 MB), and large scans queue instead of running all at once (six 110,000-line scans at once peak at 300 MB in a 512 MB container).
- Scoring runs off the async threads, one job per core, and the permit is held until the work really finishes; when too many requests are waiting the answer is a clean `503 busy` with `Retry-After`, so a rush of requests cannot pile up memory or oversubscribe the cores.
- Damaged uploads get a clean 4xx with the path of the problem and never a panic or a wrong score (a mutation fuzz test runs thousands of them on every build); a bug the fuzz found, line lengths that wrapped around and slipped under the size limit, is fixed.

### The demo
- The hosted demo now builds each repository's syntax tree itself and sends only the tree to the scoring server. GitHub does the authenticating; the visitor's GitHub token is forgotten when they sign out or after an hour.

### Removed
- The Python engine and the `krypton` Python package. The Rust server replaces them, and the scoring maths now lives only on the server.

### Not rebuilt yet
- `--top`, `--no-worse`, `--config` on `scan`, and the loading animation of the Python command line (`--history` and `--handoff-stalled` are now `--log` and `krypton review`).
- An MCP tool for agents.

## 1.x (Python)

The earlier Python command line (`krypton`, version 0.5.0 as last released) is in the git history.
