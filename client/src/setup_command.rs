//! `krypton setup [REPO] [--page] [--mode manual|auto] [--target N] [--runs N] [--no-map]`: set up Krypton for one repository, once.
//!
//! 0. It logs you in with GitHub if you are not yet (`krypton login`): the server scores only for a logged-in user.
//! 1. It maps the whole codebase (an ordinary scan) and shows its purity today: the baseline.
//! 2. It asks for the **target**: the purity a ticket's code should reach. Set it above the baseline and every change the agent makes
//!    lifts the code it touches, so the rest of the codebase gets cleaner a ticket at a time.
//! 3. It asks for **max tries**: how many runs the agent gets on a function before Krypton calls it stuck.
//! 4. It asks what happens when the agent is stuck: **ask me** (the agent builds a local review page for the current ticket, styled by
//!    `themes.md`) or **auto** (Krypton puts back the best run and the agent carries on).
//!
//! The questions are asked in the terminal, or, with `--page` (or by answering "browser" to the first question), on a one-time local
//! page where the settings are clicked instead (see `setup_page.rs`).
//!
//! The answers are kept in `.krypton/settings.json`, which git never sees; `krypton review` reads them. `krypton` with no command, in a
//! terminal, starts here, so `docker run -it` on the image is the whole setup.

use std::fs;
use std::io::{self, BufRead, IsTerminal, Write};
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::runlog;
use crate::Failure;

const USAGE: &str = "usage: krypton setup [REPO] [--page [--listen HOST:PORT]] [--mode manual|auto] [--target 0-100] [--runs 1-10] [--no-map]";
const SETTINGS: &str = "settings.json";
const BASELINE: &str = "baseline.json";
/// Used when nobody answers a question (an empty line) and no flag was given.
pub const DEFAULT_TARGET: i64 = 70;
pub const DEFAULT_RUNS: usize = 3;
const MAX_RUNS: usize = 10;

/// What happens when a ticket has functions the agent could not fix.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// A person decides on a local review page.
    Manual,
    /// Krypton puts back the best run and the agent carries on.
    Auto,
}

impl Mode {
    pub const fn name(self) -> &'static str {
        match self {
            Self::Manual => "manual",
            Self::Auto => "auto",
        }
    }

    pub fn parse(text: &str) -> Option<Self> {
        match text.trim().to_ascii_lowercase().as_str() {
            "manual" | "1" | "m" => Some(Self::Manual),
            "auto" | "2" | "a" => Some(Self::Auto),
            _ => None,
        }
    }
}

/// Everything `krypton setup` decides for a repository.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Settings {
    pub mode: Mode,
    /// The purity a ticket's code should reach.
    pub target: i64,
    /// Runs after the start before a function can count as stuck.
    pub max_runs: usize,
    /// The whole codebase's purity when setup ran, if it was mapped.
    pub baseline: Option<i64>,
}

impl Settings {
    /// What review uses before setup has been run.
    pub const DEFAULT: Self = Self { mode: Mode::Manual, target: DEFAULT_TARGET, max_runs: DEFAULT_RUNS, baseline: None };
}

fn settings_path(repo: &Path) -> PathBuf {
    runlog::folder(repo).join(SETTINGS)
}

fn bad_settings(path: &Path, what: &str) -> Failure {
    Failure::Log(format!("{} {what}; run `krypton setup` again", path.display()))
}

/// The settings saved by `krypton setup`, or `None` when setup has not been run here.
///
/// A settings file that cannot be read is an error, never a silent default.
pub fn saved(repo: &Path) -> Result<Option<Settings>, Failure> {
    let path = settings_path(repo);
    if !path.exists() {
        return Ok(None);
    }
    let text = fs::read_to_string(&path).map_err(|error| Failure::Read(path.display().to_string(), error))?;
    let value: Value = serde_json::from_str(&text).map_err(|error| bad_settings(&path, &format!("is not valid JSON: {error}")))?;
    let mode = value.get("review").and_then(Value::as_str).and_then(Mode::parse).ok_or_else(|| bad_settings(&path, "has no review mode (manual or auto)"))?;
    let target = value
        .get("target")
        .and_then(Value::as_i64)
        .filter(|target| (0..=100).contains(target))
        .ok_or_else(|| bad_settings(&path, "has no target from 0 to 100"))?;
    let max_runs = value
        .get("max_runs")
        .and_then(Value::as_u64)
        .and_then(|runs| usize::try_from(runs).ok())
        .filter(|runs| (1..=MAX_RUNS).contains(runs))
        .ok_or_else(|| bad_settings(&path, "has no max_runs from 1 to 10"))?;
    Ok(Some(Settings { mode, target, max_runs, baseline: value.get("baseline").and_then(Value::as_i64) }))
}

