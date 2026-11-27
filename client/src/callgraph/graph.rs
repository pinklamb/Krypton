//! The graph itself, on petgraph: every function (and each file's top-level code) is a node, and an edge runs from a caller to each
//! function it calls. petgraph keeps the edges and, for the footprint, gives connected groups and cycles without code of our own.

use std::collections::HashMap;

use petgraph::algo::tarjan_scc;
use petgraph::graph::{DiGraph, NodeIndex};
use petgraph::visit::EdgeRef;
use petgraph::Direction;

use super::extract::{Callee, RouteMark};
use super::resolve::{callee_name, method_name, Found, Held, Index, Site};
use super::routes::{file_route, is_file_handler};
use super::{is_test_path, Parsed};

/// How a file's top-level code is named in the graph.
pub const TOP_LEVEL: &str = "(top level)";
/// The `file` of a route node: routes belong to no file, they join the files that request and handle them.
pub const ROUTE_FILE: &str = "(route)";

/// A function, or a file's top-level code.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Function {
    pub file: String,
    pub name: String,
    pub start_line: u32,
    pub end_line: u32,
    /// In a test file, by the scorer's own test patterns.
    pub is_test: bool,
    /// A URL path rather than a function: its callers request it, the functions it calls handle it.
    pub is_route: bool,
    /// Run from outside the repository's own calls (a route's handler), so never reported as uncalled.
    pub is_entry: bool,
    /// Its tokens apart from its name, comments and layout, hashed; 0 for top-level code and routes.
    pub fingerprint: u64,
    /// Its parameter list without spacing.
    pub params: String,
}

impl Function {
    /// Whether this is a file's top-level code rather than a function.
    #[must_use]
    pub fn is_top_level(&self) -> bool {
        self.name == TOP_LEVEL
    }

    /// `file::name`, the way `krypton impact --function` and the run log name a function.
    #[must_use]
    pub fn key(&self) -> String {
        format!("{}::{}", self.file, self.name)
    }

    /// The last part of the name: what a call names.
    #[must_use]
    pub fn short(&self) -> &str {
        self.name.rsplit('.').next().unwrap_or(&self.name)
    }
}

/// How many calls the graph found, how many it could tie to a function, and how many of those rest on the name alone.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Coverage {
    pub calls: usize,
    pub resolved: usize,
    pub by_name: usize,
}

/// Every function of the repository, and who calls each one.
pub struct Graph {
    /// Node ids are dense and in file order: a file's top-level code, then its functions. An edge's weight says whether the link rests
    /// on a method's name alone.
    calls: DiGraph<Function, bool>,
    /// Each file's ids: its top-level code first, then its functions.
    files: HashMap<String, (usize, usize)>,
    coverage: Coverage,
    /// For each method name, how many calls to a method of that name could not be tied to a class for certain.
    uncertain: HashMap<String, usize>,
    /// Each URL path's route node.
    routes: HashMap<String, usize>,
    /// Files that do not parse cleanly, with the line of the first error: calls in them may be missing.
    broken: Vec<(String, u32)>,
    /// For each name, how many calls by that name led nowhere: after a function is deleted, the calls still made to it.
    unresolved: HashMap<String, usize>,
}

/// The graph id of a function by its index in a file whose top-level code has id `first`: a file's functions follow its top-level entry
/// in the order they were defined, and `None` is the top-level code itself.
const fn id_from(first: usize, number: Option<usize>) -> usize {
    match number {
        Some(number) => first.saturating_add(1).saturating_add(number),
        None => first,
    }
}

/// Where a call is made, for resolving it.
fn site<'a>(file: &'a Parsed, caller: Option<usize>, held: &'a Held) -> Site<'a> {
    let class = caller.and_then(|number| file.calls.definitions.get(number)).and_then(|definition| definition.class.as_deref());
    Site { file, caller, class, held }
}

