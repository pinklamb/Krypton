//! What one file says about calls: the functions it defines, the classes it defines, the calls each function makes, and what it imports.
//! Read from the syntax tree on this machine, in one iterative walk.

use krypton_client::{observe_imports, walk_events, Event, Lang, RawImports, WalkError};
use tree_sitter::Node;

use super::fingerprint::{fingerprint, parameters};
use super::holders::{bases_of, held_by, is_parameter_field, return_annotation, returned, ClassRef, Returned, Target};
use super::routes::{anonymous_handler, call_route, decorator_routes, guard, in_decorator, is_inline_function, route_path};

/// A function or method defined in a file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Definition {
    /// With its enclosing classes and functions: `Client.connect`, `outer.inner`.
    pub name: String,
    /// The last part of the name: what a call names.
    pub short: String,
    /// The class it is a method of, when it sits directly in one.
    pub class: Option<String>,
    /// 1-based, inclusive.
    pub start_line: u32,
    pub end_line: u32,
    /// What it returns, when the code says (see `holders::Returned`).
    pub returns: Vec<Returned>,
    /// Its tokens apart from its name, comments and layout, hashed (see `fingerprint`).
    pub fingerprint: u64,
    /// Its parameter list without spacing.
    pub params: String,
}

/// How a call names what it calls.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Callee {
    /// `charge()`
    Plain(String),
    /// `self.charge()`, `this.charge()`
    OnSelf(String),
    /// `self.store.get()`, `this.store.get()`: a method on a field of the own object.
    OnField(String, String),
    /// `billing.charge()`, `Client.create()`: called on a plain name, a module or a class.
    Qualified(String, String),
    /// `order.items.charge()`: a method on something only known when the code runs.
    Method(String),
    /// A call with no name to follow: `handlers[kind]()`, `make()()`.
    Unnamed,
}

/// One call, and the function it is made from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Call {
    /// Index into the file's definitions; `None` for a call at the top level of the file.
    pub caller: Option<usize>,
    pub callee: Callee,
    /// 1-based.
    pub line: u32,
}

/// A class the file defines, and the classes it extends.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClassInfo {
    pub name: String,
    pub bases: Vec<ClassRef>,
}

/// Where an object is kept: a field of a class (`self.x`, `this.x`, a class attribute or field), a name inside a function, or a name at the
/// top of the file.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Holder {
    Field { class: String, field: String },
    Local { function: usize, name: String },
    Global(String),
}

/// What a file says about HTTP routes (see `routes`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RouteMark {
    /// A function that handles the path, by index into the file's definitions.
    Handler { path: String, function: usize },
    /// A call or mapping that names possible handlers (`app.get("/x", handler)`, `{"/x": self.x}`). If none of them is a function of
    /// the repository, it is a request for the path instead (`fetch("/x", options)`).
    Named { path: String, caller: Option<usize>, handlers: Vec<Callee> },
    /// Code that requests the path.
    Use { path: String, caller: Option<usize> },
    /// A block that runs for the path (`if path == "/x":`): the calls in it, and the function holding it, handle the path.
    Guard { path: String, caller: Option<usize>, start_line: u32, end_line: u32 },
}

/// Everything the call graph needs from one file.
#[derive(Debug, Default)]
pub struct FileCalls {
    pub definitions: Vec<Definition>,
    pub calls: Vec<Call>,
    pub classes: Vec<ClassInfo>,
    /// The objects whose class the code states: where each is kept, and its class.
    pub held: Vec<(Holder, ClassRef)>,
    pub routes: Vec<RouteMark>,
    pub imports: RawImports,
}

/// A class or function the walk is inside, with the id of the node that opened it.
enum Scope {
    Class(String),
    Function(usize),
}

const NAME_KINDS: [&str; 4] = ["identifier", "property_identifier", "private_property_identifier", "type_identifier"];
const FUNCTION_VALUES: [&str; 4] = ["arrow_function", "function_expression", "function", "generator_function"];
const CLASS_KINDS: [&str; 4] = ["class_definition", "class_declaration", "abstract_class_declaration", "class"];

