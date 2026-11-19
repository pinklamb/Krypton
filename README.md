# [Krypton](https://pinklamb.github.io/Krypton/)

**The guardrail for coding agents.** Site and docs: https://pinklamb.github.io/Krypton/

Agents are strong and fast, and every strong thing needs a check. Krypton is that check for code: it gives the code an agent writes a **purity score**, so you can see how bloated or pure it is to understand, after every change. It measures structure (decisions, nesting, bloated functions, tangled wiring) for JavaScript, TypeScript and Python. A small Rust client parses your code **on your machine** and sends only the syntax tree to a Krypton server, which scores it and sends back the review; your source never leaves your machine. It is built to sit beside a coding agent: after each change the agent runs Krypton, sees what got heavy, and splits it before the weight costs tokens and invites mistakes. Because the score is kept after every change, you can also see whether your codebase got more or less pure after you switched models, and choose by cost and by what each model does to your code. It is not a linter; run yours too.

You need Docker ([Docker Desktop](https://docs.docker.com/get-docker/) on macOS and Windows, Docker Engine on Linux) and git; nothing else.

Clone Krypton once, anywhere outside your project, and build its image (the first build takes a few minutes):

```bash
git clone https://github.com/pinklamb/Krypton.git ~/Krypton
docker build -t krypton ~/Krypton/client
```

In PowerShell, use `$HOME\Krypton` in place of `~/Krypton`. If you clone it elsewhere, set `KRYPTON_SRC` to that folder.

The krypton command (macOS, Linux, Git Bash; add it to ~/.bashrc or ~/.zshrc to keep it):

```bash
krypton() {
  local image="${KRYPTON_IMAGE:-krypton}" src="${KRYPTON_SRC:-$HOME/Krypton}" here="$PWD" status
  command -v docker >/dev/null || { echo "krypton: Docker is not installed. Install Docker Desktop (https://docs.docker.com/get-docker/), then run this again." >&2; return 11; }
  if [ "$1" = update ]; then
    git -C "$src" pull --ff-only || { echo "krypton: could not update the code in $src. Check your internet connection (and KRYPTON_SRC, if the clone is elsewhere), then run krypton update again." >&2; return 12; }
    docker build -t "$image" "$src/client" || { echo "krypton: could not build the image from $src/client. Check that Docker is running and has disk space, then run krypton update again." >&2; return 12; }
    echo "Krypton is up to date."; return
  fi
  command -v cygpath >/dev/null && here="$(cygpath -w "$PWD")"
  MSYS_NO_PATHCONV=1 docker run --rm --pull never $([ -t 0 ] && echo -it) $([ $# -eq 0 ] || [ "$1" = setup ] && echo -p 127.0.0.1:7777:7777) \
    -v krypton-home:/krypton-home -v "$here:/repo" "$image" "$@"
  status=$?
  if [ "$status" -eq 125 ]; then
    docker info >/dev/null 2>&1 || { echo "krypton: Docker is not running. Start Docker Desktop, then run this again." >&2; return 11; }
    echo "krypton: the $image image is not built yet. Run: docker build -t $image $src/client" >&2; return 12
  fi
  if [ "$status" -eq 75 ]; then krypton update; return; fi
  return "$status"
}
```

In PowerShell (add it to your `$PROFILE` to keep it):

```powershell
function krypton {
  $image = if ($env:KRYPTON_IMAGE) { $env:KRYPTON_IMAGE } else { 'krypton' }
  $src = if ($env:KRYPTON_SRC) { $env:KRYPTON_SRC } else { Join-Path $HOME 'Krypton' }
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine('krypton: Docker is not installed. Install Docker Desktop (https://docs.docker.com/get-docker/), then run this again.'); $global:LASTEXITCODE = 11; return
  }
  if ($args.Count -gt 0 -and $args[0] -eq 'update') {
    git -C $src pull --ff-only
    if ($LASTEXITCODE -ne 0) { [Console]::Error.WriteLine("krypton: could not update the code in $src. Check your internet connection (and KRYPTON_SRC, if the clone is elsewhere), then run krypton update again."); $global:LASTEXITCODE = 12; return }
    docker build -t $image (Join-Path $src 'client')
    if ($LASTEXITCODE -ne 0) { [Console]::Error.WriteLine("krypton: could not build the image from $src\client. Check that Docker is running and has disk space, then run krypton update again."); $global:LASTEXITCODE = 12; return }
    'Krypton is up to date.'; return
  }
  $tty = @(); if (-not [Console]::IsInputRedirected) { $tty = @('-it') }
  $port = @(); if ($args.Count -eq 0 -or $args[0] -eq 'setup') { $port = @('-p', '127.0.0.1:7777:7777') }
  docker run --rm --pull never @tty @port -v krypton-home:/krypton-home -v "${PWD}:/repo" $image @args
  if ($LASTEXITCODE -eq 125) {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { [Console]::Error.WriteLine('krypton: Docker is not running. Start Docker Desktop, then run this again.'); $global:LASTEXITCODE = 11; return }
    [Console]::Error.WriteLine("krypton: the $image image is not built yet. Run: docker build -t $image $src\client"); $global:LASTEXITCODE = 12; return
  }
  if ($LASTEXITCODE -eq 75) { krypton update }
}
```

Then, in your project:

```bash
cd your-project
krypton                                   # once per project: logs you in with GitHub, asks for your settings here or on a local dashboard, and can write the pull request gate
git add .github/workflows/krypton.yml && git commit -m "Add the Krypton gate" && git push   # turns the gate on
```

Updates: after a scan or setup at a terminal, at most once a day, Krypton says when a new version is out and asks *Update now?*; yes runs `git pull` in your clone and rebuilds the image, and `krypton update` does the same any time.

Everyday commands:

```bash
krypton scan src --names                  # the review for a folder
krypton scan . --base origin/main         # only what this branch changed (exit code 1 when it fails)
krypton order . --subdir src --model gpt-4o --post    # should this prompt's files be reordered?
krypton wire folder/filename.py           # print exactly what would be sent for that file
```

The server is a separate program that holds the scoring.

## What Krypton is

**Krypton keeps AI-written code from getting tangled as agents keep editing it.** In a 20-revision test, code held to Krypton's standard stayed within about 12 times the limit while unchecked code grew to about 1,900 times, and cleaned-up code was cheaper to change later (16.9% on average over six runs). It is a command-line program that parses your source code and has a server score how structurally heavy it is. It gives every function and file a number, flags the ones that are too tangled to read or change safely, and tells you which block to pull out. It never edits or runs your code, and uses no AI.

It exists because AI coding agents write quickly but tend to pile up deeply nested, branch-heavy functions, which are harder to change and cost tokens every time the code is read again.

**Workflow:** scan (`krypton scan src` gives a 0 to 100 purity score and a ranked list), fix (hand the flagged functions to your agent; hints name the block to split), gate (run it in CI so a touched function over the limit fails the check).

**Three ways to use it:**

- **Keep code clean.** In a 20-revision agent test a CI gate kept the worst function near 12 times the limit instead of about 1,900, for about 62% more cost per run.
- **Cut the prompt bill.** Order files from most stable to most volatile so providers can cache a long shared prefix. 35% cheaper on garth; a typical gain is about 4 points of cache reuse.
- **Measure the gap.** Compare AI-assisted code with the rest of your repository in the same size bands. A measurement of difference, not a verdict.

You choose how clean: `--level relaxed`, `standard` or `strict` sets how much tangle is tolerated (only `standard` has been tested with agents). Krypton is not a linter, not an AI, and not a formatter.

## What it does

- Scores each function and file for decisions, nesting and effective size, and flags the ones that are too heavy.
- Flags files coupled to many others, through imports or a shared global object.
- Lowers the purity score when a file is mostly trivial wrappers.
- Scores only the lines a branch changed (`--base`).
- Explains how to fix a heavy function and which block to pull out.
- Tells you, from your repository's own history, whether ordering files from most stable to most volatile would keep more of your prompt cached (`krypton order`).
- Reports files too large to analyze safely as `{error code: explanation}`, left out of the score, with the setting that would include them.

## What we measured

Measured on [garth](https://github.com/matin/garth), an open-source Garmin client, and for caching on 19 more open-source repositories.

- **Prompt caching:** on garth, ordering files by change history made the same prompts 35% cheaper on the live Claude API (71% of each prompt reused against 47% alphabetical). Across 20 repositories it beat alphabetical order on 12, tied on 1 and lost on 7, a typical gain of about 4 points of reuse. It depends on your repository.
- **Cleanup:** later changes were about 17% cheaper on average after a Krypton cleanup (six runs), about 6% for the typical run, and the cleanup costs money once. Cleaned code was not more correct.
- **CI gate:** over 20 agent revisions of one feature, a Krypton gate held the worst function near 12 times over the limit against about 1,900 times without it, with every revision correct, at about 62% higher cost.
- **Rebuild:** an agent that fixed garth's worst functions first with Krypton raised its structure score from 62 to 81, all 163 tests passing.

Full results, tables and caveats are on the [prompt caching page](https://pinklamb.github.io/Krypton/prompt-caching.html).

## Projected savings for a team

A model, not a measurement of your bill: 50 developers, 100 agent requests each per day, 22 days a month, a 25,000-line repository, list prices, counting only the cost of re-reading the code. It applies Krypton's file order (reuse rates measured on garth) and the cheaper later changes after a cleanup (16.9% on average over six runs).

| Model | Repository | Monthly, alphabetical | With file order | Saved by file order | Saved a month with cleanup too | Saved a year |
|---|---|---|---|---|---|---|
| Claude Sonnet 5.5 | garth, measured | $45.2K | $27.3K | $17.9K | **$22.5K** (50%) | $270.0K |
| Claude Opus 5.5 | garth, measured | $87.4K | $50.0K | $37.3K | **$45.8K** (52%) | $549.6K |

## Documentation

Start with the [documentation site](https://pinklamb.github.io/Krypton/): getting started, how it works, the command reference, integrations, prompt caching and an FAQ. The pages live in [`site/`](site/).

## Hosted demo

[`dashboard/`](dashboard/) is a small web app: visitors sign in with GitHub, pick a repository, and see what it costs to read and what Krypton could save. GitHub does the signing in; Krypton keeps no accounts. See [`dashboard/DEPLOY.md`](dashboard/DEPLOY.md) to deploy it with Docker or Railway.

## Repository layout

| Path | What it is |
|---|---|
| [`client/`](client/) | the client, in Rust: parses code, builds the request, posts it (`krypton`) |
| [`site/`](site/) | the documentation site (deployed to GitHub Pages by `.github/workflows/pages.yml`) |
| [`dashboard/`](dashboard/) | the hosted demo and benchmark page, with its Dockerfile and deploy notes |

The scoring server is not in this repository.

## Status

Version 2.0.0: Krypton rewritten in Rust (client and server). See [`CHANGELOG.md`](CHANGELOG.md) for what changed and what has not come back yet.
