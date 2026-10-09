# [Argon](https://pinklamb.github.io/Argon/)

**Site and docs: https://pinklamb.github.io/Argon/**

Argon measures how structurally heavy code is (branches, nesting, bloated functions, tangled wiring) for JavaScript, TypeScript and Python. It runs locally, needs no network, and is built to sit beside a coding agent: after each change the agent runs Argon, sees what got heavy, and splits it before the weight costs tokens and invites mistakes. It is not a linter; run yours too.

```bash
cd argon
pip install .            # Python 3.12+
argon src --top 10
argon . --base origin/main --json
```

Or with Docker:

```bash
cd argon
docker build -t argon .
docker run --rm -v "$PWD:/repo" argon .
```

## What it does

- Scores each function and file for branches, nesting and effective size, and flags the ones that are too heavy.
- Flags files coupled to many others, through imports or a shared global object.
- Lowers the purity score when a file is mostly trivial wrappers.
- Scores only the lines a branch changed (`--base`).
- Orders files from most stable to most volatile for prompt-cache friendly prompts (`--order-by-volatility`).
- Reports files too large to analyze safely as `{error code: explanation}`, left out of the score, with the setting that would include them.

## Repository layout

| Path | What it is |
|---|---|
| [`argon/`](argon/) | the tool: package, tests, Dockerfile, [usage README](argon/README.md), [scoring notes](argon/SCORING.md) |
| [`argon/site/`](argon/site/) | the GitHub Pages site (deployed by `.github/workflows/pages.yml`) |
| [`REPORT.md`](REPORT.md) | the audit that compared a monolithic and a modular build of the same game |

## Status

Version 0.5.0. See [`argon/CHANGELOG.md`](argon/CHANGELOG.md).
