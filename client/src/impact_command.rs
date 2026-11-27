//! `krypton impact [REPO] [--base REF] [--function FILE::NAME]... [--depth N] [--json]`: what a change reaches downstream.
//!
//! It reads every source file of the repository on this machine, builds the call graph (see `callgraph`), finds the functions a change
//! touched (lines added, changed or deleted since `--base`, `HEAD` by default, which covers uncommitted work; or the functions named with
//! `--function`), and lists every function that calls them, directly or through others, up to `--depth` steps (3 by default). Nothing is
//! sent to the server and nothing is scored: it is a map for the person or agent making the change, so they can see what else to check.
//!
//! The last line says how many calls the graph could follow. Calls into other libraries, built-ins and calls decided at run time are not
//! followed. A link that rests on a method's name alone (`order.charge()`, when only one `charge` method is in sight) is a guess, so it is
//! left out unless `--by-name` asks for it, and then marked `~`. A function the change deleted is not in the code any more, so it
//! is not shown; its callers are where the change touched them, if it did.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use krypton_client::Lang;

use serde_json::{json, Value};

use crate::callgraph::{compare, from_sources, Change, Changes, Function, Graph};
use crate::diff::touched_since;
use crate::Failure;

const USAGE: &str = "usage: krypton impact [REPO] [--base REF] [--function FILE::NAME]... [--depth 1-10] [--by-name] [--json]";
const DEFAULT_DEPTH: usize = 3;
const MAX_DEPTH: usize = 10;
/// The most callers listed under one changed function in the text view; `--json` has them all.
const MAX_LISTED: usize = 40;
/// The most names listed on one summary line.
const MAX_NAMED: usize = 20;

struct Options {
    repo: PathBuf,
    base: String,
    functions: Vec<String>,
    depth: usize,
    /// Also follow links that rest on a method's name alone.
    by_name: bool,
    json: bool,
}

fn usage(message: &str) -> Failure {
    Failure::Usage(format!("{message}\n{USAGE}"))
}

fn depth_of(text: &str) -> Result<usize, Failure> {
    text.parse().ok().filter(|depth| (1..=MAX_DEPTH).contains(depth)).ok_or_else(|| usage("--depth is a number from 1 to 10"))
}

fn parse_options(args: &[String]) -> Result<Options, Failure> {
    let mut options = Options { repo: PathBuf::from("."), base: "HEAD".to_string(), functions: Vec::new(), depth: DEFAULT_DEPTH, by_name: false, json: false };
    let (mut repo, mut args) = (None, args.iter());
    while let Some(arg) = args.next() {
        let mut value = |flag: &str| args.next().cloned().ok_or_else(|| usage(&format!("{flag} needs a value")));
        match arg.as_str() {
            "--base" => options.base = value("--base")?,
            "--function" => options.functions.push(value("--function")?.replace('\\', "/")),
            "--depth" => options.depth = depth_of(&value("--depth")?)?,
            "--by-name" => options.by_name = true,
            "--json" => options.json = true,
            flag if flag.starts_with('-') => return Err(usage(&format!("unknown flag {flag}"))),
            path if repo.is_none() => repo = Some(PathBuf::from(path)),
            _ => return Err(usage("give one repository folder")),
        }
    }
    options.repo = repo.unwrap_or(options.repo);
    Ok(options)
}

/// The call graph of a repository: every source file read and parsed on this machine.
///
/// # Errors
///
/// [`Failure::Usage`] when the folder has no source files; [`Failure::Read`] or [`Failure::Walk`] when a file cannot be read or parsed.
pub fn load_graph(repo: &Path) -> Result<Graph, Failure> {
    let files = crate::scan_command::source_files(repo)?;
    if files.is_empty() {
        return Err(usage(&format!("no Python, JavaScript or TypeScript files under {}", repo.display())));
    }
    build(repo, &files)
}

