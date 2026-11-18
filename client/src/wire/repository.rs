//! A whole repository in, the request out: what `krypton wire --stdin-json` and the browser build both call, so the two can never differ.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Value};

use super::attributes::linguist_globs;
use super::key::NameKey;
use super::model::WireRequest;
use super::tree::Names;
use super::{Builder, ClientConfig, WireError};

/// The repository's own `.gitattributes`, if it sent one, is not a source file: it only adds vendored and generated patterns.
const ATTRIBUTES_FILE: &str = ".gitattributes";

/// The config with the repository's own vendored and generated patterns added (only when it declares some). They go as `*_extra`: the server
/// adds them to its own defaults, so the client never needs to know the defaults.
#[must_use]
fn with_attributes(mut config: Value, attributes: &str) -> Value {
    let (vendored, generated) = linguist_globs(attributes);
    let Some(map) = config.as_object_mut() else { return config };
    for (key, extra) in [("vendored_path_globs_extra", vendored), ("generated_path_globs_extra", generated)] {
        if !extra.is_empty() && !map.contains_key(key) {
            map.insert(key.to_string(), json!(extra));
        }
    }
    config
}

/// Build the request for every file of `repository` (`{path: text}`, one optional `.gitattributes`), and the names that stay on the client.
///
/// # Errors
///
/// [`WireError`] when the config is invalid or a file cannot be parsed or walked.
pub fn build_repository(
    mut repository: BTreeMap<String, String>,
    config: Value,
    key: NameKey,
    level: &str,
    detail: &str,
) -> Result<(WireRequest, Names), WireError> {
    let attributes = repository.remove(ATTRIBUTES_FILE).unwrap_or_default();
    let config = with_attributes(if config.is_null() { json!({}) } else { config }, &attributes);
    let builder = Builder::new(ClientConfig::from_overrides(config)?, key)?;
    let known: BTreeSet<String> = repository.keys().cloned().collect();
    let mut names = Names::new();
    let files = repository.iter().map(|(path, text)| builder.file(path, text.as_bytes(), &known, &mut names)).collect::<Result<Vec<_>, _>>()?;
    Ok((builder.request(files, level, detail), names))
}