impl Graph {
    /// Build the graph from every file of the repository.
    #[must_use]
    pub fn new(parsed: &[Parsed]) -> Self {
        let mut graph = Self {
            calls: DiGraph::new(),
            files: HashMap::new(),
            coverage: Coverage::default(),
            uncertain: HashMap::new(),
            routes: HashMap::new(),
            broken: Vec::new(),
            unresolved: HashMap::new(),
        };
        let mut index = Index::default();
        graph.broken = parsed.iter().filter_map(|file| file.syntax_error.map(|line| (file.path.clone(), line))).collect();
        for file in parsed {
            index.add_file(file);
            graph.add_file(file, &mut index);
        }
        for file in parsed {
            let Some(held) = index.held_of(&file.path) else { continue };
            let targets = graph.link_file(file, &index, held);
            graph.link_routes(file, &index, held, &targets);
        }
        graph
    }

    fn add_file(&mut self, file: &Parsed, index: &mut Index) {
        let is_test = is_test_path(&file.path);
        let first = self.calls.node_count();
        let top = TOP_LEVEL.to_string();
        let fresh = Function {
            file: file.path.clone(),
            name: top,
            start_line: 0,
            end_line: 0,
            is_test,
            is_route: false,
            is_entry: false,
            fingerprint: 0,
            params: String::new(),
        };
        self.calls.add_node(fresh);
        for (number, definition) in file.calls.definitions.iter().enumerate() {
            index.add(&file.path, (number, definition), self.calls.node_count());
            let (name, start_line, end_line) = (definition.name.clone(), definition.start_line, definition.end_line);
            let (fingerprint, params) = (definition.fingerprint, definition.params.clone());
            self.calls.add_node(Function {
                file: file.path.clone(),
                name,
                start_line,
                end_line,
                is_test,
                is_route: false,
                is_entry: false,
                fingerprint,
                params,
            });
        }
        self.files.insert(file.path.clone(), (first, self.calls.node_count()));
    }

    /// The graph id of a file's function by its index in the file (`None` is the file's top-level code).
    fn id_in(&self, file: &Parsed, number: Option<usize>) -> Option<usize> {
        let &(first, _) = self.files.get(&file.path)?;
        Some(id_from(first, number))
    }

    /// Link every call of a file; the functions each call reached for certain, in the order of the file's calls.
    fn link_file(&mut self, file: &Parsed, index: &Index, held: &Held) -> Vec<Vec<usize>> {
        let mut reached = Vec::with_capacity(file.calls.calls.len());
        for call in &file.calls.calls {
            let Some(caller) = self.id_in(file, call.caller) else { continue };
            let found = index.resolve(&site(file, call.caller, held), &call.callee);
            reached.push(if let Found::Exact(targets) = &found { targets.clone() } else { Vec::new() });
            self.count_unresolved(&found, &call.callee);
            self.record(caller, found, method_name(&call.callee));
        }
        reached
    }

    /// The route node of a path, made on first use.
    fn route_node(&mut self, path: &str) -> usize {
        if let Some(id) = self.routes.get(path) {
            return *id;
        }
        let route = Function {
            file: ROUTE_FILE.to_string(),
            name: path.to_string(),
            start_line: 0,
            end_line: 0,
            is_test: false,
            is_route: true,
            is_entry: false,
            fingerprint: 0,
            params: String::new(),
        };
        let id = self.calls.add_node(route).index();
        self.routes.insert(path.to_string(), id);
        id
    }

    /// `handler` handles `path`: the route links to it, and it is an entry point.
    fn handle(&mut self, path: &str, handler: usize) {
        let route = self.route_node(path);
        self.link(route, handler, false);
        if let Some(function) = self.calls.node_weight_mut(NodeIndex::new(handler)) {
            function.is_entry = true;
        }
    }

    /// `caller` requests `path`.
    fn request(&mut self, path: &str, caller: usize) {
        let route = self.route_node(path);
        self.link(caller, route, false);
    }

