//! Which class an object holds, read from how it was made or declared:
//!
//! * Python: `store = Sessions(cfg)`, `self.store = auth.Sessions(cfg)`, a class attribute `store = Sessions()`, `self.store: Sessions = ...`,
//!   a typed parameter `def f(store: Sessions)`;
//! * JavaScript and TypeScript: `const c = new Client()`, `this.c = new api.Client()`, a class field `c = new Client()` or `c: Client`,
//!   a typed parameter `(c: Client)`, and `constructor(private c: Client)`.
//!
//! Only these direct forms count. A value returned by a function, passed through a list, or assigned from another variable is not followed,
//! so a call on it stays unresolved rather than guessed.

use tree_sitter::Node;

/// A class as code names it: `Sessions`, or `auth.Sessions` (a class of the module imported as `auth`).
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ClassRef {
    pub module: Option<String>,
    pub name: String,
}

/// What a function returns, when the code says: a class (`-> Visitor`, `return Visitor(...)`, `return new Client()`, or a call whose
/// result is resolved later), or a local name whose class the function states (`return visitor`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Returned {
    Class(ClassRef),
    Name(String),
}

/// What a `return` statement's value says about the class returned.
#[must_use]
pub fn returned(value: Node<'_>, source: &str) -> Option<Returned> {
    if value.kind() == "identifier" {
        return text(value, source).map(|name| Returned::Name(name.to_string()));
    }
    made_from(value, source).map(Returned::Class)
}

/// The class a function's return annotation names (`-> Visitor`, `(): Visitor`).
#[must_use]
pub fn return_annotation(function: Node<'_>, source: &str) -> Option<ClassRef> {
    class_ref(function.child_by_field_name("return_type")?, source)
}

/// A stored value and the class it holds.
pub type Stored = (Target, ClassRef);

/// Where a value is stored, as written: on the own object (`self.x`, `this.x`, a class field) or under a plain name.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Target {
    OnSelf(String),
    Name(String),
}

fn text<'s>(node: Node<'_>, source: &'s str) -> Option<&'s str> {
    source.get(node.byte_range())
}

fn is_self(node: Node<'_>, source: &str) -> bool {
    node.kind() == "this" || (node.kind() == "identifier" && matches!(text(node, source), Some("self" | "cls")))
}

/// The text of a field that is a plain name.
fn plain(node: Node<'_>, field: &str, source: &str) -> Option<String> {
    let found = node.child_by_field_name(field)?;
    matches!(found.kind(), "identifier" | "property_identifier" | "type_identifier" | "private_property_identifier")
        .then(|| text(found, source))
        .flatten()
        .map(str::to_string)
}

/// `a.b` as (`a`, `b`) when both are plain names: an attribute (Python), a member (JavaScript) or a nested type (TypeScript).
fn dotted(node: Node<'_>, source: &str) -> Option<ClassRef> {
    let (object, name) = match node.kind() {
        "attribute" => ("object", "attribute"),
        "member_expression" => ("object", "property"),
        "nested_type_identifier" => ("module", "name"),
        _ => return None,
    };
    Some(ClassRef { module: Some(plain(node, object, source)?), name: plain(node, name, source)? })
}

/// The class a type or a callee names: `Sessions`, `auth.Sessions`, `Client<T>`, or the type inside an annotation.
#[must_use]
pub fn class_ref(node: Node<'_>, source: &str) -> Option<ClassRef> {
    match node.kind() {
        "identifier" | "type_identifier" => Some(ClassRef { module: None, name: text(node, source)?.to_string() }),
        "generic_type" => class_ref(node.child_by_field_name("name")?, source),
        "type" | "type_annotation" => class_ref(node.named_child(0)?, source),
        _ => dotted(node, source),
    }
}

/// The class a value is made from: `Sessions(...)`, `auth.Sessions(...)`, `new Client(...)`.
fn made_from(value: Node<'_>, source: &str) -> Option<ClassRef> {
    match value.kind() {
        "call" | "call_expression" => class_ref(value.child_by_field_name("function")?, source),
        "new_expression" => class_ref(value.child_by_field_name("constructor")?, source),
        _ => None,
    }
}

