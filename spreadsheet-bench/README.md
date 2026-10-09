# Mini spreadsheet engine comparison

Two independent implementations of the same mini spreadsheet feature are kept here:

- `argon_version/` was iterated with the local Argon analyzer. Its three raw scans and history are in `argon_version/logs/`.
- `plain_version/` was built without using Argon.

Both implementations include a formula parser, cell references and ranges, dependency tracking, cycle handling, common functions, CSV input/output, and a command line interface. See each version's README for supported syntax and commands.

## Measurement notes

`measurement-summary.json` records source-size estimates and mock cache replay output. The replay used the repository's `tools.cache_replay` harness with its mock client. Since each implementation is currently a single Python file, the replay used a one-file adaptation; all three file orderings are necessarily identical. These figures are simulated, use the harness's rough 3.5 characters-per-token estimate, and are not API measurements.

The environment did not expose model request usage for the coding iterations. Therefore actual prompt, completion, cache-read, and cache-write tokens and their billed cost are recorded as unavailable, rather than inferred from source size. No provider API requests were made.

| Build | Argon history | Final Argon purity | Source token estimate | Mock replay cost (3 orderings × 8 turns) |
|---|---:|---:|---:|---:|
| Argon | Initial build: 25 → 30 → 100; refactor scans: 100 throughout | 100/100, 0 files flagged, 0 remaining hints | See JSON (before and after) | $0.1768 simulated after refactor |
| Plain | Not used | Flagged by Argon in the exploratory scan; no Argon iteration | 3,702 | $0.1735 simulated |

The Argon refactor split token scanning, cell formula parsing, and column-label formatting into helpers, and simplified dependency-map construction. Argon reported nesting 122 → 111, branches unchanged at 94, effective lines 398 → 418, and no remaining hints. The cache replay's dollar figures use its bundled price assumptions and tiny synthetic response, so they only describe the harness simulation. The two-file cache ordering comparison and real feature-iteration token cost remain unmeasured.

## Real OpenAI cache measurement

`measure_openai_cache.py` is an API-backed comparison for GPT-6 Luna at medium reasoning. It sends the Python files in `argon/argon/` (or a folder supplied with `--source`) in Argon's stable-first order and alphabetical order, with the same six-turn edit schedule. It uses OpenAI response usage fields to report cached tokens, cache writes, and cost. It saves usage metadata only; the prompt source is sent to OpenAI but is not written to the output report.

Configure `OPENAI_API_KEY` in your shell, then run:

```powershell
python spreadsheet-bench/measure_openai_cache.py
```

The script has a $0.50 measured-spend cap and checks a conservative preflight estimate before making requests. To change the cap or source folder, use `--max-usd` and `--source`. The API calls are billable. The current environment did not have `OPENAI_API_KEY` configured, so no live requests have been made yet.
