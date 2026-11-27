//! Tying one call to the function it reaches, on evidence only; the order of evidence is in the `callgraph` module's documentation.

use std::collections::{HashMap, HashSet};

use super::extract::{Callee, Definition, Holder};
use super::holders::{ClassRef, Returned};
use super::Parsed;

/// Where a call leads.
pub enum Found {
    Exact(Vec<usize>),
    ByName(usize),
    Nowhere,
}

/// `(file, name)`.
type InFile = (String, String);
/// `(file, class, method)`.
type InClass = (String, String, String);
/// A method's file and id.
type Located = (String, usize);
/// The class each object of a file holds; `None` when the code gives it two different classes, so neither is trusted.
pub type Held = HashMap<Holder, Option<ClassRef>>;

/// Where a call is made: the file, the function making it (`None` at the top level) and that function's class, and the file's objects.
pub struct Site<'a> {
    pub file: &'a Parsed,
    pub caller: Option<usize>,
    pub class: Option<&'a str>,
    pub held: &'a Held,
}

/// How many functions deep a returned object is followed: `a()` returns what `b()` returns, which returns a `Client`.
const RETURN_DEPTH: usize = 3;

/// What resolving a returned object needs to know about a function.
struct Facts {
    file: String,
    /// Its index among its file's definitions, which is how its locals are kept.
    number: usize,
    class: Option<String>,
    returns: Vec<Returned>,
}

/// Each object's class; an object the code gives two different classes is not trusted.
#[must_use]
pub fn held_map(held: &[(Holder, ClassRef)]) -> Held {
    let mut map = Held::new();
    for (holder, class) in held {
        let known = map.entry(holder.clone()).or_insert_with(|| Some(class.clone()));
        if known.as_ref() != Some(class) {
            *known = None;
        }
    }
    map
}

/// How many barrel files deep re-exports are followed.
const MAX_BARRELS: usize = 3;

/// A file that gathers and re-exports others: `index.*` or `__init__.py`.
fn is_barrel(path: &str) -> bool {
    let file = path.rsplit('/').next().unwrap_or(path);
    file.starts_with("index.") || file == "__init__.py"
}

/// What creating an object runs.
const INITS: [&str; 2] = ["__init__", "constructor"];
/// How many classes up a method is looked for through base classes.
const MAX_BASES: usize = 32;

/// The name a module is used by in code: `billing` for `src/billing.py`, `orders` for `orders/__init__.py` or `orders/index.ts`.
#[must_use]
pub fn module_name(path: &str) -> &str {
    let mut parts = path.rsplit('/');
    let file = parts.next().unwrap_or(path);
    let stem = file.rsplit_once('.').map_or(file, |(stem, _)| stem);
    if stem == "__init__" || stem == "index" {
        parts.next().unwrap_or(stem)
    } else {
        stem
    }
}

/// The method name of a call made on something (`x.get()`), which is what a missed link would be: plain calls and unnamed ones have none.
#[must_use]
pub fn method_name(callee: &Callee) -> Option<&str> {
    match callee {
        Callee::OnSelf(name) | Callee::OnField(_, name) | Callee::Qualified(_, name) | Callee::Method(name) => Some(name),
        Callee::Plain(_) | Callee::Unnamed => None,
    }
}

/// The name a call uses, whatever its form; `None` for a call with no name.
#[must_use]
pub fn callee_name(callee: &Callee) -> Option<&str> {
    match callee {
        Callee::Plain(name) => Some(name),
        Callee::Unnamed => None,
        _ => method_name(callee),
    }
}

fn one(id: usize) -> Found {
    Found::Exact(vec![id])
}

fn exact(ids: Vec<usize>) -> Option<Found> {
    (!ids.is_empty()).then_some(Found::Exact(ids))
}

/// The lookups that tie a call to a function.
#[derive(Default)]
pub struct Index {
    /// Functions that are not methods, by file and name.
    plain: HashMap<InFile, Vec<usize>>,
    methods: HashMap<InClass, usize>,
    /// Every method by name alone, with its file.
    methods_named: HashMap<String, Vec<Located>>,
    /// Every class, by file and name, with the classes it extends.
    bases: HashMap<InFile, Vec<ClassRef>>,
    /// Each file's imports, resolved to files.
    imports: HashMap<String, Vec<String>>,
    /// Each file's objects whose class the code states.
    held: HashMap<String, Held>,
    /// What each function returns, by id.
    functions: HashMap<usize, Facts>,
}

impl Index {
    /// Note a file's classes and imports.
    pub fn add_file(&mut self, file: &Parsed) {
        self.imports.insert(file.path.clone(), file.imports.clone());
        self.held.insert(file.path.clone(), held_map(&file.calls.held));
        for class in &file.calls.classes {
            self.bases.insert((file.path.clone(), class.name.clone()), class.bases.clone());
        }
    }

    /// A file's objects whose class the code states.
    #[must_use]
    pub fn held_of(&self, path: &str) -> Option<&Held> {
        self.held.get(path)
    }