/// Read every source file and build the call graph.
fn build(repo: &Path, files: &[String]) -> Result<Graph, Failure> {
    let mut sources = Vec::with_capacity(files.len());
    for path in files {
        let bytes = fs::read(repo.join(path)).map_err(|error| Failure::Read(path.clone(), error))?;
        sources.push((path.clone(), String::from_utf8_lossy(&bytes).into_owned()));
    }
    from_sources(&sources).map_err(Failure::Walk)
}

/// The functions the change touched, or the ones named with `--function`.
/// The functions a change touched, by id, and the files it touched.
type Touched = (BTreeSet<usize>, Vec<String>);

/// The functions the change touched, or the ones named with `--function`; and the source files the change touched (none for names).
fn changed(options: &Options, graph: &Graph, files: &[String]) -> Result<Touched, Failure> {
    if !options.functions.is_empty() {
        let find = |key: &String| {
            graph.find(key).ok_or_else(|| usage(&format!("no function {key}: name it as FILE::NAME, e.g. billing.py::charge or api.py::Client.connect")))
        };
        return Ok((options.functions.iter().map(find).collect::<Result<_, _>>()?, Vec::new()));
    }
    let touched = touched_since(&options.base, &options.repo, files)?;
    // a change that does not parse is fixed first: its calls cannot be read, so anything said about it would be guesswork
    if let Some((path, line)) = graph.broken().iter().find(|(path, _)| touched.contains_key(path)) {
        return Err(Failure::Walk(krypton_client::WalkError::Syntax(path.clone(), *line)));
    }
    let ids = touched.iter().flat_map(|(file, lines)| lines.iter().filter_map(|line| graph.innermost(file, *line))).collect();
    Ok((ids, touched.into_keys().collect()))
}

/// The touched files as they were at the base, as a graph of their own (a file the change added is not there).
fn base_graph(options: &Options, touched: &[String]) -> Result<Graph, Failure> {
    let mut sources = Vec::new();
    for path in touched.iter().filter(|path| Lang::from_path(path).is_ok()) {
        let shown = Command::new("git").arg("-C").arg(&options.repo).arg("show").arg(format!("{}:./{path}", options.base)).output();
        let done = shown.map_err(|error| Failure::Log(format!("cannot run git: {error}")))?;
        if done.status.success() {
            sources.push((path.clone(), String::from_utf8_lossy(&done.stdout).into_owned()));
        }
    }
    from_sources(&sources).map_err(Failure::Walk)
}

/// A function reached from a changed one: how many steps up, the function it calls on the way, and whether that link is by name alone.
struct Reached {
    id: usize,
    depth: usize,
    via: usize,
    by_name: bool,
}

/// The callers of everything in `frontier` not seen yet, one step further up; links by name alone only when `by_name` is on.
fn step(graph: &Graph, frontier: &[usize], seen: &mut BTreeSet<usize>, depth: usize, by_name: bool) -> Vec<Reached> {
    let links = frontier.iter().flat_map(|&via| graph.callers(via).into_iter().map(move |(id, named)| Reached { id, depth, via, by_name: named }));
    links.filter(|reached| by_name || !reached.by_name).filter(|reached| seen.insert(reached.id)).collect()
}

/// Every function upstream of `start` within `depth` steps, nearest first, each listed once.
fn reach(graph: &Graph, start: usize, depth: usize, by_name: bool) -> Vec<Reached> {
    let mut seen = BTreeSet::from([start]);
    let (mut found, mut frontier) = (Vec::new(), vec![start]);
    for level in 1..=depth {
        let next = step(graph, &frontier, &mut seen, level, by_name);
        if next.is_empty() {
            break;
        }
        frontier = next.iter().map(|reached| reached.id).collect();
        found.extend(next);
    }
    found
}

/// What one changed function reaches, and the kind of change it had (none when asked about by name).
struct Report {
    id: usize,
    reached: Vec<Reached>,
    kind: Option<Change>,
}

fn label(graph: &Graph, id: usize) -> String {
    let named = |function: &Function| if function.is_route { format!("route {}", function.name) } else { format!("{} · {}", function.file, function.name) };
    graph.function(id).map_or_else(|| format!("#{id}"), named)
}