fn text<'s>(node: Node<'_>, source: &'s str) -> Option<&'s str> {
    source.get(node.byte_range())
}

/// The text of a node's field when it is a plain name (not a destructuring pattern or a computed name).
fn name_field(node: Node<'_>, field: &str, source: &str) -> Option<String> {
    let name = node.child_by_field_name(field)?;
    NAME_KINDS.contains(&name.kind()).then(|| text(name, source)).flatten().map(str::to_string)
}

/// The name a node gives a function: a declaration, a method, or a variable or class field holding a function.
fn function_name(node: Node<'_>, source: &str) -> Option<String> {
    match node.kind() {
        "function_definition" | "function_declaration" | "generator_function_declaration" | "method_definition" => name_field(node, "name", source),
        "variable_declarator" | "public_field_definition" | "field_definition" => {
            let value = node.child_by_field_name("value")?;
            let field = if node.kind() == "field_definition" { "property" } else { "name" };
            FUNCTION_VALUES.contains(&value.kind()).then(|| name_field(node, field, source)).flatten()
        }
        _ => None,
    }
}

fn class_name(node: Node<'_>, source: &str) -> Option<String> {
    CLASS_KINDS.contains(&node.kind()).then(|| name_field(node, "name", source)).flatten()
}

fn line(row: usize) -> u32 {
    u32::try_from(row.saturating_add(1)).unwrap_or(u32::MAX)
}

fn is_self(node: Node<'_>, source: &str) -> bool {
    node.kind() == "this" || (node.kind() == "identifier" && matches!(text(node, source), Some("self" | "cls")))
}

/// `store` in `self.store` or `this.store`.
fn own_field(object: Node<'_>, source: &str) -> Option<String> {
    let field = match object.kind() {
        "attribute" => "attribute",
        "member_expression" => "property",
        _ => return None,
    };
    is_self(object.child_by_field_name("object")?, source).then(|| name_field(object, field, source)).flatten()
}

/// What a call on `object.name` is, from the object.
fn member_callee(object: Node<'_>, name: String, source: &str) -> Callee {
    if let Some(field) = own_field(object, source) {
        return Callee::OnField(field, name);
    }
    match (object.kind(), text(object, source)) {
        ("this", _) | ("identifier", Some("self" | "cls")) => Callee::OnSelf(name),
        ("identifier", Some(object)) => Callee::Qualified(object.to_string(), name),
        _ => Callee::Method(name),
    }
}

/// A JSX element naming a component (`<Header />`, `<Ui.Card>`): rendering it runs the component, so it counts as a call. A lower-case
/// name is an HTML tag, and a fragment has no name; neither is a call.
fn component_of(node: Node<'_>, source: &str) -> Option<Callee> {
    if node.kind() != "jsx_self_closing_element" && node.kind() != "jsx_opening_element" {
        return None;
    }
    let name = node.child_by_field_name("name")?;
    match name.kind() {
        "identifier" => {
            text(name, source).filter(|name| name.starts_with(|first: char| first.is_ascii_uppercase())).map(|name| Callee::Plain(name.to_string()))
        }
        "member_expression" => Some(member_callee(name.child_by_field_name("object")?, name_field(name, "property", source)?, source)),
        _ => None,
    }
}

/// What a call node, or a JSX element naming a component, calls; `None` for any other node.
fn callee_of(node: Node<'_>, source: &str) -> Option<Callee> {
    if node.kind() != "call" && node.kind() != "call_expression" {
        return component_of(node, source);
    }
    Some(target_callee(node.child_by_field_name("function")?, source))
}

/// What a name or attribute refers to as a callee: `charge`, `self.charge`, `billing.charge`.
fn target_callee(target: Node<'_>, source: &str) -> Callee {
    match target.kind() {
        "identifier" => text(target, source).map_or(Callee::Unnamed, |name| Callee::Plain(name.to_string())),
        "attribute" | "member_expression" => {
            let field = if target.kind() == "attribute" { "attribute" } else { "property" };
            let object = target.child_by_field_name("object");
            match (object, name_field(target, field, source)) {
                (Some(object), Some(name)) => member_callee(object, name, source),
                _ => Callee::Unnamed,
            }
        }
        _ => Callee::Unnamed,
    }
}

