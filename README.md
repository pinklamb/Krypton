# [Krypton](https://pinklamb.github.io/Krypton/)

**The compiler for AI agents.** Site and docs: https://pinklamb.github.io/Krypton/

Krypton measures how structurally heavy code is (decisions, nesting, bloated functions, tangled wiring) for JavaScript, TypeScript and Python. It runs locally, needs no network, and is built to sit beside a coding agent: after each change the agent runs Krypton, sees what got heavy, and splits it before the weight costs tokens and invites mistakes. It also orders your files so a model provider can reuse the prompt cache. It is not a linter; run yours too.

```bash
cd krypton
pip install .            # Python 3.12+; gives you the `krypton` command
krypton src --top 10
krypton . --base origin/main --json
```

Or with Docker:

```bash
cd krypton
docker build -t krypton .
docker run --rm -v "$PWD:/repo" krypton .
```

## What Krypton is

Krypton is a command-line program that reads your source code and scores how structurally heavy it is. It gives every function and file a number, flags the ones that are too tangled to read or change safely, and tells you which block to pull out. It never edits or runs your code, uses no AI, and needs no network.

It exists because AI coding agents write quickly but tend to pile up deeply nested, branch-heavy functions, which are harder to change and cost tokens every time the code is read again.

**Workflow:** scan (`krypton src` gives a 0 to 100 purity score and a ranked list), fix (hand the flagged functions to your agent; hints name the block to split), gate (run it in CI so a touched function over the limit fails the check).

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
- Explains how to fix a heavy function and tracks whether each run improved it (`--history`).
- Orders files from most stable to most volatile for prompt-cache friendly prompts (`--order-by-volatility`).
- Reports files too large to analyze safely as `{error code: explanation}`, left out of the score, with the setting that would include them.

## What we measured

Measured on [garth](https://github.com/matin/garth), an open-source Garmin client, and for caching on 19 more open-source repositories. These are small numbers of runs, so treat them as estimates.

- **Prompt caching:** on garth, ordering files by change history made the same prompts 35% cheaper on the live Claude API (71% of each prompt reused against 47% alphabetical). Across 20 repositories it beat alphabetical order on 12, tied on 1 and lost on 7, a typical gain of about 4 points of reuse. It depends on your repository.
- **Cleanup:** later changes were about 17% cheaper on average after a Krypton cleanup (six runs), about 6% for the typical run, and the cleanup costs money once. Cleaned code was not more correct.
- **CI gate:** over 20 agent revisions of one feature, a Krypton gate held the worst function near 12 times over the limit against about 1,900 times without it, with every revision correct, at about 62% higher cost.
- **Rebuild:** an agent that fixed garth's worst functions first with Krypton raised its structure score from 62 to 81, all 163 tests passing.

Full results, tables and caveats are on the [prompt caching page](https://pinklamb.github.io/Krypton/prompt-caching.html).

## Projected savings for a team

A model, not a measurement of your bill: 50 developers, 100 agent requests each per day, 22 days a month, a 25,000-line repository, list prices, counting only the cost of re-reading the code. Plan on the typical row; the garth-like row is a best case.

| Model | Repository | Monthly, alphabetical | Monthly, Krypton order | Saved a month | Saved a year |
|---|---|---|---|---|---|
| Claude Sonnet 5.5 | Typical repository | $61.5K | $58.3K | $3.2K (5%) | $38.4K |
| Claude Sonnet 5.5 | garth-like best case | $45.2K | $27.3K | $17.9K (40%) | $214.7K |
| Claude Opus 5.5 | Typical repository | $121.5K | $114.9K | $6.7K (5%) | $80.2K |
| Claude Opus 5.5 | garth-like best case | $87.4K | $50.0K | $37.3K (43%) | $448.1K |

## Documentation

Start with the [documentation site](https://pinklamb.github.io/Krypton/): getting started, how it works, the command reference, integrations, prompt caching and an FAQ. The pages live in [`krypton/site/`](krypton/site/).

## Hosted demo

[`dashboard/`](dashboard/) is a small web app: visitors sign in with GitHub, pick a repository, and see what it costs to read and what Krypton could save. The repository is read in memory, scored and discarded; nothing from it is run or stored. See [`dashboard/DEPLOY.md`](dashboard/DEPLOY.md) to deploy it with Docker or Railway.

## Repository layout

| Path | What it is |
|---|---|
| [`krypton/`](krypton/) | the tool: package, tests, Dockerfile and [usage README](krypton/README.md) |
| [`krypton/site/`](krypton/site/) | the documentation site (deployed to GitHub Pages by `.github/workflows/pages.yml`) |
| [`dashboard/`](dashboard/) | the hosted demo and benchmark page, with its Dockerfile and deploy notes |

## Status

Version 0.5.0, renamed to Krypton. See [`krypton/CHANGELOG.md`](krypton/CHANGELOG.md).