fn file_of(graph: &Graph, id: usize) -> Option<&str> {
    graph.function(id).map(|function| function.file.as_str())
}

fn is_top_level(graph: &Graph, id: usize) -> bool {
    graph.function(id).is_some_and(Function::is_top_level)
}

/// In a test file (the scorer's test patterns): tests are counted apart, never listed as code that depends on the change.
fn is_test(graph: &Graph, id: usize) -> bool {
    graph.function(id).is_some_and(|function| function.is_test)
}

fn is_route(graph: &Graph, id: usize) -> bool {
    graph.function(id).is_some_and(|function| function.is_route)
}

/// Code, as opposed to tests, top-level lines and route nodes.
fn is_code_function(graph: &Graph, id: usize) -> bool {
    !is_test(graph, id) && !is_top_level(graph, id) && !is_route(graph, id)
}

/// The tree under one changed function, each caller indented under the function it calls.
fn tree_lines(graph: &Graph, report: &Report, changed: &BTreeSet<usize>, word: &str) -> Vec<String> {
    let mut children: BTreeMap<usize, Vec<&Reached>> = BTreeMap::new();
    for reached in &report.reached {
        children.entry(reached.via).or_default().push(reached);
    }
    let mut lines = Vec::new();
    let mut stack: Vec<&Reached> = children.get(&report.id).into_iter().flatten().rev().copied().collect();
    while let Some(reached) = stack.pop() {
        if is_test(graph, reached.id) {
            continue;
        }
        let mark = match (reached.by_name, changed.contains(&reached.id)) {
            (true, true) => format!("  ~  ({word})"),
            (true, false) => "  ~".to_string(),
            (false, true) => format!("  ({word})"),
            (false, false) => String::new(),
        };
        lines.push(format!("{}← {}{mark}", "  ".repeat(reached.depth), label(graph, reached.id)));
        // a changed caller has its own entry with its own callers, so they are not repeated here
        if !changed.contains(&reached.id) {
            stack.extend(children.get(&reached.id).into_iter().flatten().rev().copied());
        }
    }
    lines
}

/// One changed (or asked-about) function and the tree of its callers; `word` is how such functions are marked.
fn section(graph: &Graph, report: &Report, changed: &BTreeSet<usize>, word: &str) -> Vec<String> {
    let kind = report.kind.as_ref().map_or_else(|| word.to_string(), Change::word);
    let mut lines = vec![format!("{}  ({kind})", label(graph, report.id))];
    let tree = tree_lines(graph, report, changed, word);
    lines.extend(tree.iter().take(MAX_LISTED).cloned());
    if tree.len() > MAX_LISTED {
        lines.push(format!("  …and {} more (krypton impact --json lists them all)", tree.len().saturating_sub(MAX_LISTED)));
    }
    let tests = report.reached.iter().filter(|reached| is_test(graph, reached.id)).count();
    if tests > 0 {
        lines.push(format!("  + {} reach it", plural(tests, "test", "tests")));
    }
    lines
}

fn plural(count: usize, one: &str, many: &str) -> String {
    format!("{count} {}", if count == 1 { one } else { many })
}

/// Names joined by commas, at most [`MAX_NAMED`] of them.
fn listed(names: &[String]) -> String {
    let shown = names.iter().take(MAX_NAMED).cloned().collect::<Vec<_>>().join(", ");
    if names.len() > MAX_NAMED {
        format!("{shown} and {} more", names.len().saturating_sub(MAX_NAMED))
    } else {
        shown
    }
}