    /// Link a file's routes: its handlers, its requests, its guards, and, for a Next.js route file, the methods it exports.
    fn link_routes(&mut self, file: &Parsed, index: &Index, held: &Held, reached: &[Vec<usize>]) {
        let Some(first) = self.id_in(file, None) else { return };
        for mark in &file.calls.routes {
            match mark {
                RouteMark::Handler { path, function } => self.handle(path, id_from(first, Some(*function))),
                RouteMark::Use { path, caller } => self.request(path, id_from(first, *caller)),
                RouteMark::Named { path, caller, handlers } => self.named(file, (index, held), (path, *caller, first), handlers),
                RouteMark::Guard { path, caller, start_line, end_line } => self.guarded(file, reached, (path, *caller, first), (*start_line, *end_line)),
            }
        }
        if let Some(path) = file_route(&file.path) {
            let handlers = file.calls.definitions.iter().enumerate().filter(|(_, definition)| is_file_handler(&file.path, &definition.name));
            for (number, _) in handlers {
                self.handle(&path, id_from(first, Some(number)));
            }
        }
    }

    /// Handlers named with a path: the ones that are functions of the repository handle it; if none is, the call requests the path.
    fn named(&mut self, file: &Parsed, (index, held): (&Index, &Held), (path, caller, first): (&str, Option<usize>, usize), handlers: &[Callee]) {
        let found: Vec<usize> = handlers
            .iter()
            .flat_map(|callee| match index.resolve(&site(file, caller, held), callee) {
                Found::Exact(targets) => targets,
                Found::ByName(_) | Found::Nowhere => Vec::new(),
            })
            .collect();
        if found.is_empty() {
            self.request(path, id_from(first, caller));
        }
        for id in found {
            self.handle(path, id);
        }
    }

    /// A guard's path is handled by the function holding the guard and by every function the guarded block calls.
    fn guarded(&mut self, file: &Parsed, reached: &[Vec<usize>], (path, caller, first): (&str, Option<usize>, usize), (start, end): (u32, u32)) {
        self.handle(path, id_from(first, caller));
        let inside = file.calls.calls.iter().zip(reached).filter(|(call, _)| call.caller == caller && (start..=end).contains(&call.line));
        for id in inside.flat_map(|(_, targets)| targets.iter().copied()) {
            self.handle(path, id);
        }
    }

    fn count_unresolved(&mut self, found: &Found, callee: &Callee) {
        if let (Found::Nowhere, Some(name)) = (found, callee_name(callee)) {
            let count = self.unresolved.entry(name.to_string()).or_default();
            *count = count.saturating_add(1);
        }
    }

    fn record(&mut self, caller: usize, found: Found, method: Option<&str>) {
        self.coverage.calls = self.coverage.calls.saturating_add(1);
        let (targets, by_name) = match found {
            Found::Exact(targets) => (targets, false),
            Found::ByName(target) => (vec![target], true),
            Found::Nowhere => (Vec::new(), true),
        };
        if by_name {
            if let Some(method) = method {
                let count = self.uncertain.entry(method.to_string()).or_default();
                *count = count.saturating_add(1);
            }
        }
        if targets.is_empty() {
            return;
        }
        self.coverage.resolved = self.coverage.resolved.saturating_add(1);
        self.coverage.by_name = self.coverage.by_name.saturating_add(usize::from(by_name));
        // a function calling itself is not downstream of itself
        for target in targets.into_iter().filter(|target| *target != caller) {
            self.link(caller, target, by_name);
        }
    }

    /// One edge per caller and callee; an exact link wins over one by name alone.
    fn link(&mut self, caller: usize, target: usize, by_name: bool) {
        let (from, to) = (NodeIndex::new(caller), NodeIndex::new(target));
        if to.index() >= self.calls.node_count() {
            return;
        }
        match self.calls.find_edge(from, to).and_then(|edge| self.calls.edge_weight_mut(edge)) {
            Some(weight) => *weight = *weight && by_name,
            None => {
                self.calls.add_edge(from, to, by_name);
            }
        }
    }