    /// Note one function or method under its id; `number` is its index among its file's definitions.
    pub fn add(&mut self, file: &str, (number, definition): (usize, &Definition), id: usize) {
        let (class, returns) = (definition.class.clone(), definition.returns.clone());
        self.functions.insert(id, Facts { file: file.to_string(), number, class, returns });
        let short = definition.short.clone();
        if let Some(class) = &definition.class {
            self.methods.insert((file.to_string(), class.clone(), short.clone()), id);
            self.methods_named.entry(short).or_default().push((file.to_string(), id));
        } else {
            self.plain.entry((file.to_string(), short)).or_default().push(id);
        }
    }

    /// The files a name in `path` can reach: its own first, then the ones it imports.
    fn visible<'a>(&'a self, path: &'a str) -> std::vec::IntoIter<&'a str> {
        let mut files = vec![path];
        files.extend(self.through_barrels(self.imports.get(path).into_iter().flatten().map(String::as_str).collect()));
        files.into_iter()
    }

    /// Imported files, and through each barrel among them (`index.ts`, `__init__.py`) the files it re-exports, up to [`MAX_BARRELS`] deep:
    /// `import { login } from './auth'` reaches `auth/login.ts` when `auth/index.ts` says `export { login } from './login'`.
    fn through_barrels<'a>(&'a self, imported: Vec<&'a str>) -> Vec<&'a str> {
        let (mut files, mut frontier) = (Vec::new(), imported);
        for _ in 0..=MAX_BARRELS {
            let fresh: Vec<&str> = frontier.into_iter().filter(|file| !files.contains(file)).collect();
            files.extend(fresh.iter().copied());
            frontier =
                fresh.into_iter().filter(|file| is_barrel(file)).flat_map(|file| self.imports.get(file).into_iter().flatten().map(String::as_str)).collect();
        }
        files
    }

    /// The files `path` imports under the module name `module`.
    fn modules<'a>(&'a self, path: &str, module: &'a str) -> std::vec::IntoIter<&'a str> {
        let named: Vec<&str> = self.imports.get(path).into_iter().flatten().map(String::as_str).filter(|file| module_name(file) == module).collect();
        self.through_barrels(named).into_iter()
    }

    /// The classes a name means in `path`: `Sessions` in the file or a file it imports, `auth.Sessions` in the module imported as `auth`.
    fn locate(&self, path: &str, class: &ClassRef) -> Vec<InFile> {
        let files: Vec<&str> = class.module.as_deref().map_or_else(|| self.visible(path).collect(), |module| self.modules(path, module).collect());
        let key = |file: &str| (file.to_string(), class.name.clone());
        files.into_iter().map(key).filter(|found| self.bases.contains_key(found)).collect()
    }

    /// A class's method, else one of a class it extends.
    fn method_of(&self, start: InFile, name: &str) -> Option<usize> {
        let (mut queue, mut seen) = (vec![start], HashSet::new());
        while let Some((file, class)) = queue.pop() {
            if let Some(id) = self.methods.get(&(file.clone(), class.clone(), name.to_string())) {
                return Some(*id);
            }
            if seen.len() >= MAX_BASES || !seen.insert((file.clone(), class.clone())) {
                continue;
            }
            let bases = self.bases.get(&(file.clone(), class)).into_iter().flatten();
            queue.extend(bases.flat_map(|base| self.locate(&file, base)));
        }
        None
    }

    /// A method of the class a name means in `path`, or of the class the function it names returns (`self.connected().post()`).
    fn method_of_ref(&self, (path, own): (&str, Option<&str>), class: &ClassRef, name: &str) -> Option<usize> {
        self.classes_of((path, own), class, RETURN_DEPTH).into_iter().find_map(|found| self.method_of(found, name))
    }

    /// The classes a reference means: a class by that name, else the classes the function by that name returns. `own` is the class of the
    /// code making the reference, for `self.make()`.
    fn classes_of(&self, (path, own): (&str, Option<&str>), class: &ClassRef, depth: usize) -> Vec<InFile> {
        let found = self.locate(path, class);
        if !found.is_empty() || depth == 0 {
            return found;
        }
        let made_by = self.producers((path, own), class);
        made_by.into_iter().flat_map(|id| self.returned_classes(id, depth.saturating_sub(1))).collect()
    }

    /// The functions a call written like `class` runs: `make()`, `self.make()`, `module.make()`.
    fn producers(&self, (path, own): (&str, Option<&str>), class: &ClassRef) -> Vec<usize> {
        match class.module.as_deref() {
            None => self.visible(path).find_map(|file| self.plain.get(&(file.to_string(), class.name.clone()))).cloned().unwrap_or_default(),
            Some("self" | "this" | "cls") => own.and_then(|own| self.method_of((path.to_string(), own.to_string()), &class.name)).into_iter().collect(),
            Some(module) => self.modules(path, module).filter_map(|file| self.plain.get(&(file.to_string(), class.name.clone()))).flatten().copied().collect(),
        }
    }

