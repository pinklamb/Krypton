//! HTTP routes, so a call that goes through a URL counts like a function call. A frontend `fetch("/api/scan")`, a test client's
//! `post("/api/scan")` and the server's handler for `/api/scan` have no function call between them; without this, a change to both sides
//! would look like two unrelated edits and the handler would look like a function nothing calls.
//!
//! Each URL path becomes a route node in the graph: code that requests the path links to the node, and the node links to the path's
//! handlers, which are marked as entry points (called from outside, never "unused"). The path is compared as plain text on this machine;
//! nothing here is sent anywhere.
//!
//! What registers a handler:
//! * a call with a path and a function: Express `app.get("/x", handler)`, Django `path("x/", views.x)`, Flask `add_url_rule`;
//! * a decorator with a path: Flask and `FastAPI` `@app.route("/x")`, `@app.get("/x")`, `@router.post("/x")`;
//! * a mapping from a path to a function: `{"/x": self.x}`, `{"/x": () => ...}`;
//! * a guard: `if path == "/x":` (or `elif`, `===`, a `case "/x":`) runs the calls in its block for that path;
//! * a Next.js route file: `app/api/x/route.ts` exporting `GET` or `POST`, and `pages/api/x.ts`.
//!
//! What requests a path: any other call with a path among its arguments (`fetch`, `axios.get`, a test client's `post`, `requests.get`),
//! including template strings and f-strings (`` `${API}/api/scan` ``, `f"{base}/api/scan"`).

use tree_sitter::Node;

/// Path parameters in any framework's spelling, all compared as `*`: `:id`, `{id}`, `<int:id>`, `[id]`, `${id}`.
fn segment(part: &str) -> &str {
    let parameter = part.starts_with([':', '{', '<', '[']) || part.contains('*');
    if parameter {
        "*"
    } else {
        part
    }
}

/// A URL path as routes are compared: no scheme or host, no query, no trailing slash, parameters as `*`. `None` for text that is not a path
/// (`"hello"`, `"/"`, a sentence, a number).
#[must_use]
pub fn normalize(raw: &str) -> Option<String> {
    let after_host = raw.split_once("://").map_or(raw, |(_, rest)| rest.find('/').map_or("", |slash| rest.get(slash..).unwrap_or("")));
    let path = after_host.trim_start_matches('*');
    let path = path.split(['?', '#']).next().unwrap_or(path);
    if !path.starts_with('/') || path.chars().any(char::is_whitespace) {
        return None;
    }
    let parts: Vec<&str> = path.split('/').filter(|part| !part.is_empty()).map(segment).collect();
    let named = parts.iter().any(|part| part.chars().any(|letter| letter.is_ascii_alphabetic()));
    named.then(|| format!("/{}", parts.join("/")))
}

/// The text of a string or template literal, with each interpolation as `*`; `None` for any other node.
fn literal_text(node: Node<'_>, source: &str) -> Option<String> {
    if !matches!(node.kind(), "string" | "template_string") {
        return None;
    }
    let mut cursor = node.walk();
    let parts = node.children(&mut cursor).filter_map(|part| match part.kind() {
        "string_content" | "string_fragment" | "escape_sequence" => source.get(part.byte_range()),
        "interpolation" | "template_substitution" => Some("*"),
        _ => None,
    });
    Some(parts.collect())
}

/// The route a literal names, if it is a string that looks like a URL path.
#[must_use]
pub fn route_path(node: Node<'_>, source: &str) -> Option<String> {
    normalize(&literal_text(node, source)?)
}

/// A function value, not a call: an inline function, or a name or attribute that may name one.
#[must_use]
pub fn is_inline_function(node: Node<'_>) -> bool {
    matches!(node.kind(), "lambda" | "arrow_function" | "function_expression" | "function")
}

/// A path, and the call's other arguments (handler candidates).
pub type Routed<'t> = (String, Vec<Node<'t>>);

/// The first path among a call's arguments, and the other arguments (handler candidates).
#[must_use]
pub fn call_route<'t>(call: Node<'t>, source: &str) -> Option<Routed<'t>> {
    if !matches!(call.kind(), "call" | "call_expression") {
        return None;
    }
    let arguments = call.child_by_field_name("arguments")?;
    let mut cursor = arguments.walk();
    let all: Vec<Node<'t>> = arguments.named_children(&mut cursor).collect();
    let path = all.iter().find_map(|argument| route_path(*argument, source))?;
    let others = all.into_iter().filter(|argument| !matches!(argument.kind(), "string" | "template_string")).collect();
    Some((path, others))
}

/// The path an anonymous function handles: an inline handler passed with a path (`app.get("/x", (req, res) => ...)`) or mapped from one
/// (`{"/x": lambda: ...}`).
#[must_use]
pub fn anonymous_handler(node: Node<'_>, source: &str) -> Option<String> {
    if !is_inline_function(node) {
        return None;
    }
    let parent = node.parent()?;
    match parent.kind() {
        "pair" => route_path(parent.child_by_field_name("key")?, source),
        "arguments" | "argument_list" => call_route(parent.parent()?, source).map(|(path, _)| path),
        _ => None,
    }
}

/// The paths of the decorators on a decorated Python function: `@app.route("/x")`, `@router.get("/x")`.
#[must_use]
pub fn decorator_routes(definition: Node<'_>, source: &str) -> Vec<String> {
    let Some(decorated) = definition.parent().filter(|parent| parent.kind() == "decorated_definition") else { return Vec::new() };
    let mut cursor = decorated.walk();
    let decorators = decorated.children(&mut cursor).filter(|child| child.kind() == "decorator");
    decorators.filter_map(|decorator| decorator.named_child(0)).filter_map(|call| call_route(call, source)).map(|(path, _)| path).collect()
}

