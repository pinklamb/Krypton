//! Turning source text into a tree.

use std::cell::RefCell;

use tree_sitter::{Parser, Tree};

use crate::error::WalkError;
use crate::lang::Lang;

/// A parser with the grammar for `lang` loaded.
pub fn parser_for(lang: Lang) -> Result<Parser, WalkError> {
    let mut parser = Parser::new();
    parser.set_language(&lang.grammar()).map_err(WalkError::Language)?;
    Ok(parser)
}

/// The parsers one thread has made, one per language.
type Parsers = RefCell<Vec<(Lang, Parser)>>;

thread_local! {
    /// One parser per language per thread, made on first use. Making a `Parser` and loading its grammar for every file is one of the
    /// largest hidden costs of a tree-sitter pipeline; a scan of a large repository parses thousands of files.
    static PARSERS: Parsers = const { RefCell::new(Vec::new()) };
}

/// Parse source text. A file with syntax errors still gives a tree (with ERROR and missing nodes in it); an error here means
/// the grammar could not be loaded or the parser produced nothing.
///
/// # Errors
///
/// - [`WalkError::Language`] when the grammar cannot be loaded into the parser (a version mismatch).
/// - [`WalkError::NoTree`] when the parser returns no tree (cancelled, or no language set).
pub fn parse(lang: Lang, source: &str) -> Result<Tree, WalkError> {
    PARSERS.with(|cell| {
        // the parsers are only ever borrowed here, for one parse at a time, so the borrow cannot clash
        let mut parsers = cell.try_borrow_mut().map_err(|_busy| WalkError::NoTree)?;
        if !parsers.iter().any(|(known, _)| *known == lang) {
            parsers.push((lang, parser_for(lang)?));
        }
        let parser = parsers.iter_mut().find(|(known, _)| *known == lang).map(|(_, parser)| parser).ok_or(WalkError::NoTree)?;
        parser.reset();
        parser.parse(source, None).ok_or(WalkError::NoTree)
    })
}
