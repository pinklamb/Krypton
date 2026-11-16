//! JavaScript: the file extensions and the grammar. Language-specific rules for the migration go in this folder too.

pub const EXTENSIONS: &[&str] = &["js", "mjs", "cjs", "jsx"];

pub fn grammar() -> tree_sitter::Language {
    tree_sitter_javascript::LANGUAGE.into()
}
