# Changelog

## Unreleased

- `krypton scan` at a terminal is short: while it runs, the crystal draws itself beside the stage (reading the files, building the trees with a count, scoring on the server); then the closed crystal sits beside the purity score and a bar, with one line of what it means and the five most tangled functions under their real names. `--full` prints the full review with every hint, and a pipe, an agent, CI and `--json` get exactly what they got before. The loader now clears each line it redraws, so nothing is left behind.

- The pull request now shows the score itself: the Action keeps **one** Krypton comment on each pull request with the scorecard, the purity before and after in lavender, and edits it on every push instead of adding another (found by a hidden `<!-- krypton-gate -->` marker). `comment: "false"` turns it off; it needs `pull-requests: write`, which the workflow `krypton setup` writes includes, and without it the comment is skipped with a warning, never a failed check. The final step now says why the gate failed (the target rule or a lower score).
- `krypton setup` asks a fifth question, the pull request gate (not now, never worse, or target), and writes `.github/workflows/krypton.yml` for it, never overwriting one that exists; `--gate off|worse|target` skips the question. The Action gains a `target` input: the files a pull request changed must score at least that, and old code is never judged (`krypton comment --target N --changed FILE`).
- The scan follows `.gitignore` (git's own rules, nested files and `!` included) and a new `.kryptonignore` in the same syntax for more paths to leave out.
- `krypton setup` suggests the target from today's score: a step above it, never lower than a target saved before.

- `krypton setup`, run once per repository (the first `docker run -it ... krypton` starts it): maps the whole codebase and shows its purity today, then asks for a **target** purity per ticket (set it above today's score so each ticket lifts the code it touches), **max tries** before a function counts as stuck (1-10, default 3), and what happens then: **ask me** (a local review page for the current ticket, styled by `themes.md`; a starter file is written if you have none) or **auto** (`krypton review` puts back the best run). Kept in `.krypton/settings.json`; flags `--mode --target --runs --no-map` skip the questions. `krypton review --auto|--manual` overrides the mode once. The review JSON adds `mode`, `target`, `max_runs`, `meets_target` and `auto_approved`.
- `krypton setup` asks first where to choose the settings: in the terminal, or on a local settings dashboard. The dashboard is a one-time link (`http://localhost:7777/<secret>`) served by the CLI itself; it stops once you save. `--page` goes straight to it, `--listen HOST:PORT` (or `KRYPTON_LISTEN`) moves it. In Docker, publish the port with `-p 127.0.0.1:7777:7777`; the README helper does this for `setup`.
- Names are keyed. Every name and compared expression is now sent as HMAC-SHA256 under a 256-bit key made fresh for each scan, held in memory and thrown away, so nobody can recover a name by hashing guesses or link two requests. Only the short public list of framework names the server recognises (`addEventListener`, Celery's `task`, ...) keeps the plain hash. Scores are unchanged (checked on a 145-file codebase: same purity, same 1,115 functions). `KRYPTON_KEY` (64 hex) fixes the key for reproducible `krypton wire` output.
- The terminal crystal is lavender now, and setup opens with it.
- The review page your agent builds now has a Settings panel (target, max tries, ask me / auto), styled by `themes.md`; it saves through `krypton setup`, so the CLI checks every value.
- `krypton order` now tries several file orders against your git history (stable files first, big files that rarely change first, least recently changed first, alphabetical, reverse alphabetical) and uses the one that would have kept the most of the prompt cached, still only when it clearly beats your own order. The summary lists every order tried; `--json` adds `strategy` and `tried`.
- Login. The server now scores only for a logged-in user or a GitHub Actions run. `krypton login` uses GitHub's device flow (a short code you approve at github.com/login/device); the server trades GitHub's answer for a 30-day Krypton token and keeps no GitHub token. `krypton setup` logs you in first, the token lives in `$KRYPTON_HOME/credentials` (the Docker helper keeps it in the `krypton-home` volume), `KRYPTON_TOKEN` overrides it, and `krypton logout` removes it. In CI, `krypton login --github-actions` trades the run's own OIDC identity (`permissions: id-token: write`) for a one-hour token, so no secret is stored.
- Pull requests get one line and no comment: the Krypton gate line now also says how many functions got worse (`Purity ▲ 71% → 74% (+3) · 2 functions worse`), its Details opens the repository's tree on the Krypton site when the Action's new `site` input is set, and the scorecard goes to the Actions run summary. The Action no longer needs `pull-requests: write`.
- Removed the README purity badge: `krypton badge`, `krypton comment --badge`, the Action's push step and its `badge-branch` input, and the `contents: write` permission it needed. The pull request comment and the Krypton gate line stay.
- A "Krypton gate" line in the checks on every pull request, such as `Purity ▲ 71% → 74% (+3)`: the purity before and after and the change, at a glance. It fails only when `fail-on-worse` is on and the score went down. `krypton comment --line FILE` writes the line; the action needs `statuses: write`.

## 2.2.0

- A green crystal draws itself in the terminal while `krypton scan` runs, and closes when the run is done. It writes to standard error only, appears only on a real terminal (never in a pipe, in CI, on a dumb terminal or with `--json` output), erases itself before the report prints, and gives the cursor back even when the run fails. `--no-loader` or `KRYPTON_NO_LOADER` turn it off; `NO_COLOR` draws it without colour.
- The guardrail angle: the docs and the demo now lead with the purity score and the idea that every fast agent needs a limit, and explain how to compare models by what they do to your own code. The demo has a fifth step, Models.

## 2.1.0

A review step for the work of coding agents, and the pull request pieces around it.

### The client
- `krypton scan --log [--ticket TEXT]` adds each run to `.krypton/runs.jsonl` (purity and, for every function at or over the limit, how many times over it is, under its real name) and saves the state of the files as a patch in `.krypton/attempts/`. The `.krypton/` folder writes its own `.gitignore`, so the log and the saved code cannot be committed by accident.
- `krypton review [--json]` names the functions that are still over the limit after the start and three runs and never got 10% better, with their score at every run, the best run, and the purity if the best run is put back. This is the old stalled hand-off, rebuilt.
- `krypton restore --run N` puts the files back to run N from its saved patch. It refuses when the files changed since the last logged run, `--dry-run` only checks, and a patch that does not apply cleanly leaves everything as it was.
- `krypton scan --summary FILE`, `krypton comment BEFORE AFTER [--badge FILE] [--fail-if-worse]` and `krypton badge SUMMARY OUT` write the pull request comment (one comment, edited in place) and the README badge.

### Around it
- The client talks to the hosted Krypton server by default, so there is no server address to set. `--server` or `KRYPTON_SERVER` still point it somewhere else.
- `client/Dockerfile`: build the client as a local Docker image and run it with `docker run`; nothing else to install.
- The documentation is one scrolling guide (get started, how it works, what the server receives, review) with a simpler navigation; the detailed pages are under Reference.
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
- An axum server: `POST /v1/score`, `POST /v1/order`, `GET /v1/capabilities`, `GET /v1/health`. It refuses `detail: "full"` unless started to allow it.
- The scoring engine is a Rust port that gives the same answers as the Python engine it replaces on every check: 976 source files, 388 requests, 6,000 damaged trees, 2,516 ordering requests, and the garth repository at all three levels.
- Memory is budgeted: each request reserves its share of the container's memory from the size its gzip stream declares, before anything is decompressed, so an oversized or lying upload is refused with a 413 (a 300 MB gzip bomb peaks at 5 MB), and large scans queue instead of running all at once (six 110,000-line scans at once peak at 300 MB in a 512 MB container).
- Scoring runs off the async threads, one job per core, and the permit is held until the work really finishes; when too many requests are waiting the answer is a clean `503 busy` with `Retry-After`, so a rush of requests cannot pile up memory or oversubscribe the cores.
- Damaged uploads get a clean 4xx with the path of the problem and never a panic or a wrong score (a mutation fuzz test runs thousands of them on every build); a bug the fuzz found, line lengths that wrapped around and slipped under the size limit, is fixed.

### The demo
- The hosted demo now builds each repository's syntax tree itself and sends only the tree to the scoring server. GitHub does the authenticating; the visitor's GitHub token is forgotten when they sign out or after an hour.

### Removed
- The Python engine and the `krypton` Python package. The Rust server replaces them, and the scoring maths now lives only on the server.

### Not rebuilt yet
- `--top`, `--no-worse`, `--config` on `scan` (`--history` and `--handoff-stalled` are now `--log` and `krypton review`).
- An MCP tool for agents.

## 1.x (Python)

The earlier Python command line (`krypton`, version 0.5.0 as last released) is in the git history.