    /// The function with this id.
    #[must_use]
    pub fn function(&self, id: usize) -> Option<&Function> {
        self.calls.node_weight(NodeIndex::new(id))
    }

    /// The functions that call this one, by id, each with whether the link rests on the name alone.
    #[must_use]
    pub fn callers(&self, id: usize) -> Vec<(usize, bool)> {
        let mut found: Vec<(usize, bool)> =
            self.calls.edges_directed(NodeIndex::new(id), Direction::Incoming).map(|edge| (edge.source().index(), *edge.weight())).collect();
        found.sort_unstable();
        found
    }

    /// How many calls by `name` (any form: `name()`, `x.name()`) led to no function of the repository.
    #[must_use]
    pub fn unresolved_calls(&self, name: &str) -> usize {
        self.unresolved.get(name).copied().unwrap_or(0)
    }

    /// Every node with its id: functions, top-level code and routes.
    pub fn all(&self) -> impl Iterator<Item = (usize, &Function)> + '_ {
        self.calls.node_indices().filter_map(|node| self.calls.node_weight(node).map(|function| (node.index(), function)))
    }

    /// The files that do not parse cleanly, with the line of the first error.
    #[must_use]
    pub fn broken(&self) -> &[(String, u32)] {
        &self.broken
    }

    /// How many calls the graph could follow.
    #[must_use]
    pub const fn coverage(&self) -> Coverage {
        self.coverage
    }

    /// How many calls to a method named `name` could not be tied to a class for certain: callers the graph may be missing.
    #[must_use]
    pub fn uncertain_calls(&self, name: &str) -> usize {
        self.uncertain.get(name).copied().unwrap_or(0)
    }

    /// The function named `file::name`.
    #[must_use]
    pub fn find(&self, key: &str) -> Option<usize> {
        self.calls.node_indices().find(|node| self.calls.node_weight(*node).is_some_and(|function| function.key() == key)).map(NodeIndex::index)
    }

    /// Functions, by key, in the order to untangle them: each one after the functions it calls, directly or through others, so inner
    /// helpers come first. Functions that call each other (recursion) sit together. Uses Tarjan's strongly connected components rather
    /// than a topological sort, which would fail on the first cycle; petgraph returns the components callees first. A key not in the
    /// graph keeps its place after the rest.
    #[must_use]
    pub fn helpers_first(&self, keys: &[String]) -> Vec<String> {
        let ranks: HashMap<usize, usize> =
            tarjan_scc(&self.calls).into_iter().enumerate().flat_map(|(rank, group)| group.into_iter().map(move |node| (node.index(), rank))).collect();
        let ids: HashMap<String, usize> =
            self.calls.node_indices().filter_map(|node| self.calls.node_weight(node).map(|function| (function.key(), node.index()))).collect();
        let mut ordered = keys.to_vec();
        ordered.sort_by_key(|key| ids.get(key).and_then(|id| ranks.get(id)).copied().unwrap_or(usize::MAX));
        ordered
    }

    /// The innermost function of `file` that holds the 1-based `line`, else the file's top-level code; `None` for a file not in the graph.
    #[must_use]
    pub fn innermost(&self, file: &str, line: u32) -> Option<usize> {
        let &(first, end) = self.files.get(file)?;
        let holding = (first.saturating_add(1)..end)
            .filter_map(|id| self.function(id).map(|function| (id, function)))
            .filter(|(_, function)| function.start_line <= line && line <= function.end_line);
        let smallest = holding.min_by_key(|(_, function)| function.end_line.saturating_sub(function.start_line));
        Some(smallest.map_or(first, |(id, _)| id))
    }
}