/// The opening lines: what changed, and how far it reaches.
fn header(graph: &Graph, options: &Options, reports: &[Report], changed: &BTreeSet<usize>) -> Vec<String> {
    let changed_files: BTreeSet<&str> = changed.iter().filter_map(|id| file_of(graph, *id)).collect();
    let what = plural(changed.iter().filter(|id| is_code_function(graph, **id)).count(), "function", "functions");
    let first = if options.functions.is_empty() {
        format!("Changed since {}: {what} in {}{}.", options.base, plural(changed_files.len(), "file", "files"), kinds_summary(graph, reports))
    } else {
        format!("Asked about: {what}.")
    };
    let reached = reports.iter().flat_map(|report| report.reached.iter().map(|reached| reached.id));
    let downstream: BTreeSet<usize> = reached.filter(|id| !changed.contains(id) && !is_test(graph, *id) && !is_route(graph, *id)).collect();
    let files: BTreeSet<&str> = downstream.iter().filter_map(|id| file_of(graph, *id)).collect();
    let others = files.iter().filter(|file| !changed_files.contains(*file)).count();
    // only a diff has files the change did not touch
    let untouched = if options.functions.is_empty() { format!(" ({others} not touched by the change)") } else { String::new() };
    let second = format!(
        "Downstream: {} in {}{untouched} call them, directly or through others, up to {} up.",
        plural(downstream.len(), "function", "functions"),
        plural(files.len(), "file", "files"),
        plural(options.depth, "step", "steps")
    );
    vec![first, second]
}

/// The changed functions with no callers. Where some calls to a method of the same name could not be tied to a class, there may be callers
/// the graph cannot see, so those say so rather than claim nothing calls them.
fn leaf_lines(graph: &Graph, reports: &[Report]) -> Vec<String> {
    let leaves = reports.iter().filter(|report| report.reached.is_empty() && is_code_function(graph, report.id)).map(|report| report.id);
    let unsure_calls = |id: usize| graph.function(id).map_or(0, |function| graph.uncertain_calls(function.short()));
    let (unsure, none): (Vec<usize>, Vec<usize>) = leaves.partition(|id| unsure_calls(*id) > 0);
    let mut lines = Vec::new();
    if !none.is_empty() {
        lines.push(format!("No calls found to: {}.", listed(&none.iter().map(|id| label(graph, *id)).collect::<Vec<_>>())));
    }
    if !unsure.is_empty() {
        let described = |id: &usize| {
            let short = graph.function(*id).map_or("", Function::short);
            format!("{} ({} to a .{short}() on an object of unknown class)", label(graph, *id), plural(unsure_calls(*id), "call", "calls"))
        };
        lines.push(format!("No callers found for certain: {}.", listed(&unsure.iter().map(described).collect::<Vec<_>>())));
    }
    lines
}

/// How many functions (not tests) had each kind of change: ` (2 new, 1 logic changed)`; empty when no kinds are known.
fn kinds_summary(graph: &Graph, reports: &[Report]) -> String {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for kind in reports.iter().filter(|report| is_code_function(graph, report.id)).filter_map(|report| report.kind.as_ref()) {
        let word = match kind {
            Change::Renamed(_) => "renamed".to_string(),
            Change::Moved(_) => "moved".to_string(),
            other => other.word(),
        };
        let count = counts.entry(word).or_default();
        *count = count.saturating_add(1);
    }
    let parts: Vec<String> = counts.iter().map(|(word, count)| format!("{count} {word}")).collect();
    if parts.is_empty() {
        String::new()
    } else {
        format!(" ({})", parts.join(", "))
    }
}

/// The changed functions whose code is the same apart from comments and layout, and the functions the change deleted, with the calls by
/// their name that now lead nowhere.
fn cosmetic_and_deleted(graph: &Graph, reports: &[Report], deleted: &[String]) -> Vec<String> {
    let mut lines = Vec::new();
    let cosmetic: Vec<String> = reports.iter().filter(|report| report.kind == Some(Change::Cosmetic)).map(|report| label(graph, report.id)).collect();
    if !cosmetic.is_empty() {
        lines.push(format!("Only comments or layout changed, so nothing downstream is affected: {}.", listed(&cosmetic)));
    }
    let gone: Vec<String> = deleted.iter().map(|key| deleted_note(graph, key)).collect();
    if !gone.is_empty() {
        lines.push(format!("Deleted: {}.", listed(&gone)));
    }
    lines
}

