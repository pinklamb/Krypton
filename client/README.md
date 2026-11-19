# krypton: the Krypton client

Parses source code into a syntax tree on your machine and sends the tree to a Krypton server. Names become digests, strings and comments are not sent, and the real names stay here (`--names` puts them back into the review, locally).
Everything the client sends is built in `src/wire/`, so the code that decides what leaves your machine is small enough to read.

```
krypton scan REPO [--level relaxed|standard|strict] [--base REF] [--names] [--json] [--save FILE]
krypton order REPO [--subdir DIR] [--ext .py,.ts] [--files LIST|-] [--model NAME] [--post] [--json]
krypton wire FILE... | --stdin-json            print the request body instead of sending it
```

* `scan` builds the tree of every Python, JavaScript and TypeScript file under REPO (skipping `node_modules`, `dist`, `.git` and the like) and prints the server's review.
* `order` reads the repository's git history here and sends only file names, token counts and which files each recent commit touched. Without `--post` it prints the body.
  File sizes are counted in the tokens of the model you name with `--model` (`gpt-4o` and the other OpenAI models offline with the real vocabularies; `claude-...` with Anthropic's counter and your own `ANTHROPIC_API_KEY`, which sends file text to Anthropic, never to Krypton), or `--tokens-from COUNTS.json`; with no model it estimates and says so.
* `wire --stdin-json` reads `{"path": "source text"}` from standard input, so a program that holds a repository in memory never writes it to disk.
  A `.gitattributes` entry adds its `linguist-vendored` / `linguist-generated` patterns to the file selection.

## Layout

| Path | What it is |
|---|---|
| `src/lang/`, `src/parse.rs`, `src/events.rs` | tree-sitter parsing for each language, and an iterative walk |
| `src/wire/` | the serializer: kinds, offsets, line shapes, digests, imports; the text rules that must agree with the server |
| `src/http.rs` | the one network call: a gzipped POST with the key |
| `src/scan_command.rs`, `src/order_command.rs`, `src/wire_command.rs`, `src/diff.rs`, `src/tokens.rs` | the three commands, the `--base` diff reader and the token counters |
| `tests/` | the serializer against known values (digests, line shapes, imports, edge cases) |
| `Dockerfile` | builds the client as a local image (git included, for `--base`, `order` and `--log`) |
| `quality.sh` | formatting, strict clippy, documentation and tests, all in Docker |

## Building

Builds run in Docker like everything else here (no Rust on the host):

```
docker build -t krypton .                       # the client as a local image (Dockerfile in this folder)
docker run --rm -v "$PWD:/repo" krypton scan .      # run it on the repository in the current folder
sh quality.sh                                   # fmt, clippy -D warnings, docs, tests
```

There is no fallback anywhere: a file that cannot be read, parsed or sent is an error with a reason and a non-zero exit code.