#[cfg(test)]
mod tests {
    use super::super::from_sources;
    use super::*;

    const BILLING: &str = "def charge(amount):\n    return amount\n\nclass Ledger:\n    def __init__(self):\n        pass\n\n    def record(self):\n        self.write()\n\n    def write(self):\n        pass\n";
    const CHECKOUT: &str = "from billing import charge, Ledger\n\ndef checkout(cart):\n    charge(cart.total)\n    Ledger().record()\n";
    const API: &str = "import checkout\n\ndef place_order(cart):\n    checkout.checkout(cart)\n    cart.write()\n    print(cart)\n";

    fn build(files: &[(&str, &str)]) -> Graph {
        let owned: Vec<(String, String)> = files.iter().map(|(path, source)| ((*path).to_string(), (*source).to_string())).collect();
        from_sources(&owned).unwrap()
    }

    fn graph() -> Graph {
        build(&[("billing.py", BILLING), ("checkout.py", CHECKOUT), ("api.py", API)])
    }

    fn callers_of(graph: &Graph, key: &str) -> Vec<(String, bool)> {
        let id = graph.find(key).unwrap();
        graph.callers(id).into_iter().map(|(caller, by_name)| (graph.function(caller).unwrap().key(), by_name)).collect()
    }

    // Calls are tied across files through imports, to methods through self and classes, and by name only when one method has the name.
    #[test]
    fn test_callers() {
        let graph = graph();
        assert_eq!(callers_of(&graph, "billing.py::charge"), [("checkout.py::checkout".to_string(), false)], "imported function");
        assert_eq!(callers_of(&graph, "billing.py::Ledger.__init__"), [("checkout.py::checkout".to_string(), false)], "creating the class");
        assert_eq!(callers_of(&graph, "billing.py::Ledger.record"), [("checkout.py::checkout".to_string(), true)], "a method on a new object, by name");
        // api.py calls cart.write() but cannot see billing.py, so the only write method in the repository is not tied to it
        assert_eq!(callers_of(&graph, "billing.py::Ledger.write"), [("billing.py::Ledger.record".to_string(), false)], "self call only");
        assert_eq!(callers_of(&graph, "checkout.py::checkout"), [("api.py::place_order".to_string(), false)], "called on the module's name");
        assert_eq!(graph.coverage(), Coverage { calls: 7, resolved: 5, by_name: 1 }, "print and the unseen write are not followed");
        assert_eq!((graph.uncertain_calls("write"), graph.uncertain_calls("record")), (1, 1), "missed or guessed method calls are counted");
    }

    // Argon's case: a method called on an object kept in a class attribute, a local, a typed parameter, and through a base class.
    #[test]
    fn test_objects() {
        let auth = "class Store:\n    def get(self, key):\n        return key\n\nclass Sessions(Store):\n    def drop(self):\n        pass\n";
        let server = "import auth\n\nclass Handler:\n    sessions = auth.Sessions()\n\n    def read(self):\n        return self.sessions.get(1)\n\n\
                      def clean(store: auth.Store):\n    local = auth.Sessions()\n    local.drop()\n    store.get(2)\n    {}.get(3)\n";
        let graph = build(&[("auth.py", auth), ("server.py", server)]);
        let get = callers_of(&graph, "auth.py::Store.get");
        assert_eq!(get, [("server.py::Handler.read".to_string(), false), ("server.py::clean".to_string(), false)], "field via the base class, typed parameter");
        assert_eq!(callers_of(&graph, "auth.py::Sessions.drop"), [("server.py::clean".to_string(), false)], "a local");
        assert_eq!(graph.uncertain_calls("get"), 1, "the dict's get stays unresolved, and is counted");
    }

    /// The callers of `key`, as keys, by name.
    fn caller_keys(graph: &Graph, key: &str) -> Vec<String> {
        callers_of(graph, key).into_iter().map(|(caller, _)| caller).collect()
    }

