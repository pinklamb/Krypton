//! What a client sends beyond the bare tree. This mirrors the server's `wire_policy.py` (published at GET /v1/capabilities).
//!
//! The server never receives source text. Every node is always sent (kind, field, position, flags), but the code in it is not:
//! an unnamed token needs no text (its kind is its text); a name is sent as a digest and put back by the client; an expression the
//! engine only compares is sent as a digest; a match pattern or dotted name is sent as a mask that keeps one fact.

use tree_sitter::Node;

/// Names: sent as a digest, turned back into the real name by the client.
pub const NAME_KINDS: &[&str] = &[
    "identifier",
    "property_identifier",
    "private_property_identifier",
    "type_identifier",
    "shorthand_property_identifier",
    "shorthand_property_identifier_pattern",
];
/// Roles that name a scope or a binding, whatever the node is (a computed or string name, for example): the `name` of a declaration,
/// and the binding fields the engine reads. These are the engine's own tables (`scopes.py`), pinned by the parity harness.
pub const NAME_ROLES: &[(&str, &str)] = &[
    ("function_definition", "name"),
    ("function_declaration", "name"),
    ("generator_function_declaration", "name"),
    ("method_definition", "name"),
    ("class_definition", "name"),
    ("class_declaration", "name"),
    ("abstract_class_declaration", "name"),
    ("variable_declarator", "name"),
    ("pair", "key"),
    ("field_definition", "property"),
    ("public_field_definition", "name"),
    ("assignment", "left"),
];
const CASE_PATTERN: &str = "case_pattern";
const DOTTED_NAME: &str = "dotted_name";
/// A digest also replaces the code of expressions the engine only compares: the value that is returned or yielded...
pub const RETURN_KINDS: &[&str] = &["return_statement", "yield", "yield_expression"];
/// ...an expression body (not a block)...
pub const BODY_KINDS: &[&str] = &["lambda", "arrow_function"];
/// ...and the object a method is called on.
pub const RECEIVER_KINDS: &[&str] = &["member_expression", "attribute"];
/// ...the class a `new` builds, and the name a decorator applies (the engine looks both up by name, whatever expression they are).
const CONSTRUCTOR_KIND: &str = "new_expression";
const DECORATOR_KIND: &str = "decorator";

/// True for a name: an identifier-like node, or whatever fills a naming role of its parent.
pub fn is_name(kind: &str, field: Option<&str>, parent_kind: &str) -> bool {
    NAME_KINDS.contains(&kind) || field.is_some_and(|name| NAME_ROLES.contains(&(parent_kind, name)))
}

/// True for the kinds sent as a mask.
pub fn is_masked(kind: &str) -> bool {
    kind == CASE_PATTERN || kind == DOTTED_NAME
}

/// The stand-in for a masked kind: `_` or `?` for a match pattern, `n.n` for a dotted name (each run of non-dot characters becomes `n`).
pub fn mask_of(kind: &str, text: &str) -> String {
    if kind == CASE_PATTERN {
        return if text == "_" { "_".to_string() } else { "?".to_string() };
    }
    text.split('.').map(|part| if part.is_empty() { "" } else { "n" }).collect::<Vec<_>>().join(".")
}

/// The value a return/yield node carries, or the expression body of a lambda/arrow function.
fn returned_value(node: Node) -> Option<Node> {
    let kind = node.kind();
    if RETURN_KINDS.contains(&kind) && node.is_named() {
        return node.named_child(0);
    }
    if !BODY_KINDS.contains(&kind) {
        return None;
    }
    node.child_by_field_name("body").filter(|body| body.kind() != "statement_block")
}

/// The returned value with its outer parentheses removed (the engine's own unwrapping).
fn returned_expression(node: Node) -> Option<Node> {
    let mut target = returned_value(node)?;
    while target.kind() == "parenthesized_expression" && target.named_child_count() == 1 {
        target = target.named_child(0)?;
    }
    Some(target)
}

/// The nodes this node makes the engine compare by equality only, other than names (which already carry a digest).
pub fn digest_targets(node: Node) -> Vec<Node> {
    let receiver = node.child_by_field_name("object").filter(|_| RECEIVER_KINDS.contains(&node.kind()));
    let constructor = node.child_by_field_name("constructor").filter(|_| node.kind() == CONSTRUCTOR_KIND);
    let decorated = Some(node).filter(|found| found.kind() == DECORATOR_KIND).and_then(decorator_target);
    [returned_expression(node), receiver, constructor, decorated].into_iter().flatten().filter(|target| !NAME_KINDS.contains(&target.kind())).collect()
}

/// `@app.task`, `@shared_task`, `@celery.task(bind=True)` -> the node named `task` / `shared_task` (what the engine looks up).
fn decorator_target(decorator: Node) -> Option<Node> {
    let mut target = decorator.named_child(0);
    if target.is_some_and(|found| found.kind() == "call") {
        target = target.and_then(|found| found.child_by_field_name("function"));
    }
    if target.is_some_and(|found| found.kind() == "attribute") {
        target = target.and_then(|found| found.child_by_field_name("attribute"));
    }
    target
}