/// A value that may name a function (`handler`, `self.handler`, `views.index`), as the callee it would be.
fn function_value(node: Node<'_>, source: &str) -> Option<Callee> {
    Some(target_callee(node, source)).filter(|callee| *callee != Callee::Unnamed)
}

/// The walk's state: where it is and what it has found.
struct Reader<'s> {
    source: &'s str,
    lang: Lang,
    scopes: Vec<(usize, Scope)>,
    found: FileCalls,
    failure: Option<WalkError>,
}

impl Reader<'_> {
    /// The enclosing classes and functions, each followed by a dot: `Client.retry.`.
    fn prefix(&self) -> String {
        let names: Vec<&str> = self
            .scopes
            .iter()
            .filter_map(|(_, scope)| match scope {
                Scope::Class(name) => Some(name.as_str()),
                Scope::Function(index) => self.found.definitions.get(*index).map(|definition| definition.short.as_str()),
            })
            .collect();
        let mut prefix = names.join(".");
        if !prefix.is_empty() {
            prefix.push('.');
        }
        prefix
    }

    fn define(&mut self, node: Node<'_>, short: String) -> usize {
        let class = match self.scopes.last() {
            Some((_, Scope::Class(name))) => Some(name.clone()),
            _ => None,
        };
        let name = format!("{}{short}", self.prefix());
        let (start_line, end_line) = (line(node.start_position().row), line(node.end_position().row));
        let returns = return_annotation(node, self.source).map(Returned::Class).into_iter().collect();
        let (fingerprint, params) = (fingerprint(node, self.source), parameters(node, self.source));
        self.found.definitions.push(Definition { name, short, class, start_line, end_line, returns, fingerprint, params });
        self.found.definitions.len().saturating_sub(1)
    }

    fn innermost_class(&self) -> Option<String> {
        self.scopes.iter().rev().find_map(|(_, scope)| match scope {
            Scope::Class(name) => Some(name.clone()),
            Scope::Function(_) => None,
        })
    }

    /// Where a name is kept, from the scope it is written in: a function's local, a class attribute, or a top-level name.
    fn holder_of(&self, target: Target) -> Option<Holder> {
        match (target, self.scopes.last()) {
            (Target::OnSelf(field), _) => Some(Holder::Field { class: self.innermost_class()?, field }),
            (Target::Name(name), Some((_, Scope::Function(index)))) => Some(Holder::Local { function: *index, name }),
            (Target::Name(field), Some((_, Scope::Class(class)))) => Some(Holder::Field { class: class.clone(), field }),
            (Target::Name(name), None) => Some(Holder::Global(name)),
        }
    }

    /// Note the class of an object this node stores, if it states one.
    fn hold(&mut self, node: Node<'_>) {
        for (target, class) in held_by(node, self.source) {
            self.keep(node, target, class);
        }
    }

    /// Keep one stored object's class, under the holder its scope gives it.
    fn keep(&mut self, node: Node<'_>, target: Target, class: ClassRef) {
        if let (true, Target::Name(field), Some(owner)) = (is_parameter_field(node), &target, self.innermost_class()) {
            self.found.held.push((Holder::Field { class: owner, field: field.clone() }, class.clone()));
        }
        if let Some(holder) = self.holder_of(target) {
            self.found.held.push((holder, class));
        }
    }

    fn caller(&self) -> Option<usize> {
        self.scopes.iter().rev().find_map(|(_, scope)| match scope {
            Scope::Function(index) => Some(*index),
            Scope::Class(_) => None,
        })
    }

    fn enter(&mut self, node: Node<'_>) {
        if let Some(name) = class_name(node, self.source) {
            self.found.classes.push(ClassInfo { name: name.clone(), bases: bases_of(node, self.source) });
            self.scopes.push((node.id(), Scope::Class(name)));
        } else if let Some(name) = function_name(node, self.source) {
            let index = self.define(node, name);
            self.scopes.push((node.id(), Scope::Function(index)));
            for path in decorator_routes(node, self.source) {
                self.found.routes.push(RouteMark::Handler { path, function: index });
            }
        } else if let Some(path) = anonymous_handler(node, self.source) {
            // an inline handler gets a name, so the calls it makes belong to it rather than to the code around it
            let index = self.define(node, format!("route {path}"));
            self.scopes.push((node.id(), Scope::Function(index)));
            self.found.routes.push(RouteMark::Handler { path, function: index });
        }
        self.route_marks(node);
        if let Some(callee) = callee_of(node, self.source) {
            self.found.calls.push(Call { caller: self.caller(), callee, line: line(node.start_position().row) });
        }
        self.hold(node);
        self.note_return(node);
        if self.failure.is_none() {
            self.failure = observe_imports(&mut self.found.imports, node, self.source, self.lang).err();
        }
    }

    /// Note what a `return` gives back, on the function it returns from.
    fn note_return(&mut self, node: Node<'_>) {
        if node.kind() != "return_statement" {
            return;
        }
        let value = node.named_child(0).and_then(|value| returned(value, self.source));
        if let (Some(value), Some(function)) = (value, self.caller().and_then(|index| self.found.definitions.get_mut(index))) {
            function.returns.push(value);
        }
    }

    /// Note a route this node registers, requests or guards (inline and decorated handlers are noted where they are defined).
    fn route_marks(&mut self, node: Node<'_>) {
        let caller = self.caller();
        if let Some((path, start_line, end_line)) = guard(node, self.source) {
            self.found.routes.push(RouteMark::Guard { path, caller, start_line, end_line });
        } else if node.kind() == "pair" {
            let path = node.child_by_field_name("key").and_then(|key| route_path(key, self.source));
            let value = node.child_by_field_name("value").filter(|value| !is_inline_function(*value));
            if let (Some(path), Some(handler)) = (path, value.and_then(|value| function_value(value, self.source))) {
                self.found.routes.push(RouteMark::Named { path, caller, handlers: vec![handler] });
            }
        } else if let Some((path, others)) = call_route(node, self.source).filter(|_| !in_decorator(node)) {
            if others.iter().any(|other| is_inline_function(*other)) {
                return;
            }
            let handlers: Vec<Callee> = others.into_iter().filter_map(|other| function_value(other, self.source)).collect();
            let mark = if handlers.is_empty() { RouteMark::Use { path, caller } } else { RouteMark::Named { path, caller, handlers } };
            self.found.routes.push(mark);
        }
    }

    fn exit(&mut self, node: Node<'_>) {
        if self.scopes.last().is_some_and(|(id, _)| *id == node.id()) {
            self.scopes.pop();
        }
    }
}