fn save(repo: &Path, settings: &Settings) -> Result<PathBuf, Failure> {
    runlog::ensure_folder(repo)?;
    let path = settings_path(repo);
    let body = json!({ "review": settings.mode.name(), "target": settings.target, "max_runs": settings.max_runs, "baseline": settings.baseline });
    let text = serde_json::to_string_pretty(&body).map_err(|error| Failure::Output(error.to_string()))?;
    fs::write(&path, text + "\n").map_err(|error| Failure::Log(format!("cannot write {}: {error}", path.display())))?;
    Ok(path)
}

/// Map the whole codebase with an ordinary scan and return its purity.
fn map_codebase(repo: &Path) -> Result<i64, Failure> {
    runlog::ensure_folder(repo)?;
    let summary = runlog::folder(repo).join(BASELINE);
    let args = [repo.display().to_string(), "--summary".to_string(), summary.display().to_string()];
    crate::scan_command::run(&args)?;
    Ok(runlog::read_summary(&summary)?.purity)
}

/// A starting `themes.md` for the review page, written only when the repository has none.
const STARTER_THEMES: &str = "# Krypton review page style

Your coding agent builds a small review page on your computer when it gets stuck, and it styles that page from this file.
Write it the way you would brief a designer. Anything you leave blank, the agent decides. This file only changes how the page looks,
never what it shows.

## The look

Calm and simple.

## Colors

- Background:
- Cards:
- Text:
- Accent, for buttons:

## Words on the buttons

- Approve: Approve best
- Send back: Send back
";

/// Write `themes.md` next to the code if there is none; never touch one that exists.
fn starter_themes(repo: &Path) -> Result<Option<PathBuf>, Failure> {
    let path = repo.join("themes.md");
    if path.exists() {
        return Ok(None);
    }
    fs::write(&path, STARTER_THEMES).map_err(|error| Failure::Log(format!("cannot write {}: {error}", path.display())))?;
    Ok(Some(path))
}

/// One terminal conversation: the questions are asked here and the answers read here.
struct Prompt<'a> {
    input: &'a mut dyn BufRead,
    output: &'a mut dyn Write,
}

impl Prompt<'_> {
    fn say(&mut self, text: &str) -> Result<(), Failure> {
        self.output.write_all(text.as_bytes()).and_then(|()| self.output.flush()).map_err(|error| Failure::Output(error.to_string()))
    }

    /// Ask until `parse` accepts the answer; an empty line takes `default`, the end of input is an error.
    fn ask<T>(&mut self, question: &str, default: T, parse: impl Fn(&str) -> Option<T>) -> Result<T, Failure> {
        self.say(question)?;
        loop {
            let mut line = String::new();
            let read = self.input.read_line(&mut line).map_err(|error| Failure::Read("standard input".to_string(), error))?;
            if read == 0 {
                return Err(Failure::Usage(format!("no answer was given\n{USAGE}")));
            }
            if line.trim().is_empty() {
                return Ok(default);
            }
            if let Some(answer) = parse(&line) {
                return Ok(answer);
            }
            self.say("That answer does not fit; try again: ")?;
        }
    }
}

pub fn parse_target(text: &str) -> Option<i64> {
    text.trim().trim_end_matches('%').parse().ok().filter(|target| (0..=100).contains(target))
}

pub fn parse_runs(text: &str) -> Option<usize> {
    text.trim().parse().ok().filter(|runs| (1..=MAX_RUNS).contains(runs))
}

fn target_question(baseline: Option<i64>) -> String {
    let today = baseline.map_or_else(String::new, |score| {
        format!("Your codebase scores {score}/100 today. Set the target above that: every ticket must reach it, so new code lifts the rest.\n")
    });
    format!("\n{today}Target purity for each ticket's code, 0-100 [{DEFAULT_TARGET}]: ")
}

