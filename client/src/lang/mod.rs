//! The supported languages. To add one:
//!
//! 1. add `lang/<name>.rs` with its `EXTENSIONS` and `grammar()`, copying `python.rs`;
//! 2. add a `mod <name>;` line, a [`Lang`] variant and an entry in [`Lang::ALL`] here, plus one arm in each of the `match`es.
//!
//! The compiler points at every `match` that needs the new variant. Nothing outside this folder changes.

mod javascript;
mod python;
mod tsx;
mod typescript;

use crate::error::WalkError;

/// The languages the Rust client reads: the same four the Python engine scores.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lang {
    Python,
    JavaScript,
    TypeScript,
    Tsx,
}

impl Lang {
    /// Every supported language.
    pub const ALL: [Self; 4] = [Self::Python, Self::JavaScript, Self::TypeScript, Self::Tsx];

    /// The language for a file name, by extension (any capitalisation). An unknown extension is an error, not a default.
    ///
    /// # Errors
    ///
    /// [`WalkError::UnsupportedFile`] when the extension is not one of the supported languages.
    pub fn from_path(path: &str) -> Result<Self, WalkError> {
        let extension = path.rsplit('.').next().unwrap_or_default().to_ascii_lowercase();
        Self::ALL.into_iter().find(|lang| lang.extensions().contains(&extension.as_str())).ok_or_else(|| WalkError::UnsupportedFile(path.to_string()))
    }

    /// The language's name as the wire format spells it.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Python => "python",
            Self::JavaScript => "javascript",
            Self::TypeScript => "typescript",
            Self::Tsx => "tsx",
        }
    }

    /// The file extensions this language owns, without the dot.
    #[must_use]
    pub const fn extensions(self) -> &'static [&'static str] {
        match self {
            Self::Python => python::EXTENSIONS,
            Self::JavaScript => javascript::EXTENSIONS,
            Self::TypeScript => typescript::EXTENSIONS,
            Self::Tsx => tsx::EXTENSIONS,
        }
    }

    /// Every supported extension, for error messages: ".py, .js, .mjs, .cjs, .jsx, .ts, .mts, .cts, .tsx".
    pub fn supported() -> String {
        let all = Self::ALL.into_iter().flat_map(Self::extensions);
        all.map(|extension| format!(".{extension}")).collect::<Vec<_>>().join(", ")
    }

    pub(crate) fn grammar(self) -> tree_sitter::Language {
        match self {
            Self::Python => python::grammar(),
            Self::JavaScript => javascript::grammar(),
            Self::TypeScript => typescript::grammar(),
            Self::Tsx => tsx::grammar(),
        }
    }
}