/// Read one file's definitions, calls, classes and imports from its tree.
///
/// # Errors
///
/// The [`WalkError`] of an import whose text is not valid UTF-8.
pub fn read_file(root: Node<'_>, source: &str, lang: Lang) -> Result<FileCalls, WalkError> {
    let mut reader = Reader { source, lang, scopes: Vec::new(), found: FileCalls::default(), failure: None };
    walk_events(root, |event| match event {
        Event::Enter(node, _) => reader.enter(node),
        Event::Exit(node, _) => reader.exit(node),
    });
    reader.failure.map_or(Ok(reader.found), Err)
}

#[cfg(test)]
mod tests {
    use super::*;
    use krypton_client::parse;

    fn read(lang: Lang, source: &str) -> FileCalls {
        let tree = parse(lang, source).unwrap();
        read_file(tree.root_node(), source, lang).unwrap()
    }

    /// Whether the function `from` makes a call to `target`.
    fn has(found: &FileCalls, from: Option<usize>, target: &Callee) -> bool {
        found.calls.iter().any(|made| made.caller == from && &made.callee == target)
    }

    /// A definition as its name, class and lines.
    type Row<'a> = (&'a str, Option<&'a str>, u32, u32);
    /// A call as its caller and callee.
    type Made<'a> = (Option<usize>, &'a Callee);

    fn names(found: &FileCalls) -> Vec<Row<'_>> {
        found.definitions.iter().map(|d| (d.name.as_str(), d.class.as_deref(), d.start_line, d.end_line)).collect()
    }

    // Python: functions, methods with their class, nested functions, and each call's form and caller.
    #[test]
    fn test_python() {
        let source = "import billing\n\nclass Client:\n    def connect(self):\n        self.retry()\n        billing.charge(1)\n\n    def retry(self):\n        def inner():\n            pass\n        inner()\n\ndef main():\n    Client().connect()\n\nmain()\n";
        let found = read(Lang::Python, source);
        assert_eq!(
            names(&found),
            [("Client.connect", Some("Client"), 4, 6), ("Client.retry", Some("Client"), 8, 11), ("Client.retry.inner", None, 9, 10), ("main", None, 13, 14)]
        );
        let calls: Vec<Made<'_>> = found.calls.iter().map(|call| (call.caller, &call.callee)).collect();
        assert_eq!(
            calls,
            [
                (Some(0), &Callee::OnSelf("retry".into())),
                (Some(0), &Callee::Qualified("billing".into(), "charge".into())),
                (Some(1), &Callee::Plain("inner".into())),
                (Some(3), &Callee::Method("connect".into())),
                (Some(3), &Callee::Plain("Client".into())),
                (None, &Callee::Plain("main".into())),
            ],
            "calls with their callers"
        );
        assert_eq!(found.classes, [ClassInfo { name: "Client".into(), bases: Vec::new() }]);
        assert_eq!(found.imports.python.len(), 1, "the import is read");
    }

    // A method on a field of the own object, with the field's class kept as a class attribute, as Argon's server does.
    #[test]
    fn test_field_call() {
        let source = "import auth\n\nclass Handler:\n    sessions = auth.Sessions(cfg)\n\n    def _session(self):\n        return self.sessions.get(1)\n";
        let found = read(Lang::Python, source);
        assert!(has(&found, Some(0), &Callee::OnField("sessions".into(), "get".into())), "the call names the field");
        let class = ClassRef { module: Some("auth".into()), name: "Sessions".into() };
        assert_eq!(found.held, [(Holder::Field { class: "Handler".into(), field: "sessions".into() }, class)], "the field's class");
        assert_eq!(found.calls.first().map(|call| call.line), Some(4), "calls keep their line");
    }

    // TSX: a component used as an element is called; an HTML tag is not.
    #[test]
    fn test_jsx() {
        let source = "function Header() { return <div />; }\nfunction App() { return <main><Header /><Ui.Card title=\"x\"></Ui.Card><></></main>; }\n";
        let found = read(Lang::Tsx, source);
        let calls: Vec<&Callee> = found.calls.iter().filter(|call| call.caller == Some(1)).map(|call| &call.callee).collect();
        assert_eq!(calls, [&Callee::Plain("Header".into()), &Callee::Qualified("Ui".into(), "Card".into())], "components only");
        assert!(found.calls.iter().all(|call| call.caller == Some(1)), "div and main are not calls");
    }

    // JavaScript and TypeScript: declarations, arrow functions in variables and class fields, `this` calls, and imports.
    #[test]
    fn test_javascript() {
        let source = "import { charge } from './billing';\nconst total = (cart) => cart.sum();\nclass Shop {\n  pay = () => { this.log(); charge(total(1)); };\n  log() {}\n}\nfunction run() { handlers[x](); }\n";
        let found = read(Lang::TypeScript, source);
        let defined: Vec<&str> = found.definitions.iter().map(|d| d.name.as_str()).collect();
        assert_eq!(defined, ["total", "Shop.pay", "Shop.log", "run"], "definitions");
        assert!(has(&found, Some(1), &Callee::OnSelf("log".into())), "this call");
        assert!(has(&found, Some(1), &Callee::Plain("charge".into())), "imported call");
        assert!(has(&found, Some(0), &Callee::Qualified("cart".into(), "sum".into())), "call on a parameter");
        assert!(has(&found, Some(3), &Callee::Unnamed), "a call with no name");
        assert_eq!(found.imports.specifiers, ["./billing"], "the import is read");
    }
}