const WHERE_QUESTION: &str = "
Choose your settings:

  1) Here         Answer three questions in this terminal.
  2) In a browser Get a link to a page on this computer and click them yourself.

Choose 1 or 2 [1]: ";

/// Whether the person wants the settings page instead of the terminal questions.
fn parse_where(text: &str) -> Option<bool> {
    match text.trim().to_ascii_lowercase().as_str() {
        "1" | "here" | "terminal" => Some(false),
        "2" | "browser" | "page" => Some(true),
        _ => None,
    }
}

const RUNS_QUESTION: &str = "How many tries does the agent get on a function before it counts as stuck, 1-10 [3]: ";

const MODE_QUESTION: &str = "
When the agent is still stuck after those tries, what should happen?

  1) Ask me      Your agent builds a review page on this computer for the current ticket, styled by themes.md.
                 You approve the best try or send it back before anything goes to GitHub.
  2) Auto        Krypton puts back the best run on its own and the agent carries on.

Choose 1 or 2 [1]: ";

/// Ask whatever the flags left open.
fn ask_all(prompt: &mut Prompt, given: &Options, baseline: Option<i64>) -> Result<Settings, Failure> {
    let target = match given.target {
        Some(target) => target,
        None => prompt.ask(&target_question(baseline), DEFAULT_TARGET, parse_target)?,
    };
    let max_runs = match given.max_runs {
        Some(runs) => runs,
        None => prompt.ask(RUNS_QUESTION, DEFAULT_RUNS, parse_runs)?,
    };
    let mode = match given.mode {
        Some(mode) => mode,
        None => prompt.ask(MODE_QUESTION, Mode::Manual, Mode::parse)?,
    };
    Ok(Settings { mode, target, max_runs, baseline })
}

fn next_steps(settings: &Settings, themes: Option<&Path>) -> String {
    let handled = if settings.mode == Mode::Auto { "automatically (best run kept)" } else { "by you, on a local review page" };
    let mut lines = vec![format!(
        "Saved: target {} purity per ticket, {} tries before a function is stuck, stuck functions handled {handled}.",
        settings.target, settings.max_runs
    )];
    if let Some(path) = themes {
        lines.push(format!("Wrote {} for you to style the review page.", path.display()));
    }
    lines.push("Next: krypton scan . --log --ticket \"what you are fixing\"   (before and after each change)".to_string());
    lines.push("Change any of this with krypton setup again, krypton setup --page, or: krypton setup --mode manual|auto --target N --runs N".to_string());
    lines.join("\n")
}

#[derive(Default)]
struct Options {
    repo: PathBuf,
    mode: Option<Mode>,
    target: Option<i64>,
    max_runs: Option<usize>,
    no_map: bool,
    page: bool,
    listen: String,
}

fn usage(message: &str) -> Failure {
    Failure::Usage(format!("{message}\n{USAGE}"))
}

fn parse_options(args: &[String]) -> Result<Options, Failure> {
    let listen = std::env::var("KRYPTON_LISTEN").unwrap_or_else(|_| crate::setup_page::LISTEN.to_string());
    let mut options = Options { repo: PathBuf::from("."), listen, ..Options::default() };
    let mut words = args.iter();
    while let Some(word) = words.next() {
        let mut value = |flag: &str| words.next().cloned().ok_or_else(|| usage(&format!("{flag} needs a value")));
        match word.as_str() {
            "--mode" => options.mode = Some(Mode::parse(&value("--mode")?).ok_or_else(|| usage("--mode is manual or auto"))?),
            "--target" => options.target = Some(parse_target(&value("--target")?).ok_or_else(|| usage("--target is a whole number from 0 to 100"))?),
            "--runs" => options.max_runs = Some(parse_runs(&value("--runs")?).ok_or_else(|| usage("--runs is a whole number from 1 to 10"))?),
            "--no-map" => options.no_map = true,
            "--page" => options.page = true,
            "--listen" => options.listen = value("--listen")?,
            flag if flag.starts_with("--") => return Err(usage(&format!("unknown option {flag}"))),
            path => options.repo = PathBuf::from(path),
        }
    }
    Ok(options)
}

/// Whether `krypton` with no command should start setup: only when a person is at the keyboard.
pub fn interactive() -> bool {
    io::stdin().is_terminal()
}