/// What an assignment writes to: a plain name, or a field of the own object.
fn target_of(node: Node<'_>, source: &str) -> Option<Target> {
    if node.kind() == "identifier" {
        return Some(Target::Name(text(node, source)?.to_string()));
    }
    let field = match node.kind() {
        "attribute" => "attribute",
        "member_expression" => "property",
        _ => return None,
    };
    is_self(node.child_by_field_name("object")?, source).then(|| plain(node, field, source).map(Target::OnSelf)).flatten()
}

/// The class from a type annotation, which says what the code means to hold, else from a value that makes one.
fn value_or_type(node: Node<'_>, value: &str, kind: &str, source: &str) -> Option<ClassRef> {
    let declared = node.child_by_field_name(kind).and_then(|annotation| class_ref(annotation, source));
    declared.or_else(|| node.child_by_field_name(value).and_then(|value| made_from(value, source)))
}

/// A Python typed parameter's name: `typed_default_parameter` names it, `typed_parameter` holds it as its first child.
fn python_parameter(node: Node<'_>, source: &str) -> Option<String> {
    let name = node.child_by_field_name("name").or_else(|| node.named_child(0))?;
    (name.kind() == "identifier").then(|| text(name, source)).flatten().map(str::to_string)
}

/// What an assignment's left side writes to.
fn assigned(node: Node<'_>, source: &str) -> Option<Target> {
    target_of(node.child_by_field_name("left")?, source)
}

/// The class made by the value in `field`.
fn made_in(node: Node<'_>, field: &str, source: &str) -> Option<ClassRef> {
    made_from(node.child_by_field_name(field)?, source)
}

/// The class a parameter's type annotation names.
fn annotated(node: Node<'_>, source: &str) -> Option<ClassRef> {
    class_ref(node.child_by_field_name("type")?, source)
}

/// `sources, budget = {}, Budget(limits)`: each name on the left with the class its value on the right makes.
fn unpacked(node: Node<'_>, source: &str) -> Option<Vec<Stored>> {
    let (left, right) = (node.child_by_field_name("left")?, node.child_by_field_name("right")?);
    let tuple = matches!(left.kind(), "pattern_list" | "tuple_pattern") && matches!(right.kind(), "expression_list" | "tuple");
    if !tuple {
        return None;
    }
    let (mut targets, mut values) = (left.walk(), right.walk());
    let pairs = left.named_children(&mut targets).zip(right.named_children(&mut values));
    Some(pairs.filter_map(|(target, value)| Some((target_of(target, source)?, made_from(value, source)?))).collect())
}

/// The values `node` stores with the class each holds: usually none or one, several for a tuple assignment.
#[must_use]
pub fn held_by(node: Node<'_>, source: &str) -> Vec<Stored> {
    if node.kind() == "assignment" {
        if let Some(pairs) = unpacked(node, source) {
            return pairs;
        }
    }
    single(node, source).into_iter().collect()
}

/// A value stored with the class it holds, if `node` stores exactly one.
fn single(node: Node<'_>, source: &str) -> Option<Stored> {
    let (target, class) = match node.kind() {
        "assignment" => (assigned(node, source), value_or_type(node, "right", "type", source)),
        "assignment_expression" => (assigned(node, source), made_in(node, "right", source)),
        "variable_declarator" => (plain(node, "name", source).map(Target::Name), value_or_type(node, "value", "type", source)),
        "public_field_definition" => (plain(node, "name", source).map(Target::OnSelf), value_or_type(node, "value", "type", source)),
        "field_definition" => (plain(node, "property", source).map(Target::OnSelf), made_in(node, "value", source)),
        "typed_parameter" | "typed_default_parameter" => (python_parameter(node, source).map(Target::Name), annotated(node, source)),
        "required_parameter" | "optional_parameter" => (plain(node, "pattern", source).map(Target::Name), annotated(node, source)),
        _ => return None,
    };
    Some((target?, class?))
}

/// Whether a TypeScript constructor parameter is also a field (`constructor(private c: Client)`).
#[must_use]
pub fn is_parameter_field(node: Node<'_>) -> bool {
    let mut cursor = node.walk();
    let found = node.children(&mut cursor).any(|child| child.kind() == "accessibility_modifier" || child.kind() == "readonly");
    found && matches!(node.kind(), "required_parameter" | "optional_parameter")
}