/// A deleted function, and how many calls by its name are left that reach no function of the repository.
fn deleted_note(graph: &Graph, key: &str) -> String {
    let (file, name) = key.split_once("::").unwrap_or((key, ""));
    let short = name.rsplit('.').next().unwrap_or(name);
    match graph.unresolved_calls(short) {
        0 => format!("{file} · {name}"),
        left => format!("{file} · {name} ({} to {short}() still lead nowhere)", plural(left, "call", "calls")),
    }
}

/// The closing lines: changed functions with no callers, changed tests, and files changed outside any function.
fn footer(graph: &Graph, reports: &[Report], deleted: &[String]) -> Vec<String> {
    let mut lines = cosmetic_and_deleted(graph, reports, deleted);
    lines.extend(leaf_lines(graph, reports));
    let tests: Vec<&str> =
        reports.iter().filter(|report| is_test(graph, report.id) && !is_top_level(graph, report.id)).filter_map(|report| file_of(graph, report.id)).collect();
    let test_files: BTreeSet<&str> = tests.iter().copied().collect();
    if !tests.is_empty() {
        lines.push(format!("Tests changed: {} in {}.", plural(tests.len(), "test function", "test functions"), plural(test_files.len(), "file", "files")));
    }
    let top: Vec<String> =
        reports.iter().filter(|report| is_top_level(graph, report.id)).filter_map(|report| file_of(graph, report.id)).map(str::to_string).collect();
    if !top.is_empty() {
        lines.push(format!("Also changed outside any function (imports, constants, top-level code): {}.", listed(&top)));
    }
    lines
}

/// What the graph could and could not follow. Most calls in any codebase go to other libraries and built-ins, which are not this
/// repository's functions, so the count is given as it is rather than as a share.
fn coverage_line(graph: &Graph, by_name: bool) -> String {
    let coverage = graph.coverage();
    let exact = coverage.resolved.saturating_sub(coverage.by_name);
    let rest = "The rest go to other libraries and built-ins, or to code chosen at run time, and are not followed.";
    if by_name {
        return format!(
            "Calls tied to this repository's functions: {} of {}, {} of them by a method's name alone (~). {rest}",
            coverage.resolved, coverage.calls, coverage.by_name
        );
    }
    format!(
        "Calls tied to this repository's functions: {exact} of {}. {rest} {} more match only a method's name; --by-name follows them too.",
        coverage.calls, coverage.by_name
    )
}

fn text(graph: &Graph, options: &Options, reports: &[Report], changed: &BTreeSet<usize>, deleted: &[String]) -> String {
    if changed.is_empty() {
        return format!(
            "No source changes since {}. Compare with another ref (--base main), or name a function (--function FILE::NAME).\n{}\n",
            options.base,
            coverage_line(graph, options.by_name)
        );
    }
    let mut lines = header(graph, options, reports, changed);
    if !graph.broken().is_empty() {
        let where_: Vec<String> = graph.broken().iter().map(|(path, line)| format!("{path}:{line}")).collect();
        lines.push(format!("Not fully parsed (syntax errors), so calls in them may be missing: {}.", listed(&where_)));
    }
    // a cosmetic change reaches nothing, so it is listed at the end rather than given a tree
    let shown = reports.iter().filter(|report| !report.reached.is_empty() && !is_test(graph, report.id) && report.kind != Some(Change::Cosmetic));
    for report in shown {
        lines.push(String::new());
        lines.extend(section(graph, report, changed, if options.functions.is_empty() { "changed" } else { "asked about" }));
    }
    lines.push(String::new());
    lines.extend(footer(graph, reports, deleted));
    lines.push(coverage_line(graph, options.by_name));
    lines.join("\n") + "\n"
}

fn json_report(graph: &Graph, report: &Report) -> Value {
    let key = |id: usize| graph.function(id).map(Function::key);
    let callers: Vec<Value> = report
        .reached
        .iter()
        .map(|reached| {
            json!({ "function": key(reached.id), "depth": reached.depth, "calls": key(reached.via), "by_name_only": reached.by_name, "test": is_test(graph, reached.id) })
        })
        .collect();
    json!({ "function": key(report.id), "change": report.kind.as_ref().map(Change::word), "callers": callers })
}

