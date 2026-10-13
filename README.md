# [Krypton](https://pinklamb.github.io/Argon/)

**The compiler for AI agents.** Site and docs: https://pinklamb.github.io/Argon/

Krypton measures how structurally heavy code is (decisions, nesting, bloated functions, tangled wiring) for JavaScript, TypeScript and Python. It runs locally, needs no network, and is built to sit beside a coding agent: after each change the agent runs Krypton, sees what got heavy, and splits it before the weight costs tokens and invites mistakes. It also orders your files so a model provider can reuse the prompt cache. It is not a linter; run yours too.

```bash
cd argon
pip install .            # Python 3.12+; gives you the `krypton` command
krypton src --top 10
krypton . --base origin/main --json
```

Or with Docker:

```bash
cd argon
docker build -t krypton .
docker run --rm -v "$PWD:/repo" krypton .
```

The older `argon` command still works and runs the same program.

## What it does

- Scores each function and file for decisions, nesting and effective size, and flags the ones that are too heavy.
- Flags files coupled to many others, through imports or a shared global object.
- Lowers the purity score when a file is mostly trivial wrappers.
- Scores only the lines a branch changed (`--base`).
- Explains how to fix a heavy function and tracks whether each run improved it (`--history`).
- Orders files from most stable to most volatile for prompt-cache friendly prompts (`--order-by-volatility`).
- Reports files too large to analyze safely as `{error code: explanation}`, left out of the score, with the setting that would include them.

## Documentation

Start with the [documentation site](https://pinklamb.github.io/Argon/): getting started, how it works, the command reference, integrations, prompt caching and an FAQ. The pages live in [`argon/site/`](argon/site/).

## Hosted demo

[`dashboard/`](dashboard/) is a small web app: visitors sign in with GitHub, pick a repository, and see what it costs to read and what Krypton could save. The repository is read in memory, scored and discarded; nothing from it is run or stored. See [`dashboard/DEPLOY.md`](dashboard/DEPLOY.md) to deploy it with Docker or Railway.

## Repository layout

| Path | What it is |
|---|---|
| [`argon/`](argon/) | the tool: package, tests, Dockerfile and [usage README](argon/README.md). The folder and Python package keep their earlier name, `argon`, for now |
| [`argon/site/`](argon/site/) | the documentation site (deployed to GitHub Pages by `.github/workflows/pages.yml`) |
| [`dashboard/`](dashboard/) | the hosted demo and benchmark page, with its Dockerfile and deploy notes |

## Status

Version 0.5.0, renamed to Krypton. See [`argon/CHANGELOG.md`](argon/CHANGELOG.md).
