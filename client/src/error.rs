//! Every way an operation in this crate can fail. Errors are values; nothing is replaced by a default.

use std::fmt;
use std::str::Utf8Error;

use tree_sitter::LanguageError;

use crate::lang::Lang;

/// Everything that can go wrong in this crate.
#[derive(Debug)]
pub enum WalkError {
    /// The file extension is not one of the supported languages.
    UnsupportedFile(String),
    /// The grammar could not be loaded into the parser (a version mismatch).
    Language(LanguageError),
    /// The parser returned no tree (cancelled, or no language set).
    NoTree,
    /// A node's byte range is not valid UTF-8 text.
    InvalidUtf8(Utf8Error),
}

impl fmt::Display for WalkError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnsupportedFile(path) => write!(f, "unsupported file type: {path} (supported: {})", Lang::supported()),
            Self::Language(error) => write!(f, "the grammar could not be loaded: {error}"),
            Self::NoTree => write!(f, "the parser returned no tree"),
            Self::InvalidUtf8(error) => write!(f, "a node is not valid UTF-8: {error}"),
        }
    }
}

impl std::error::Error for WalkError {}
