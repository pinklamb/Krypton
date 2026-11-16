//! The three `posixpath` functions the import rules use, written to give the same answers as Python's.

#![allow(
    clippy::arithmetic_side_effects,
    clippy::string_slice,
    reason = "client side: offsets come from tree-sitter and from the length of the very text being sliced, so they sit on character boundaries and far below usize::MAX"
)]

/// `posixpath.join(a, b)`: an absolute `b` replaces `a`; otherwise they are joined with one slash.
#[must_use]
pub fn join(a: &str, b: &str) -> String {
    if b.starts_with('/') {
        b.to_string()
    } else if a.is_empty() || a.ends_with('/') {
        format!("{a}{b}")
    } else {
        format!("{a}/{b}")
    }
}

/// `posixpath.normpath`: collapse `.`, `..` and repeated slashes; the empty path is `.`.
#[must_use]
pub fn normpath(path: &str) -> String {
    if path.is_empty() {
        return ".".to_string();
    }
    let slashes = if path.starts_with("//") && !path.starts_with("///") { 2 } else { usize::from(path.starts_with('/')) };
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/').filter(|part| !part.is_empty() && *part != ".") {
        let keeps = part != ".." || (slashes == 0 && parts.is_empty()) || parts.last() == Some(&"..");
        if keeps {
            parts.push(part);
        } else {
            parts.pop();
        }
    }
    let joined = format!("{}{}", "/".repeat(slashes), parts.join("/"));
    if joined.is_empty() {
        ".".to_string()
    } else {
        joined
    }
}

/// `posixpath.dirname`: everything before the last slash, without trailing slashes (unless the path is all slashes).
#[must_use]
pub fn dirname(path: &str) -> String {
    let head = &path[..path.rfind('/').map_or(0, |index| index + 1)];
    if head.is_empty() || head.chars().all(|c| c == '/') {
        head.to_string()
    } else {
        head.trim_end_matches('/').to_string()
    }
}
