//! Our own iterative cursor walk, with the depth and exit events the Python scope handling needs.
//! It never recurses, so a very deep tree cannot overflow the stack, and moving to the next sibling is
//! constant time however wide the node is.

#![allow(clippy::arithmetic_side_effects, reason = "node and edit counts of a file held in memory are far below usize::MAX, and release builds check overflow")]

use tree_sitter::{Node, TreeCursor};

/// One step of [`walk_events`].
#[derive(Debug, Clone, Copy)]
pub enum Event<'t> {
    /// The walk reached a node; the number is its depth below the root (the root is 0).
    Enter(Node<'t>, u32),
    /// The walk finished a node and everything under it.
    Exit(Node<'t>, u32),
}

/// What a walk covered.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct WalkStats {
    pub nodes: usize,
    pub max_depth: u32,
}

/// Iterative depth-first walk of the subtree under `root`: `Enter` in pre-order, `Exit` after the last descendant.
pub fn walk_events<'t>(root: Node<'t>, mut visit: impl FnMut(Event<'t>)) -> WalkStats {
    let mut cursor = root.walk();
    let mut stats = WalkStats::default();
    let mut depth: u32 = 0;
    loop {
        stats.nodes += 1;
        stats.max_depth = stats.max_depth.max(depth);
        visit(Event::Enter(cursor.node(), depth));
        if cursor.goto_first_child() {
            depth += 1;
            continue;
        }
        if !climb(&mut cursor, &mut depth, &mut visit) {
            return stats;
        }
    }
}

/// Finish the current leaf and every ancestor whose last child it was, stopping at the next sibling.
/// Returns false when the walk has come back to the root and is finished.
fn climb<'t>(cursor: &mut TreeCursor<'t>, depth: &mut u32, visit: &mut impl FnMut(Event<'t>)) -> bool {
    loop {
        visit(Event::Exit(cursor.node(), *depth));
        if cursor.goto_next_sibling() {
            return true;
        }
        if !cursor.goto_parent() {
            return false;
        }
        *depth -= 1;
    }
}

/// One node seen by [`walk_fields`]: the node, its depth, and the field name its parent gives it.
#[derive(Debug, Clone, Copy)]
pub struct Visit<'t> {
    pub node: Node<'t>,
    pub depth: u32,
    pub field: Option<&'t str>,
}

/// Iterative depth-first walk, `Visit` in pre-order only.
///
/// Unlike [`walk_events`] it reports each node's field name,
/// which the wire format needs, and has no exit events (the depth is enough to find a node's parent).
pub fn walk_fields<'t>(root: Node<'t>, mut visit: impl FnMut(Visit<'t>)) {
    let mut cursor = root.walk();
    let mut depth: u32 = 0;
    loop {
        visit(Visit { node: cursor.node(), depth, field: cursor.field_name() });
        if cursor.goto_first_child() {
            depth += 1;
            continue;
        }
        if !next_node(&mut cursor, &mut depth) {
            return;
        }
    }
}

/// Move to the next node in pre-order after a leaf; false when the walk is back at the root and finished.
fn next_node(cursor: &mut TreeCursor<'_>, depth: &mut u32) -> bool {
    loop {
        if cursor.goto_next_sibling() {
            return true;
        }
        if !cursor.goto_parent() {
            return false;
        }
        *depth -= 1;
    }
}
