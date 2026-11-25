//! The Krypton client. It parses source code into a syntax tree on this machine and builds the request for a Krypton server; only the tree
//! (node kinds, positions, line shapes and digests of names) is sent, never source text.
//!
//! | Module | Responsibility |
//! |---|---|
//! | `error` | `WalkError` |
//! | `lang` | the supported languages, one file each |
//! | `parse` | source text to tree |
//! | `events` | `walk_events`: iterative walk with depth and exit events |
//! | `wire` | the wire serializer: a file in, the JSON request for the server out (`wire/` has its own small modules) |
//!
//! Errors are values, never fallbacks: every operation that can fail returns a [`WalkError`] or a [`WireError`] and nothing is silently
//! replaced by an empty string, a default or `None`.

mod error;
mod events;
mod lang;
mod parse;
mod wire;

pub use error::WalkError;
pub use events::{walk_events, walk_fields, Event, Visit, WalkStats};
pub use lang::Lang;
pub use parse::parse;
pub use wire::{
    build_repository, collapsed, dirname, expression_digest, hex, hmac_sha256, import_candidates, line_shapes, linguist_globs, longest_line, normpath,
    observe_imports, posix_join, split_lines, BadKey, Builder, CharIndex, ClientConfig, ImportConfig, NameKey, Names, PythonImport, RawImports, Shapes,
    Vendored, WireError, WireFile, WireRequest, PUBLIC_WORDS,
};