    /// The classes a function returns, as its own file means them.
    fn returned_classes(&self, id: usize, depth: usize) -> Vec<InFile> {
        let Some(facts) = self.functions.get(&id) else { return Vec::new() };
        let place = (facts.file.as_str(), facts.class.as_deref());
        let local = |name: &String| {
            let holder = Holder::Local { function: facts.number, name: name.clone() };
            self.held.get(&facts.file).and_then(|held| held.get(&holder)).cloned().flatten()
        };
        let classes = facts.returns.iter().filter_map(|value| match value {
            Returned::Class(class) => Some(class.clone()),
            Returned::Name(name) => local(name),
        });
        classes.flat_map(|class| self.classes_of(place, &class, depth)).collect()
    }

    /// Where `callee`, called at `site`, leads.
    pub fn resolve(&self, site: &Site<'_>, callee: &Callee) -> Found {
        let path = site.file.path.as_str();
        match callee {
            Callee::OnSelf(name) => {
                let own = site.class.and_then(|class| self.method_of((path.to_string(), class.to_string()), name));
                own.map_or_else(|| self.by_name(path, name), one)
            }
            Callee::OnField(field, name) => self.field_call(site, field, name),
            Callee::Plain(name) => self.plain_call(path, name),
            Callee::Qualified(object, name) => self.qualified_call(site, object, name),
            Callee::Method(name) => self.by_name(path, name),
            Callee::Unnamed => Found::Nowhere,
        }
    }

    /// `self.store.get()`: the method of the class the field holds.
    fn field_call(&self, site: &Site<'_>, field: &str, name: &str) -> Found {
        let holder = site.class.map(|class| Holder::Field { class: class.to_string(), field: field.to_string() });
        let class = holder.and_then(|holder| site.held.get(&holder).cloned().flatten());
        let found = class.and_then(|class| self.method_of_ref((&site.file.path, site.class), &class, name));
        found.map_or_else(|| self.by_name(&site.file.path, name), one)
    }

    /// The class the object named `object` holds where the call is made: a local of the calling function, else a top-level name.
    fn held_class(site: &Site<'_>, object: &str) -> Option<ClassRef> {
        let local = site.caller.map(|function| Holder::Local { function, name: object.to_string() });
        let found = local.and_then(|holder| site.held.get(&holder).cloned().flatten());
        found.or_else(|| site.held.get(&Holder::Global(object.to_string())).cloned().flatten())
    }

    /// The file's own function of that name, else one from a file it imports, else the class of that name being created.
    fn plain_call(&self, path: &str, name: &str) -> Found {
        let own = self.visible(path).find_map(|file| self.plain.get(&(file.to_string(), name.to_string())));
        own.map_or_else(|| self.constructor(path, &ClassRef { module: None, name: name.to_string() }), |ids| Found::Exact(ids.clone()))
    }

    /// Creating an object runs its class's `__init__` or `constructor`.
    fn constructor(&self, path: &str, class: &ClassRef) -> Found {
        let inits = self.locate(path, class).into_iter().filter_map(|found| INITS.iter().find_map(|init| self.method_of(found.clone(), init)));
        exact(inits.collect()).unwrap_or(Found::Nowhere)
    }

    /// `store.get()` on an object whose class is stated; `billing.charge()` in a module; `api.Client()` creating a module's class;
    /// `Client.create()` on a class; else by the method's name alone.
    fn qualified_call(&self, site: &Site<'_>, object: &str, name: &str) -> Found {
        let path = site.file.path.as_str();
        if let Some(id) = Self::held_class(site, object).and_then(|class| self.method_of_ref((path, site.class), &class, name)) {
            return one(id);
        }
        let functions: Vec<usize> =
            self.modules(path, object).filter_map(|file| self.plain.get(&(file.to_string(), name.to_string()))).flatten().copied().collect();
        if let Some(found) = exact(functions) {
            return found;
        }
        if let Found::Exact(ids) = self.constructor(path, &ClassRef { module: Some(object.to_string()), name: name.to_string() }) {
            return Found::Exact(ids);
        }
        let on_class = self.method_of_ref((path, site.class), &ClassRef { module: None, name: object.to_string() }, name);
        on_class.map_or_else(|| self.by_name(path, name), one)
    }

    /// A method on an object of unknown class: tied only when exactly one method of that name sits in the caller's own file or a file
    /// it imports. Even then it is a guess (`dict.get()` is not `Sessions.get()`), so the link is marked as resting on the name alone.
    fn by_name(&self, path: &str, name: &str) -> Found {
        let visible: HashSet<&str> = self.visible(path).collect();
        let mut candidates = self.methods_named.get(name).into_iter().flatten().filter(|(file, _)| visible.contains(file.as_str()));
        match (candidates.next(), candidates.next()) {
            (Some((_, only)), None) => Found::ByName(*only),
            _ => Found::Nowhere,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // The name a module is used by.
    #[test]
    fn test_module_name() {
        assert_eq!(module_name("src/billing.py"), "billing");
        assert_eq!(module_name("orders/__init__.py"), "orders");
        assert_eq!(module_name("web/cart/index.ts"), "cart");
    }
}