    fn is_entry(graph: &Graph, key: &str) -> bool {
        graph.find(key).and_then(|id| graph.function(id)).is_some_and(|function| function.is_entry)
    }

    // A frontend request and a Python handler meet at the route: the request's function is upstream of the handler.
    #[test]
    fn test_route_bridge() {
        let server = "from flask import Flask\napp = Flask(__name__)\n\n@app.post(\"/api/items/<int:id>\")\ndef save_item(id):\n    return store(id)\n\ndef store(id):\n    return id\n";
        let web = "const API = '';\nexport async function save(id: number) {\n  return fetch(`${API}/api/items/${id}`, { method: 'POST' });\n}\n";
        let graph = build(&[("server.py", server), ("web/api.ts", web)]);
        assert_eq!(caller_keys(&graph, "server.py::save_item"), ["(route)::/api/items/*"], "the route calls the handler");
        assert_eq!(caller_keys(&graph, "(route)::/api/items/*"), ["web/api.ts::save"], "the request calls the route");
        assert!(is_entry(&graph, "server.py::save_item"), "a handler is an entry point");
        assert!(!is_entry(&graph, "server.py::store"), "a plain function is not");
    }

    // Express with a named and an inline handler, and a Next.js route file.
    #[test]
    fn test_route_registrations() {
        let express = "const app = express();\nfunction list(req, res) { res.send(all()); }\nfunction all() { return []; }\napp.get('/api/items', list);\napp.post('/api/items', (req, res) => { all(); });\n";
        let next = "export async function GET() { return Response.json([]); }\nfunction helper() {}\n";
        let client = "def test_items(client):\n    client.get('/api/items')\n";
        let graph = build(&[("server.js", express), ("app/api/orders/route.ts", next), ("tests/test_api.py", client)]);
        assert!(is_entry(&graph, "server.js::list"), "a named handler");
        assert_eq!(caller_keys(&graph, "server.js::all"), ["server.js::list", "server.js::route /api/items"], "the inline handler is a function of its own");
        assert!(is_entry(&graph, "app/api/orders/route.ts::GET") && !is_entry(&graph, "app/api/orders/route.ts::helper"), "Next.js methods only");
        assert_eq!(caller_keys(&graph, "(route)::/api/items"), ["tests/test_api.py::test_items"], "the test client requests the route");
    }

    // Argon's server: a mapping from paths to methods, and an `if path ==` guard whose block calls the handler.
    #[test]
    fn test_route_dispatch() {
        let server = "class Handler:\n    def routes(self):\n        return {\"/api/session\": self.session}\n\n    def session(self):\n        pass\n\n    def do_POST(self):\n        if self.path == \"/api/scan\":\n            self.scan()\n\n    def scan(self):\n        pass\n";
        let test = "def test_scan(me):\n    me.post(\"/api/scan\", {})\n";
        let graph = build(&[("server.py", server), ("test_server.py", test)]);
        assert!(is_entry(&graph, "server.py::Handler.session"), "mapped");
        assert_eq!(
            caller_keys(&graph, "server.py::Handler.scan"),
            ["server.py::Handler.do_POST", "(route)::/api/scan"],
            "guarded, and still called by do_POST"
        );
        assert_eq!(caller_keys(&graph, "(route)::/api/scan"), ["test_server.py::test_scan"], "the test reaches the guarded handler through the route");
    }

    // Argon's tests: `me = self.connected()`, where `connected` returns a local `Visitor`; also an annotated factory, and a module's factory
    // returning another factory's result.
    #[test]
    fn test_returned_objects() {
        let tests = "class Visitor:\n    def post(self, path):\n        pass\n\nclass Case:\n    def connected(self):\n        visitor = Visitor()\n        return visitor\n\n    def test_scan(self):\n        me = self.connected()\n        me.post('/x')\n\ndef make() -> Visitor:\n    return build()\n\ndef use_it():\n    make().post('/y')\n    v = make()\n    v.post('/z')\n";
        let client = "import tests_util\n\ndef remote():\n    c = tests_util.make()\n    c.post('/w')\n";
        let graph = build(&[("tests_util.py", tests), ("client.py", client)]);
        let callers = caller_keys(&graph, "tests_util.py::Visitor.post");
        assert_eq!(callers, ["tests_util.py::Case.test_scan", "tests_util.py::use_it", "client.py::remote"], "through the returned object");
    }

