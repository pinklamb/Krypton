//! What a function is apart from its name, comments, docstring and layout: a hash of its tokens, and its parameter list. Two versions with
//! the same fingerprint differ only in comments or spacing (a cosmetic change) or in name (a rename, or a move when the file differs).

use std::hash::{DefaultHasher, Hash, Hasher};

use tree_sitter::Node;

/// A bare string as a statement: a docstring, which documents rather than does.
fn is_docstring(node: Node<'_>) -> bool {
    node.kind() == "expression_statement" && node.named_child_count() == 1 && node.named_child(0).is_some_and(|only| only.kind() == "string")
}

/// The node holding the function itself: the value of `const f = () => ...` or a class field, else the node.
fn body_of(node: Node<'_>) -> Node<'_> {
    node.child_by_field_name("value").unwrap_or(node)
}

/// A hash of every token of the function except its own name, comments and docstrings. Stable within one run, which is all it is
/// compared across.
#[must_use]
pub fn fingerprint(function: Node<'_>, source: &str) -> u64 {
    let name = function.child_by_field_name("name").map(|name| name.id());
    let mut hasher = DefaultHasher::new();
    let mut stack = vec![function];
    while let Some(node) = stack.pop() {
        if Some(node.id()) == name || node.kind() == "comment" || is_docstring(node) {
            continue;
        }
        if node.child_count() == 0 {
            source.get(node.byte_range()).hash(&mut hasher);
            continue;
        }
        // children go on the stack last first, so they come off in source order
        let (start, mut cursor) = (stack.len(), node.walk());
        stack.extend(node.children(&mut cursor));
        if let Some(pushed) = stack.get_mut(start..) {
            pushed.reverse();
        }
    }
    hasher.finish()
}

/// The parameter list without spacing: `(a, b=1)` and `( a,  b = 1 )` are the same.
#[must_use]
pub fn parameters(function: Node<'_>, source: &str) -> String {
    let body = body_of(function);
    let list = body.child_by_field_name("parameters").or_else(|| body.child_by_field_name("parameter"));
    list.and_then(|list| source.get(list.byte_range())).map_or_else(String::new, |text| text.chars().filter(|letter| !letter.is_whitespace()).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use krypton_client::{parse, Lang};

    fn first_function(source: &str) -> (u64, String) {
        let tree = parse(Lang::Python, source).unwrap();
        let function = tree.root_node().named_child(0).unwrap();
        (fingerprint(function, source), parameters(function, source))
    }

    // Comments, docstrings, spacing and the name do not count; a token or a parameter does.
    #[test]
    fn test_fingerprint() {
        let base = first_function("def f(a, b):\n    return a + b\n");
        assert_eq!(first_function("def f( a,b ):\n    \"\"\"Adds.\"\"\"\n    # sum\n    return a  +  b\n"), base, "cosmetic");
        assert_eq!(first_function("def g(a, b):\n    return a + b\n").0, base.0, "a rename keeps the fingerprint");
        assert_ne!(first_function("def f(a, b):\n    return a - b\n").0, base.0, "logic");
        assert_ne!(first_function("def f(a, b, c):\n    return a + b\n").1, base.1, "parameters");
    }
}