/// The settings the flags give, with the defaults for anything left open.
fn from_flags(options: &Options, baseline: Option<i64>) -> Settings {
    Settings {
        mode: options.mode.unwrap_or(Mode::Manual),
        target: options.target.unwrap_or(DEFAULT_TARGET),
        max_runs: options.max_runs.unwrap_or(DEFAULT_RUNS),
        baseline,
    }
}

/// Ask in the terminal or on the settings page, whichever the person picks; flags already given are not asked again in the terminal.
fn choose(options: &Options, baseline: Option<i64>) -> Result<Settings, Failure> {
    let mut prompt = Prompt { input: &mut io::stdin().lock(), output: &mut io::stdout().lock() };
    let everything_given = options.mode.is_some() && options.target.is_some() && options.max_runs.is_some();
    let page = options.page || (!everything_given && prompt.ask(WHERE_QUESTION, false, parse_where)?);
    if page {
        return crate::setup_page::choose(&options.listen, &from_flags(options, baseline));
    }
    ask_all(&mut prompt, options, baseline)
}

pub fn run(args: &[String]) -> Result<(), Failure> {
    let options = parse_options(args)?;
    let asking = interactive();
    if !asking && options.mode.is_none() && !options.page {
        return Err(usage("no terminal to ask in: pass --mode manual or --mode auto, or --page for the settings page (or run the image with docker run -it)"));
    }
    if asking {
        crate::loader::splash();
        if !options.no_map {
            crate::login_command::ensure(&crate::http::Server::from_flag(None))?;
        }
        eprintln!("Mapping your codebase ...");
    }
    let baseline = if options.no_map { None } else { Some(map_codebase(&options.repo)?) };
    let settings = if asking || options.page { choose(&options, baseline)? } else { from_flags(&options, baseline) };
    save(&options.repo, &settings)?;
    let themes = if settings.mode == Mode::Manual { starter_themes(&options.repo)? } else { None };
    println!("{}", next_steps(&settings, themes.as_deref()));
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use super::{ask_all, parse_where, save, saved, starter_themes, Mode, Options, Prompt, Settings};

    fn answers(typed: &str) -> Option<Settings> {
        let mut input = Cursor::new(typed.as_bytes().to_vec());
        let mut output = Vec::new();
        ask_all(&mut Prompt { input: &mut input, output: &mut output }, &Options::default(), Some(28)).ok()
    }

    // Enter three times takes the defaults.
    #[test]
    fn test_defaults() {
        assert_eq!(answers("\n\n\n"), Some(Settings { mode: Mode::Manual, target: 70, max_runs: 3, baseline: Some(28) }), "70, 3, ask me");
    }

    // A wrong answer is asked again.
    #[test]
    fn test_asks_again() {
        assert_eq!(answers("150\n75%\n0\n5\nmaybe\n2\n").map(|s| (s.target, s.max_runs, s.mode)), Some((75, 5, Mode::Auto)), "the fitting answers count");
    }

    // "Here" or "browser" picks where the settings are chosen.
    #[test]
    fn test_parse_where() {
        assert_eq!([parse_where("1"), parse_where("browser"), parse_where("x")], [Some(false), Some(true), None], "1 or 2, here or browser");
    }

    // No answer at all is an error, not a guess.
    #[test]
    fn test_no_answer() {
        assert_eq!(answers(""), None, "end of input is refused");
    }

    // The saved settings are read back, and an existing themes.md is never overwritten.
    #[test]
    fn test_saved() {
        let repo = std::env::temp_dir().join(format!("krypton-setup-{}", std::process::id()));
        std::fs::create_dir_all(&repo).unwrap();
        let wanted = Settings { mode: Mode::Auto, target: 72, max_runs: 4, baseline: Some(28) };
        save(&repo, &wanted).unwrap();
        assert_eq!(saved(&repo).unwrap(), Some(wanted), "what was saved comes back");
        std::fs::write(repo.join("themes.md"), "mine").unwrap();
        assert!(starter_themes(&repo).unwrap().is_none(), "an existing themes.md is kept");
        assert_eq!(std::fs::read_to_string(repo.join("themes.md")).unwrap(), "mine", "and left untouched");
        std::fs::remove_dir_all(&repo).unwrap();
    }
}