/// The classes a class extends: Python `class A(B, mod.C)`, JavaScript and TypeScript `class A extends B`.
#[must_use]
pub fn bases_of(node: Node<'_>, source: &str) -> Vec<ClassRef> {
    if let Some(list) = node.child_by_field_name("superclasses") {
        let mut cursor = list.walk();
        return list.named_children(&mut cursor).filter_map(|base| class_ref(base, source)).collect();
    }
    let mut cursor = node.walk();
    let heritage = node.children(&mut cursor).filter(|child| child.kind() == "class_heritage");
    heritage.flat_map(|clause| extended(clause, source)).collect()
}

/// The class named in a `class_heritage`: the expression itself (JavaScript) or an `extends_clause`'s value (TypeScript).
fn extended(heritage: Node<'_>, source: &str) -> Vec<ClassRef> {
    let mut cursor = heritage.walk();
    let named =
        heritage.named_children(&mut cursor).filter_map(|part| if part.kind() == "extends_clause" { part.child_by_field_name("value") } else { Some(part) });
    named.filter(|part| part.kind() != "implements_clause").filter_map(|part| class_ref(part, source)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use krypton_client::{parse, walk_events, Event, Lang};

    fn held(lang: Lang, source: &str) -> Vec<Stored> {
        let tree = parse(lang, source).unwrap();
        let mut found = Vec::new();
        walk_events(tree.root_node(), |event| {
            if let Event::Enter(node, _) = event {
                found.extend(held_by(node, source));
            }
        });
        found
    }

    fn class(module: Option<&str>, name: &str) -> ClassRef {
        ClassRef { module: module.map(str::to_string), name: name.to_string() }
    }

    // Python: made from a class, a module's class, annotated, a class attribute and a typed parameter.
    #[test]
    fn test_python() {
        let source = "store = Sessions()\nsources, budget = {}, Budget(limits)\nclass H:\n    s = auth.Sessions(cfg)\n    def __init__(self, c: Client):\n        self.db: Db = connect()\n        self.q = Queue()\n        x = y\n";
        assert_eq!(
            held(Lang::Python, source),
            [
                (Target::Name("store".into()), class(None, "Sessions")),
                (Target::Name("budget".into()), class(None, "Budget")),
                (Target::Name("s".into()), class(Some("auth"), "Sessions")),
                (Target::Name("c".into()), class(None, "Client")),
                (Target::OnSelf("db".into()), class(None, "Db")),
                (Target::OnSelf("q".into()), class(None, "Queue")),
            ],
            "x = y is not followed; the annotation on self.db wins over connect()"
        );
    }

    // TypeScript: new, a field, a typed parameter and a constructor parameter field.
    #[test]
    fn test_typescript() {
        let source = "const c = new api.Client();\nclass A { s: Store; t = new Timer(); constructor(private d: Db) { this.q = new Queue(); } }\nfunction f(x: Map<string, Item>) {}\n";
        assert_eq!(
            held(Lang::TypeScript, source),
            [
                (Target::Name("c".into()), class(Some("api"), "Client")),
                (Target::OnSelf("s".into()), class(None, "Store")),
                (Target::OnSelf("t".into()), class(None, "Timer")),
                (Target::Name("d".into()), class(None, "Db")),
                (Target::OnSelf("q".into()), class(None, "Queue")),
                (Target::Name("x".into()), class(None, "Map")),
            ]
        );
    }

    // Base classes, from Python's list and TypeScript's extends.
    #[test]
    fn test_bases() {
        let bases = |lang: Lang, source: &str| {
            let tree = parse(lang, source).unwrap();
            let class_node = tree.root_node().named_child(0).unwrap();
            bases_of(class_node, source)
        };
        assert_eq!(bases(Lang::Python, "class A(Base, mod.Mixin):\n    pass\n"), [class(None, "Base"), class(Some("mod"), "Mixin")]);
        assert_eq!(bases(Lang::TypeScript, "class A extends Base implements I {}\n"), [class(None, "Base")]);
        assert_eq!(bases(Lang::JavaScript, "class A extends Base {}\n"), [class(None, "Base")]);
    }
}
