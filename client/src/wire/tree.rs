//! Write a tree-sitter tree as the wire format's parallel arrays: every node, in depth-first pre-order, and none of the code.

#![allow(
    clippy::arithmetic_side_effects,
    clippy::indexing_slicing,
    reason = "client side: offsets come from tree-sitter and from the length of the very text being sliced, so they sit on character boundaries and far below usize::MAX"
)]

use std::collections::{BTreeMap, HashMap};

use tree_sitter::Node;

use super::imports::{observe, RawImports};
use super::key::NameKey;
use super::model::{WireTree, ERROR, EXTRA, MISSING, NAMED};
use super::policy::{digest_targets, is_masked, is_name, mask_of};
use crate::error::WalkError;
use crate::events::{walk_fields, Visit};
use crate::lang::Lang;

/// digest -> the real text, kept on the client so it can put names back into the server's answer.
pub type Names = BTreeMap<String, String>;

/// Character offsets for a text: tree-sitter reports bytes, the engine counts characters.
pub struct CharIndex {
    byte_to_char: Vec<u32>,
    line_starts: Vec<u32>,
}

impl CharIndex {
    /// The character index of every byte offset, and of the start of every line (lines end at `\n`, as tree-sitter's rows do).
    #[must_use]
    pub fn new(text: &str) -> Self {
        let mut byte_to_char = vec![0; text.len() + 1];
        let mut line_starts = vec![0];
        let mut total = 0;
        for (byte, c) in text.char_indices() {
            byte_to_char[byte..byte + c.len_utf8()].fill(total);
            total += 1;
            if c == '\n' {
                line_starts.push(total);
            }
        }
        byte_to_char[text.len()] = total;
        Self { byte_to_char, line_starts }
    }

    /// The character index of a byte offset.
    #[must_use]
    pub fn index(&self, byte: usize) -> u32 {
        self.byte_to_char[byte]
    }

    /// The character column of a byte offset on a 0-based row.
    #[must_use]
    pub fn column(&self, row: usize, byte: usize) -> u32 {
        self.index(byte) - self.line_starts[row]
    }
}

/// Distinct strings numbered in order of first use.
#[derive(Default)]
struct Interner {
    numbers: HashMap<String, u32>,
    names: Vec<String>,
}

impl Interner {
    fn number(&mut self, name: &str) -> u32 {
        if let Some(&found) = self.numbers.get(name) {
            return found;
        }
        #[allow(clippy::cast_possible_truncation, reason = "a table of node kinds or fields is far below 2^32 entries")]
        let next = self.names.len() as u32;
        self.numbers.insert(name.to_string(), next);
        self.names.push(name.to_string());
        next
    }
}

pub struct TreeWriter<'s> {
    source: &'s str,
    chars: CharIndex,
    tree: WireTree,
    names: Names,
    kinds: Interner,
    fields: Interner,
    path: Vec<usize>,
    wanted: HashMap<usize, String>,
    key: &'s NameKey,
}

fn flags_of(node: Node) -> u8 {
    [(node.is_named(), NAMED), (node.is_extra(), EXTRA), (node.is_error(), ERROR), (node.is_missing(), MISSING)]
        .iter()
        .filter(|(set, _)| *set)
        .fold(0, |bits, (_, bit)| bits | bit)
}

impl<'s> TreeWriter<'s> {
    pub fn new(source: &'s str, key: &'s NameKey) -> Self {
        Self {
            source,
            chars: CharIndex::new(source),
            tree: WireTree::default(),
            names: Names::new(),
            kinds: Interner::default(),
            fields: Interner::default(),
            path: Vec::new(),
            wanted: HashMap::new(),
            key,
        }
    }

    /// The node's source text, read only to make a digest or a mask; a range that is not valid UTF-8 is an error.
    fn slice(&self, node: Node) -> Result<&'s str, WalkError> {
        node.utf8_text(self.source.as_bytes()).map_err(WalkError::InvalidUtf8)
    }

    /// Record one node (the walk calls this in pre-order).
    pub fn add(&mut self, visit: &Visit) -> Result<(), WalkError> {
        let index = self.tree.n;
        self.shape(visit);
        self.stand_ins(index, visit)?;
        self.tree.n += 1;
        Ok(())
    }

    fn shape(&mut self, visit: &Visit) {
        let node = visit.node;
        self.path.truncate(visit.depth as usize);
        #[allow(clippy::cast_possible_wrap, reason = "a node index is below the node limit")]
        let parent = self.path.last().map_or(-1, |&found| found as i64);
        self.path.push(self.tree.n);
        #[allow(clippy::cast_possible_wrap, reason = "the field table is small")]
        let field = visit.field.map_or(-1, |name| self.fields.number(name) as i32);
        let tree = &mut self.tree;
        tree.kind.push(self.kinds.number(node.kind()));
        tree.field.push(field);
        tree.parent.push(parent);
        tree.flags.push(flags_of(node));
        let (start, end) = (node.start_position(), node.end_position());
        #[allow(clippy::cast_possible_truncation, reason = "a file has far fewer than 2^32 lines")]
        tree.start_line.push(start.row as u32);
        tree.start_col.push(self.chars.column(start.row, node.start_byte()));
        tree.start_index.push(self.chars.index(node.start_byte()));
        #[allow(clippy::cast_possible_truncation, reason = "a file has far fewer than 2^32 lines")]
        tree.end_line.push(end.row as u32);
        tree.end_col.push(self.chars.column(end.row, node.end_byte()));
        tree.end_index.push(self.chars.index(node.end_byte()));
    }

    /// What goes in place of text: a mask, a name's digest, or the digest of an expression the engine only compares.
    fn stand_ins(&mut self, index: usize, visit: &Visit) -> Result<(), WalkError> {
        let node = visit.node;
        let kind = node.kind();
        if is_masked(kind) {
            let mask = mask_of(kind, self.slice(node)?);
            self.tree.mask.insert(index.to_string(), mask);
        }
        let parent_kind = node.parent().map_or("", |parent| parent.kind());
        if is_name(kind, visit.field, parent_kind) {
            let text = self.slice(node)?;
            let digest = self.key.digest(text);
            self.tree.digest.insert(index.to_string(), digest.clone());
            self.names.insert(digest, text.to_string());
        }
        for target in digest_targets(node) {
            let digest = self.key.digest(self.slice(target)?);
            self.wanted.insert(target.id(), digest);
        }
        if let Some(digest) = self.wanted.get(&node.id()) {
            self.tree.digest.insert(index.to_string(), digest.clone());
        }
        Ok(())
    }

    /// The finished arrays and the names to keep on the client.
    pub fn finish(mut self) -> (WireTree, Names) {
        self.tree.kinds = self.kinds.names;
        self.tree.fields = self.fields.names;
        (self.tree, self.names)
    }
}

/// Walk `root` depth-first and write every node; also reads the imports on the way (the client resolves them).
pub type Written = (WireTree, Names, RawImports);

pub fn write_tree(source: &str, root: Node, lang: Lang, key: &NameKey) -> Result<Written, WalkError> {
    let mut writer = TreeWriter::new(source, key);
    let mut raw = RawImports::default();
    let mut failure = None;
    walk_fields(root, |visit| {
        if failure.is_none() {
            failure = writer.add(&visit).and_then(|()| observe(&mut raw, visit.node, source, lang)).err();
        }
    });
    if let Some(error) = failure {
        return Err(error);
    }
    let (tree, names) = writer.finish();
    Ok((tree, names, raw))
}
