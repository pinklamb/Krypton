//! The client half of the scoring server: turn a source file into the wire format (`POST /v1/score`), with every node of its tree.
//! It was first written as a reference client in Python, and a private parity check holds this one to the same result.

mod attributes;
mod digest;
mod facts;
mod imports;
mod lines;
mod model;
mod policy;
mod posix;
mod tree;
mod vendored;

use std::collections::BTreeSet;
use std::fmt;

use regex::Regex;
use serde_json::Value;

use crate::error::WalkError;
use crate::lang::Lang;
use crate::parse::parse;

pub use attributes::linguist_globs;
pub use digest::{expression_digest, hex};
pub use imports::{candidates as import_candidates, ImportConfig, PythonImport, RawImports};
pub use lines::{collapsed, line_shapes, longest_line, split_lines, Shapes};
pub use model::{ClientInfo, WireFile, WireFlags, WireOptions, WireRequest, WIRE_VERSION};
pub use posix::{dirname, join as posix_join, normpath};
pub use tree::{write_tree, CharIndex, Names};
pub use vendored::Vendored;

const DEFAULT_MARKERS: [&str; 3] = ["@generated", "DO NOT EDIT", "auto-generated"];

/// Everything that can go wrong building a request; nothing is replaced by a default.
#[derive(Debug)]
pub enum WireError {
    Walk(WalkError),
    Regex(regex::Error),
    /// The config overrides are not what the server accepts, or are not JSON.
    Config(String),
}

impl fmt::Display for WireError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Walk(error) => write!(f, "{error}"),
            Self::Regex(error) => write!(f, "a built-in pattern did not compile: {error}"),
            Self::Config(message) => write!(f, "invalid config: {message}"),
        }
    }
}

impl std::error::Error for WireError {}

impl From<WalkError> for WireError {
    fn from(error: WalkError) -> Self {
        Self::Walk(error)
    }
}

impl From<regex::Error> for WireError {
    fn from(error: regex::Error) -> Self {
        Self::Regex(error)
    }
}

/// The user's config overrides, and the two that the client itself needs.
pub struct ClientConfig {
    pub generated_markers: Vec<String>,
    pub detect_vendored: bool,
    pub imports: ImportConfig,
    /// Sent to the server unchanged as `options.config`.
    pub overrides: Value,
}

fn string_list(value: &Value, name: &str) -> Result<Vec<String>, WireError> {
    let bad = || WireError::Config(format!("{name} must be a list of strings"));
    value.as_array().ok_or_else(bad)?.iter().map(|item| item.as_str().map(str::to_string).ok_or_else(bad)).collect()
}

impl ClientConfig {
    /// Read the overrides object (null means none), checking the keys this client uses.
    ///
    /// # Errors
    ///
    /// [`WireError::Config`] when the overrides are not a JSON object, `generated_markers` is not a list of strings, `detect_vendored_content` is not true or false, or the import settings are invalid (see [`ImportConfig::from_overrides`]).
    pub fn from_overrides(overrides: Value) -> Result<Self, WireError> {
        let overrides = if overrides.is_null() { Value::Object(serde_json::Map::default()) } else { overrides };
        if !overrides.is_object() {
            return Err(WireError::Config("the overrides must be a JSON object".to_string()));
        }
        let generated_markers = match overrides.get("generated_markers") {
            Some(list) => string_list(list, "generated_markers")?,
            None => DEFAULT_MARKERS.iter().map(ToString::to_string).collect(),
        };
        let detect_vendored = match overrides.get("detect_vendored_content") {
            Some(flag) => flag.as_bool().ok_or_else(|| WireError::Config("detect_vendored_content must be true or false".to_string()))?,
            None => true,
        };
        let imports = ImportConfig::from_overrides(&overrides).map_err(WireError::Config)?;
        Ok(Self { generated_markers, detect_vendored, imports, overrides })
    }
}

/// Builds wire files; the patterns are compiled once.
pub struct Builder {
    config: ClientConfig,
    vendored: Vendored,
    waiver: Regex,
}

/// The file's text as the engine reads it: invalid bytes replaced, one leading BOM removed.
fn decode(bytes: &[u8]) -> String {
    let text = String::from_utf8_lossy(bytes);
    text.strip_prefix('\u{feff}').unwrap_or(&text).to_string()
}

impl Builder {
    /// Compile the built-in patterns once.
    ///
    /// # Errors
    ///
    /// [`WireError::Regex`] when a built-in pattern does not compile. That is a bug in the client, not a problem with any input.
    pub fn new(config: ClientConfig) -> Result<Self, WireError> {
        Ok(Self { config, vendored: Vendored::new()?, waiver: facts::waiver_pattern()? })
    }

    /// One file as a wire file. `known` is every path in the request (imports are resolved against it); `names` collects digest -> real
    /// name, which stays on the client. `path` is made relative-looking with forward slashes by the caller.
    ///
    /// # Errors
    ///
    /// [`WireError::Walk`] when the file's extension is not supported ([`WalkError::UnsupportedFile`]), the parser fails ([`WalkError::Language`], [`WalkError::NoTree`]), or a node's text is not valid UTF-8 ([`WalkError::InvalidUtf8`]).
    pub fn file(&self, path: &str, bytes: &[u8], known: &BTreeSet<String>, names: &mut Names) -> Result<WireFile, WireError> {
        let path = path.replace('\\', "/");
        let lang = Lang::from_path(&path)?;
        let text = decode(bytes);
        let parsed = parse(lang, &text)?;
        let shapes = line_shapes(&text);
        let (tree, tree_names, raw) = write_tree(&text, parsed.root_node(), lang)?;
        names.extend(tree_names);
        let vendored = if self.config.detect_vendored { self.vendored.reason(&path, &text) } else { None };
        Ok(WireFile {
            language: lang.name(),
            bytes: bytes.len(),
            longest_line: longest_line(&text),
            lines: shapes.lines,
            cr: shapes.cr,
            final_newline: shapes.final_newline,
            flags: WireFlags { generated_marker: facts::has_generated_marker(&text, &self.config.generated_markers), vendored },
            altered_lines: None,
            change_frequency: None,
            waivers: facts::waiver_facts(&text, &self.waiver, names),
            imports: imports::candidates(&path, &raw, &self.config.imports, known),
            tree,
            path,
        })
    }

    /// The whole request body for these files.
    #[must_use]
    pub fn request(&self, files: Vec<WireFile>, level: &str, detail: &str) -> WireRequest {
        WireRequest {
            wire_version: WIRE_VERSION,
            client: ClientInfo { name: "krypton", version: env!("CARGO_PKG_VERSION") },
            options: WireOptions { level: level.to_string(), detail: detail.to_string(), config: self.config.overrides.clone() },
            files,
        }
    }
}
