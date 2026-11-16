//! TSX: TypeScript with JSX, which needs its own grammar.

pub const EXTENSIONS: &[&str] = &["tsx"];

pub fn grammar() -> tree_sitter::Language {
    tree_sitter_typescript::LANGUAGE_TSX.into()
}