    // A barrel's re-exports: TypeScript `export { login } from './login'` and Python `from .session import start` in `__init__.py`.
    #[test]
    fn test_barrels() {
        let graph = build(&[
            ("src/auth/login.ts", "export function login() {}\n"),
            ("src/auth/index.ts", "export { login } from './login';\n"),
            ("src/app.ts", "import { login } from './auth';\nexport function main() { login(); }\n"),
            ("auth/session.py", "def start():\n    pass\n"),
            ("auth/__init__.py", "from .session import start\n"),
            ("server.py", "import auth\n\ndef serve():\n    auth.start()\n"),
        ]);
        assert_eq!(caller_keys(&graph, "src/auth/login.ts::login"), ["src/app.ts::main"], "through index.ts");
        assert_eq!(caller_keys(&graph, "auth/session.py::start"), ["server.py::serve"], "through __init__.py");
    }

    // A file that does not parse is reported with the line of its first error.
    #[test]
    fn test_syntax_error() {
        let graph = build(&[("ok.py", "def f():\n    pass\n"), ("bad.py", "def f():\n    pass\n\ndef g(:\n    pass\n")]);
        assert_eq!(graph.broken(), [("bad.py".to_string(), 4)], "the first error's line");
    }

    // Helpers come before the functions that call them, recursion stays together, and unknown keys go last.
    #[test]
    fn test_helpers_first() {
        let source =
            "def top():\n    mid()\n\ndef mid():\n    leaf()\n    ping()\n\ndef leaf():\n    pass\n\ndef ping():\n    pong()\n\ndef pong():\n    ping()\n";
        let graph = build(&[("a.py", source)]);
        let keys: Vec<String> = ["a.py::top", "missing.py::x", "a.py::mid", "a.py::leaf", "a.py::ping"].map(String::from).to_vec();
        let order = graph.helpers_first(&keys);
        let at = |key: &str| order.iter().position(|found| found == key).unwrap();
        assert!(at("a.py::leaf") < at("a.py::mid") && at("a.py::mid") < at("a.py::top"), "leaf, mid, top: {order:?}");
        assert!(at("a.py::ping") < at("a.py::mid"), "the recursive pair before its caller: {order:?}");
        assert_eq!(order.last().map(String::as_str), Some("missing.py::x"), "unknown last");
    }

    // Test files are marked by the scorer's patterns.
    #[test]
    fn test_marks_tests() {
        let graph = build(&[("app.py", "def f():\n    pass\n"), ("tests/test_app.py", "from app import f\n\ndef test_f():\n    f()\n")]);
        let caller = graph.callers(graph.find("app.py::f").unwrap());
        assert_eq!(caller.len(), 1, "the test calls f");
        assert!(caller.first().and_then(|(id, _)| graph.function(*id)).is_some_and(|function| function.is_test), "and is marked as a test");
    }

    // A line belongs to the innermost function holding it, else to the file's top-level code.
    #[test]
    fn test_innermost() {
        let graph = graph();
        let key = |line| graph.innermost("billing.py", line).and_then(|id| graph.function(id)).map(Function::key);
        assert_eq!(key(9).as_deref(), Some("billing.py::Ledger.record"));
        assert_eq!(key(4).as_deref(), Some("billing.py::(top level)"), "the class line is not in a function");
        assert_eq!(graph.innermost("missing.py", 1), None, "not a scanned file");
    }
}
