//! Turning source text into a tree.

use tree_sitter::{Parser, Tree};

use crate::error::WalkError;
use crate::lang::Lang;

/// A parser with the grammar for `lang` loaded.
pub fn parser_for(lang: Lang) -> Result<Parser, WalkError> {
    let mut parser = Parser::new();
    parser.set_language(&lang.grammar()).map_err(WalkError::Language)?;
    Ok(parser)
}

/// Parse source text. A file with syntax errors still gives a tree (with ERROR and missing nodes in it); an error here means
/// the grammar could not be loaded or the parser produced nothing.
///
/// # Errors
///
/// - [`WalkError::Language`] when the grammar cannot be loaded into the parser (a version mismatch).
/// - [`WalkError::NoTree`] when the parser returns no tree (cancelled, or no language set).
pub fn parse(lang: Lang, source: &str) -> Result<Tree, WalkError> {
    parser_for(lang)?.parse(source, None).ok_or(WalkError::NoTree)
}
