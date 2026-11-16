//! Line shapes and the text helpers that must agree with Python's `str` methods, because the server compares their results.

#![allow(
    clippy::arithmetic_side_effects,
    clippy::string_slice,
    reason = "client side: offsets come from tree-sitter and from the length of the very text being sliced, so they sit on character boundaries and far below usize::MAX"
)]

use serde::Serialize;

/// Python's `str.isspace`: Rust's `White_Space` plus the four information separators U+001C to U+001F.
pub const fn is_py_space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')
}

/// The text with every run of whitespace turned into one space and the ends trimmed (the engine's `collapsed`).
pub fn collapsed(text: &str) -> String {
    text.split(is_py_space).filter(|part| !part.is_empty()).collect::<Vec<_>>().join(" ")
}

/// The characters Python's `str.splitlines` breaks on.
const fn is_break(c: char) -> bool {
    matches!(c, '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}')
}

/// Python's `str.splitlines()`: lines without their terminators, `\r\n` as one break, no empty last line.
#[must_use]
pub fn split_lines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((index, c)) = chars.next() {
        if !is_break(c) {
            continue;
        }
        lines.push(&text[start..index]);
        start = index + c.len_utf8();
        if c == '\r' && chars.next_if(|&(_, next)| next == '\n').is_some() {
            start += 1;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// Characters in the longest line, counted the way the CLI counts it.
#[must_use]
pub fn longest_line(text: &str) -> usize {
    split_lines(text).iter().map(|line| line.chars().count()).max().unwrap_or(0)
}

/// One physical line: its length in characters and the `[start, end)` runs of visible characters.
pub type LineShape = (usize, Vec<(usize, usize)>);

/// What the server needs to rebuild a same-shape skeleton of the file without seeing its text.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Shapes {
    /// Per physical line: its length in characters and the `[start, end)` runs of visible characters.
    pub lines: Vec<LineShape>,
    /// The lines that ended with a carriage return.
    pub cr: Vec<usize>,
    pub final_newline: bool,
}

/// The `[start, end)` character runs of non-whitespace in one line.
fn visible_runs(line: &str) -> Vec<(usize, usize)> {
    let mut runs = Vec::new();
    let mut open: Option<usize> = None;
    let mut length = 0;
    for (position, c) in line.chars().enumerate() {
        length = position + 1;
        match (is_py_space(c), open) {
            (false, None) => open = Some(position),
            (true, Some(start)) => {
                runs.push((start, position));
                open = None;
            }
            _ => {}
        }
    }
    runs.extend(open.map(|start| (start, length)));
    runs
}

/// Split on `\n` only (the CLI's physical lines), noting carriage returns and the final newline.
#[must_use]
pub fn line_shapes(text: &str) -> Shapes {
    let mut raw: Vec<&str> = text.split('\n').collect();
    let final_newline = text.ends_with('\n');
    if final_newline {
        raw.pop();
    }
    let cr = raw.iter().enumerate().filter(|(_, line)| line.ends_with('\r')).map(|(number, _)| number).collect();
    let lines = raw
        .iter()
        .map(|line| {
            let content = line.strip_suffix('\r').unwrap_or(line);
            (content.chars().count(), visible_runs(content))
        })
        .collect();
    Shapes { lines, cr, final_newline }
}
