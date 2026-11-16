//! The vendored and generated patterns a repository declares in its `.gitattributes` (`linguist-vendored`, `linguist-generated`), as
//! `krypton/engine/vendored.py::linguist_globs` reads them. Only the client sees the file, so only the client can turn it into config.

/// Where Python's `str.splitlines()` breaks a line (more than `\n`).
const fn is_line_break(c: char) -> bool {
    matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c'..='\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}')
}

/// The engine globs for one `.gitattributes` pattern: a bare name matches at any depth, a trailing slash is a folder.
fn globs_for(pattern: &str) -> Vec<String> {
    let body = pattern.trim_matches('/');
    if body.is_empty() {
        return Vec::new();
    }
    let prefix = if pattern.starts_with('/') || body.contains('/') { "" } else { "**/" };
    vec![format!("{prefix}{body}"), format!("{prefix}{body}/**")]
}

/// `(vendored globs, generated globs)` from the text of a `.gitattributes` file.
#[must_use]
pub fn linguist_globs(text: &str) -> (Vec<String>, Vec<String>) {
    let (mut vendored, mut generated) = (Vec::new(), Vec::new());
    for raw in text.split(is_line_break) {
        let before_comment = raw.split('#').next().unwrap_or_default();
        let mut parts = before_comment.split_whitespace();
        let Some(pattern) = parts.next() else { continue };
        for attribute in parts {
            let (name, value) = attribute.split_once('=').unwrap_or((attribute, ""));
            let target = match name {
                "linguist-vendored" => &mut vendored,
                "linguist-generated" => &mut generated,
                _ => continue,
            };
            if !value.eq_ignore_ascii_case("false") {
                target.extend(globs_for(pattern));
            }
        }
    }
    (vendored, generated)
}

#[cfg(test)]
mod tests {
    use super::*;

    // A bare name matches at any depth and a path stays where it is.
    #[test]
    fn test_pattern_globs() {
        let text = "docs/** linguist-vendored\nfoo.js linguist-generated=true\n/lib/ linguist-vendored=false\nbar linguist-vendored # note\n*.py text\n";
        let (vendored, generated) = linguist_globs(text);
        assert_eq!(vendored, ["docs/**", "docs/**/**", "**/bar", "**/bar/**"]);
        assert_eq!(generated, ["**/foo.js", "**/foo.js/**"]);
    }
}