fn json_text(graph: &Graph, options: &Options, reports: &[Report], deleted: &[String]) -> String {
    let coverage = graph.coverage();
    let base = options.functions.is_empty().then_some(options.base.as_str());
    let body = json!({
        "compared_with": base,
        "depth": options.depth,
        "by_name_included": options.by_name,
        "syntax_errors": graph.broken().iter().map(|(path, line)| json!({ "file": path, "line": line })).collect::<Vec<_>>(),
        "changed": reports.iter().map(|report| json_report(graph, report)).collect::<Vec<_>>(),
        "deleted": deleted.iter().map(|key| json!({ "function": key, "note": deleted_note(graph, key) })).collect::<Vec<_>>(),
        "calls": { "found": coverage.calls, "followed": coverage.resolved.saturating_sub(coverage.by_name), "by_name_only": coverage.by_name },
    });
    format!("{body:#}\n")
}

/// Run `krypton impact`.
///
/// # Errors
///
/// [`Failure::Usage`] for bad flags, a function that is not there, a folder with no source files, or a ref git cannot diff against;
/// [`Failure::Read`] or [`Failure::Walk`] when a file cannot be read or parsed.
pub fn run(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    let files = crate::scan_command::source_files(&options.repo)?;
    if files.is_empty() {
        return Err(usage(&format!("no Python, JavaScript or TypeScript files under {}", options.repo.display())));
    }
    let graph = build(&options.repo, &files)?;
    let (changed, touched) = changed(&options, &graph, &files)?;
    let diffed = if options.functions.is_empty() { compare(&graph, &base_graph(&options, &touched)?, &changed) } else { Changes::default() };
    let report = |id: usize| Report { id, reached: reach(&graph, id, options.depth, options.by_name), kind: diffed.kinds.get(&id).cloned() };
    let reports: Vec<Report> = changed.iter().copied().map(report).collect();
    let output =
        if options.json { json_text(&graph, &options, &reports, &diffed.deleted) } else { text(&graph, &options, &reports, &changed, &diffed.deleted) };
    print!("{output}");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const FILES: [(&str, &str); 4] = [
        ("billing.py", "def charge(amount):\n    return amount\n"),
        ("checkout.py", "from billing import charge\n\ndef checkout(cart):\n    charge(cart.total)\n"),
        ("api.py", "import checkout\n\ndef place_order(cart):\n    checkout.checkout(cart)\n\ndef refund(cart):\n    checkout.checkout(cart)\n"),
        ("tests/test_billing.py", "from billing import charge\n\ndef test_charge():\n    assert charge(1) == 1\n"),
    ];

    // Tests that reach a function are counted under it, never listed as code that depends on it or counted downstream.
    #[test]
    fn test_tests_apart() {
        let graph = graph();
        let out = render(&graph, &options(&["billing.py::charge"], 3));
        assert!(out.contains("    ← api.py · refund\n  + 1 test reach it\n"), "counted under the tree: {out}");
        assert!(!out.contains("test_charge"), "not listed: {out}");
        assert!(out.contains("Downstream: 3 functions in 2 files call them"), "not counted downstream: {out}");
    }

    // A function with no callers says so plainly only when no call to a method of its name went unresolved.
    #[test]
    fn test_unsure_leaf() {
        let files = [("store.py", "class Store:\n    def get(self, key):\n        return key\n"), ("use.py", "def read(cache):\n    return cache.get(1)\n")];
        let graph = from_sources(&files.map(|(path, source)| (path.to_string(), source.to_string()))).unwrap();
        let out = render(&graph, &options(&["store.py::Store.get"], 3));
        assert!(out.contains("No callers found for certain: store.py · Store.get (1 call to a .get() on an object of unknown class)."), "{out}");
    }

    fn graph() -> Graph {
        from_sources(&FILES.map(|(path, source)| (path.to_string(), source.to_string()))).unwrap()
    }

    fn options(functions: &[&str], depth: usize) -> Options {
        Options {
            repo: PathBuf::from("."),
            base: "HEAD".to_string(),
            functions: functions.iter().map(|name| (*name).to_string()).collect(),
            depth,
            by_name: false,
            json: false,
        }
    }

    fn render(graph: &Graph, options: &Options) -> String {
        let changed: BTreeSet<usize> = options.functions.iter().map(|key| graph.find(key).unwrap()).collect();
        let reports: Vec<Report> = changed.iter().map(|&id| Report { id, reached: reach(graph, id, options.depth, options.by_name), kind: None }).collect();
        text(graph, options, &reports, &changed, &[])
    }

    // A change to charge reaches checkout directly and both api functions through it, as a tree.
    #[test]
    fn test_tree() {
        let graph = graph();
        let out = render(&graph, &options(&["billing.py::charge"], 3));
        assert!(out.contains("Asked about: 1 function."), "header: {out}");
        assert!(out.contains("Downstream: 3 functions in 2 files call them"), "counts: {out}");
        assert!(
            out.contains("billing.py · charge  (asked about)\n  ← checkout.py · checkout\n    ← api.py · place_order\n    ← api.py · refund\n"),
            "tree: {out}"
        );
        assert!(out.contains("Calls tied to this repository's functions: 4 of 4."), "coverage, the test's call included: {out}");
    }

    // A caller that changed too is marked and not expanded: its own entry lists its callers.
    #[test]
    fn test_changed_caller() {
        let graph = graph();
        let out = render(&graph, &options(&["billing.py::charge", "checkout.py::checkout"], 3));
        assert!(out.contains("billing.py · charge  (asked about)\n  ← checkout.py · checkout  (asked about)\n  + 1 test reach it\n\n"), "not expanded: {out}");
        assert!(out.contains("checkout.py · checkout  (asked about)\n  ← api.py · place_order\n  ← api.py · refund\n"), "its own entry: {out}");
    }

    // The depth limit stops the walk; a function nothing calls says so.
    #[test]
    fn test_depth_and_leaf() {
        let graph = graph();
        assert!(!render(&graph, &options(&["billing.py::charge"], 1)).contains("place_order"), "one step only");
        assert!(render(&graph, &options(&["api.py::refund"], 3)).contains("No calls found to: api.py · refund."), "a leaf");
    }

    // The JSON lists every caller with its depth and the function it calls.
    #[test]
    fn test_json() {
        let graph = graph();
        let id = graph.find("billing.py::charge").unwrap();
        let body: Value = serde_json::from_str(&json_text(
            &graph,
            &options(&["billing.py::charge"], 3),
            &[Report { id, reached: reach(&graph, id, 3, false), kind: None }],
            &[],
        ))
        .unwrap();
        assert_eq!(body.get("compared_with"), Some(&Value::Null), "asked by name");
        let second = json!({ "function": "api.py::place_order", "depth": 2, "calls": "checkout.py::checkout", "by_name_only": false, "test": false });
        // nearest first: checkout and the test one step up, then place_order two steps up
        assert_eq!(body.pointer("/changed/0/callers/2"), Some(&second), "a caller two steps up");
        assert_eq!(body.pointer("/changed/0/callers/1/test"), Some(&Value::Bool(true)), "the test is marked as one");
    }

    // Flags are read, and a bad one is a usage error.
    #[test]
    fn test_options() {
        let args: Vec<String> = ["repo", "--base", "main", "--function", "a\\b.py::f", "--depth", "2", "--by-name", "--json"].map(String::from).to_vec();
        let parsed = parse_options(&args).unwrap();
        assert_eq!((parsed.repo, parsed.base.as_str(), parsed.functions, parsed.depth), (PathBuf::from("repo"), "main", vec!["a/b.py::f".to_string()], 2));
        assert!(parsed.by_name && parsed.json, "both switches read");
        assert!(matches!(parse_options(&["--depth".to_string(), "11".to_string()]), Err(Failure::Usage(_))), "depth out of range");
        assert!(matches!(parse_options(&["--nope".to_string()]), Err(Failure::Usage(_))), "unknown flag");
    }
}
