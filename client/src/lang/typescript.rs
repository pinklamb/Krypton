//! TypeScript: the file extensions and the grammar (`.tsx` has its own grammar, in `tsx.rs`).

pub const EXTENSIONS: &[&str] = &["ts", "mts", "cts"];

pub fn grammar() -> tree_sitter::Language {
    tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()
}