/// Whether a call is a decorator's, whose route belongs to the function it decorates.
#[must_use]
pub fn in_decorator(call: Node<'_>) -> bool {
    call.parent().is_some_and(|parent| parent.kind() == "decorator")
}

/// A path a comparison tests (`path == "/x"`, `"/x" === url`), if any.
fn compared_path(node: Node<'_>, source: &str) -> Option<String> {
    let equality = match node.kind() {
        "comparison_operator" => source.get(node.byte_range()).is_some_and(|text| text.contains("==")),
        "binary_expression" => {
            node.child_by_field_name("operator").and_then(|operator| source.get(operator.byte_range())).is_some_and(|op| op.starts_with("=="))
        }
        _ => false,
    };
    if !equality {
        return None;
    }
    let mut cursor = node.walk();
    let mut operands = node.named_children(&mut cursor);
    operands.find_map(|operand| route_path(operand, source))
}

/// The block a guard runs for its path: an `if` or `elif` whose condition compares the path, or a `case "/x":`.
fn guarded_block(node: Node<'_>) -> Option<Node<'_>> {
    let mut current = node;
    for _ in 0..4 {
        let parent = current.parent()?;
        if matches!(parent.kind(), "if_statement" | "elif_clause") {
            let condition = parent.child_by_field_name("condition")?;
            let inside = condition.start_byte() <= node.start_byte() && node.end_byte() <= condition.end_byte();
            return inside.then(|| parent.child_by_field_name("consequence")).flatten();
        }
        current = parent;
    }
    None
}

/// A guard: the path, and the 1-based lines of the block that runs for it.
#[must_use]
pub fn guard(node: Node<'_>, source: &str) -> Option<(String, u32, u32)> {
    let line = |row: usize| u32::try_from(row.saturating_add(1)).unwrap_or(u32::MAX);
    if node.kind() == "switch_case" {
        let path = route_path(node.child_by_field_name("value")?, source)?;
        return Some((path, line(node.start_position().row), line(node.end_position().row)));
    }
    let path = compared_path(node, source)?;
    let block = guarded_block(node)?;
    Some((path, line(block.start_position().row), line(block.end_position().row)))
}

/// The route a Next.js file serves, from where it sits: `app/api/items/[id]/route.ts` serves `/api/items/*` (route groups such as
/// `(shop)` are left out), `pages/api/items.ts` serves `/api/items`.
#[must_use]
pub fn file_route(path: &str) -> Option<String> {
    let parts: Vec<&str> = path.split('/').collect();
    let (stem, folders) = parts.split_last()?;
    let stem = stem.rsplit_once('.').map_or(*stem, |(stem, _)| stem);
    if stem == "route" {
        let app = folders.iter().rposition(|folder| *folder == "app")?;
        let inside = folders.get(app.saturating_add(1)..)?.iter().filter(|folder| !folder.starts_with('('));
        return normalize(&format!("/{}", inside.copied().collect::<Vec<_>>().join("/")));
    }
    let pages = folders.iter().rposition(|folder| *folder == "pages")?;
    let inside: Vec<&str> = folders.get(pages.saturating_add(1)..)?.to_vec();
    let file = if stem == "index" { Vec::new() } else { vec![stem] };
    (inside.first() == Some(&"api")).then(|| normalize(&format!("/{}", [inside, file].concat().join("/")))).flatten()
}

/// The functions of a Next.js route file that handle requests: the HTTP methods in an `app` route, any top-level function in `pages/api`.
#[must_use]
pub fn is_file_handler(path: &str, name: &str) -> bool {
    let method = matches!(name, "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "HEAD" | "OPTIONS");
    method || (path.contains("pages/api/") && !name.contains('.'))
}

#[cfg(test)]
mod tests {
    use super::*;

    // Every framework's spelling of a path comes out the same.
    #[test]
    fn test_normalize() {
        let same =
            ["/api/items/:id", "/api/items/{id}", "/api/items/<int:id>", "/api/items/[id]", "/api/items/*", "https://x.dev/api/items/*?a=1", "*/api/items/*/"];
        assert!(same.iter().all(|raw| normalize(raw).as_deref() == Some("/api/items/*")), "{:?}", same.map(normalize));
        assert_eq!(normalize("/"), None, "the root is not a route to compare");
        assert_eq!(normalize("hello"), None, "not a path");
        assert_eq!(normalize("/ a b"), None, "a sentence");
        assert_eq!(normalize("/123"), None, "no name in it");
    }

    // Next.js route files.
    #[test]
    fn test_file_route() {
        assert_eq!(file_route("web/app/api/items/[id]/route.ts").as_deref(), Some("/api/items/*"));
        assert_eq!(file_route("app/(shop)/api/cart/route.js").as_deref(), Some("/api/cart"));
        assert_eq!(file_route("pages/api/users.ts").as_deref(), Some("/api/users"));
        assert_eq!(file_route("pages/about.tsx"), None, "a page, not an API route");
        assert_eq!(file_route("src/route.ts"), None, "not under app/");
        assert!(is_file_handler("app/api/x/route.ts", "GET") && !is_file_handler("app/api/x/route.ts", "helper"));
    }
}
