#!/bin/sh
# Code quality for client, in Docker like every build here. Run from anywhere:  sh client/quality.sh
#
#   0. formatting: rustfmt (rustfmt.toml), checked, never silently rewritten
#   1. production code (library and binary): clippy's defaults, the `pedantic`, `nursery` and `cargo` groups and the lints in Cargo.toml; every warning is an error
#   2. tests: the same lints, except the six a test legitimately breaks (unwrap, expect, panic, type_complexity, nesting, and function length for long data tables)
#   3. the documentation builds with no broken links (RUSTDOCFLAGS=-D warnings)
#   4. the tests themselves
#
# What the Cargo.toml lints enforce:  small functions (complexity, length, arguments, nesting), named types instead of nested tuples,
# and no hidden failure (no unwrap, expect, panic, todo or dbg! in shipped code: an error is a Result the caller sees). Indexing, arithmetic and
# string slicing are linted too: code that is bounded by what it just parsed says why in a module-level `allow(..., reason = ...)`.
# Thresholds live in clippy.toml.
set -e
here="$(cd "$(dirname "$0")" && (pwd -W 2>/dev/null || pwd))"
export MSYS_NO_PATHCONV=1
docker run --rm -v "$here:/work" -v krypton_cargo:/usr/local/cargo/registry -v krypton_target:/target \
  -e CARGO_TARGET_DIR=/target -w /work rust:1 sh -c '
    set -e
    rustup component add clippy rustfmt >/dev/null 2>&1
    echo "== formatting";                cargo fmt --all --check
    echo "== production code (strict)";  cargo clippy --workspace --lib --bins -- -D warnings
    echo "== the library alone (no network or tokenizer)"; cargo clippy --lib --no-default-features -- -D warnings
    echo "== tests (same lints, tests may unwrap and panic)"; cargo clippy --workspace --tests -- -D warnings
    echo "== documentation";           RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps --lib --quiet
    echo "== tests pass";                cargo test --workspace --quiet
    echo "quality: all clean"'
