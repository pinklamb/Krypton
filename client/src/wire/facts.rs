//! Facts about a file that need its real text, so the client works them out: the generated marker, and the waiver comments (as facts,
//! never as the comment).

#![allow(
    clippy::arithmetic_side_effects,
    reason = "client side: offsets come from tree-sitter and from the length of the very text being sliced, so they sit on character boundaries and far below usize::MAX"
)]

use std::collections::BTreeMap;

use regex::Regex;

use super::digest::expression_digest;
use super::lines::{is_py_space, split_lines};
use super::model::WireWaiver;
use super::tree::Names;

/// A generated marker is looked for in this many first lines only.
const MARKER_LINES: usize = 5;
const COMMENT_STARTS: [&str; 3] = ["#", "//", "/*"];

/// True if any marker appears in the first five lines (reported by the engine, never a reason to skip a file).
pub fn has_generated_marker(text: &str, markers: &[String]) -> bool {
    let head = text.splitn(MARKER_LINES + 1, '\n').take(MARKER_LINES).collect::<Vec<_>>().join("\n");
    markers.iter().any(|marker| head.contains(marker.as_str()))
}

/// The pattern of a waiver comment: `# krypton: ignore -- reason`, `// krypton: ignore`, `/* krypton: ignore */`.
pub fn waiver_pattern() -> Result<Regex, regex::Error> {
    Regex::new(r"(?:#|//|/\*)\s*krypton:\s*ignore\b([^\n]*)")
}

/// Python's `str.strip()`.
fn py_strip(text: &str) -> &str {
    text.trim_matches(is_py_space)
}

/// The reason of a waiver on this line ('' if none was given), or None if the line holds no waiver; normalised as the engine does.
fn reason_of(line: &str, pattern: &Regex) -> Option<String> {
    let rest = pattern.captures(line)?.get(1)?.as_str();
    let trimmed = py_strip(rest);
    let without_end = py_strip(trimmed.strip_suffix("*/").unwrap_or(trimmed));
    Some(py_strip(without_end.trim_start_matches(['-', ':'])).to_string())
}

/// For each line holding a waiver: whether it is a comment by itself, and the digest of its reason (the reason stays on the client).
pub fn waiver_facts(text: &str, pattern: &Regex, names: &mut Names) -> BTreeMap<String, WireWaiver> {
    let mut facts = BTreeMap::new();
    for (number, line) in split_lines(text).into_iter().enumerate() {
        let Some(reason) = reason_of(line, pattern) else { continue };
        let digest = (!reason.is_empty()).then(|| expression_digest(&reason));
        if let Some(digest) = &digest {
            names.insert(digest.clone(), reason);
        }
        let comment_only = COMMENT_STARTS.iter().any(|start| line.trim_start_matches(is_py_space).starts_with(start));
        facts.insert((number + 1).to_string(), WireWaiver { comment_only, reason: digest });
    }
    facts
}
