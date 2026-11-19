//! The terminal loader: one lavender diamond pulses (◇ ◈ ◆ ◈) on a single line while the run works, and a bright ◆ means it is done.
//!
//! It is built so that it cannot get in the way of the real output:
//!
//! * it writes to standard error only, so `--json` and pipes on standard output are untouched;
//! * it draws nothing unless standard error is a real terminal (not in CI, pipes, `TERM=dumb`, on Windows consoles, or with `--no-loader`);
//! * it draws nothing for the first 0.3 s, so a quick run never flickers;
//! * it erases itself before the report prints and always gives the cursor back, even when the run fails;
//! * it never slows or changes the scoring: a side thread only reads two counters and the stage name the run updates.
//!
//! Beside the diamond it names the stage the run is in (reading the files, building the trees with a count, scoring on the server). Plain
//! Unicode diamonds draw the same in every UTF-8 terminal, with no special font.

use std::env;
use std::io::{self, IsTerminal, Write};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex, PoisonError};
use std::thread::{self, JoinHandle};
use std::time::Duration;

/// The pulse: the diamond fills and empties, one shape a tick.
pub const PULSE: [&str; 4] = ["◇", "◈", "◆", "◈"];
/// Krypton's mark: the full diamond, shown when a run is done and at the top of the report.
pub const MARK: &str = "◆";
pub type Rgb = (u8, u8, u8);
/// The diamond, and Krypton's name.
pub const LAVENDER: Rgb = (199, 181, 236);
/// The full diamond at the height of the pulse, the stage text, and the finishing flash.
pub const BRIGHT: Rgb = (240, 234, 255);
/// The file count, dimmer than the stage.
pub const MUTED: Rgb = (168, 156, 144);
const HIDE_CURSOR: &str = "\x1b[?25l";
const SHOW_CURSOR: &str = "\x1b[?25h";
const CLEAR_LINE: &str = "\x1b[K";
const SHOW_AFTER: Duration = Duration::from_millis(300);
const TICK: Duration = Duration::from_millis(180);
const FLASH: Duration = Duration::from_millis(120);
/// How many pulse frames `krypton setup`'s greeting shows before it settles on the full diamond.
const SPLASH_TICKS: usize = 8;

/// `text` in a 24-bit colour, or unchanged when colour is off (`NO_COLOR`).
pub fn paint(text: &str, rgb: Rgb, color: bool) -> String {
    if color {
        format!("\x1b[38;2;{};{};{}m{text}\x1b[0m", rgb.0, rgb.1, rgb.2)
    } else {
        text.to_string()
    }
}

/// The diamond at `tick` of the pulse, indented: lavender, and bright when it is full.
fn frame(tick: usize, color: bool) -> String {
    let index = tick.checked_rem(PULSE.len()).unwrap_or(0);
    let shape = PULSE.get(index).copied().unwrap_or(MARK);
    format!("  {}", paint(shape, if shape == MARK { BRIGHT } else { LAVENDER }, color))
}

/// The line with the stage written after the diamond, and while files are being counted, how many are done.
fn with_stage(mut line: String, stage: &str, done: usize, total: usize, color: bool) -> String {
    if stage.is_empty() {
        return line;
    }
    line.push_str("  ");
    line.push_str(&paint(stage, BRIGHT, color));
    if total > 1 && done < total {
        line.push_str(&paint(&format!("  {done} of {total} files"), MUTED, color));
    }
    line
}

/// Krypton's name, in its two shades.
fn name(color: bool) -> String {
    paint("Kr", BRIGHT, color) + &paint("ypton", LAVENDER, color)
}

/// The diamond pulsing beside the name, then settling on the full diamond: the greeting `krypton setup` opens with. Nothing is drawn where
/// the loader would not draw (see [`wanted`]).
pub fn splash() {
    if !wanted(&Environment::read(), false) {
        return;
    }
    let color = env::var_os("NO_COLOR").is_none();
    let mut out = io::stderr().lock();
    for tick in 0..SPLASH_TICKS {
        let line = format!("{HIDE_CURSOR}\r{}  {}{CLEAR_LINE}", frame(tick, color), name(color));
        if out.write_all(line.as_bytes()).and_then(|()| out.flush()).is_err() {
            return;
        }
        thread::sleep(Duration::from_millis(110));
    }
    let settled = format!("\r  {}  {}{CLEAR_LINE}\n\n{SHOW_CURSOR}", paint(MARK, BRIGHT, color), name(color));
    // a closed terminal at the very end leaves nothing to clean up
    let _done = out.write_all(settled.as_bytes()).and_then(|()| out.flush()).is_ok();
}

/// What the loader needs to know about where it is running.
pub struct Environment {
    pub is_terminal: bool,
    pub term: Option<String>,
    pub in_ci: bool,
    pub opted_out: bool,
}

impl Environment {
    /// Read it from the process: standard error, `TERM`, `CI` and `KRYPTON_NO_LOADER`.
    #[must_use]
    pub fn read() -> Self {
        Self {
            is_terminal: io::stderr().is_terminal(),
            term: env::var("TERM").ok(),
            in_ci: env::var_os("CI").is_some(),
            opted_out: env::var_os("KRYPTON_NO_LOADER").is_some(),
        }
    }
}

/// True only for a real, capable terminal and a person who did not switch the loader off. Nothing is drawn anywhere else.
#[must_use]
pub fn wanted(environment: &Environment, switched_off: bool) -> bool {
    let capable = environment.is_terminal && environment.term.as_deref() != Some("dumb") && !cfg!(windows);
    capable && !environment.in_ci && !environment.opted_out && !switched_off
}

/// Starts the loader when this is a terminal that should show it.
#[must_use]
pub fn start_if_wanted(switched_off: bool) -> Option<Loader> {
    wanted(&Environment::read(), switched_off).then(|| Loader::start(env::var_os("NO_COLOR").is_none()))
}

struct Shared {
    stage: Mutex<String>,
    done: AtomicUsize,
    total: AtomicUsize,
    drawn: AtomicBool,
    hidden: AtomicBool,
    stopped: Mutex<bool>,
    wake: Condvar,
    color: bool,
    out: Mutex<Box<dyn Write + Send>>,
}

impl Shared {
    /// Wait up to `span`; true when the loader was stopped meanwhile.
    fn wait(&self, span: Duration) -> bool {
        let guard = self.stopped.lock().unwrap_or_else(PoisonError::into_inner);
        let (guard, _) = self.wake.wait_timeout_while(guard, span, |stopped| !*stopped).unwrap_or_else(PoisonError::into_inner);
        *guard
    }

    fn stop(&self) {
        *self.stopped.lock().unwrap_or_else(PoisonError::into_inner) = true;
        self.wake.notify_all();
    }

    /// Write and flush; a closed terminal stops the loader instead of printing anywhere else.
    fn write(&self, text: &str) -> bool {
        if text.is_empty() {
            return true;
        }
        let mut out = self.out.lock().unwrap_or_else(PoisonError::into_inner);
        let written = out.write_all(text.as_bytes()).and_then(|()| out.flush()).is_ok();
        drop(out);
        if !written {
            self.stop();
        }
        written
    }

    /// Redraw the one line in place, clearing the rest of it so a shorter line leaves nothing behind.
    fn draw(&self, line: &str) -> bool {
        self.drawn.store(true, Ordering::SeqCst);
        self.write(&format!("\r{line}{CLEAR_LINE}"))
    }

    /// Remove the line so the report prints on a clean screen.
    fn erase(&self) {
        if self.drawn.swap(false, Ordering::SeqCst) {
            self.write(&format!("\r{CLEAR_LINE}"));
        }
    }

    /// The side thread: wait, hide the cursor, then pulse one shape a tick until the run ends.
    fn run(&self) {
        if self.wait(SHOW_AFTER) || !self.write(HIDE_CURSOR) {
            return;
        }
        self.hidden.store(true, Ordering::SeqCst);
        let mut tick: usize = 0;
        while !self.wait(TICK) {
            let (done, total) = (self.done.load(Ordering::SeqCst), self.total.load(Ordering::SeqCst));
            let stage = self.stage.lock().unwrap_or_else(PoisonError::into_inner).clone();
            if !self.draw(&with_stage(frame(tick, self.color), &stage, done, total, self.color)) {
                return;
            }
            tick = tick.wrapping_add(1);
        }
    }
}

/// Draws from a side thread; the run calls [`Loader::update`], and dropping it (or [`Loader::finish`]) ends and erases the line.
pub struct Loader {
    shared: Arc<Shared>,
    thread: Option<JoinHandle<()>>,
    finished: bool,
}

impl Loader {
    /// Start drawing on standard error (the first frame waits 0.3 s).
    #[must_use]
    pub fn start(color: bool) -> Self {
        Self::with_output(color, Box::new(io::stderr()))
    }

    fn with_output(color: bool, out: Box<dyn Write + Send>) -> Self {
        let shared = Arc::new(Shared {
            stage: Mutex::new(String::new()),
            done: AtomicUsize::new(0),
            total: AtomicUsize::new(1),
            drawn: AtomicBool::new(false),
            hidden: AtomicBool::new(false),
            stopped: Mutex::new(false),
            wake: Condvar::new(),
            color,
            out: Mutex::new(out),
        });
        let worker = Arc::clone(&shared);
        Self { shared, thread: Some(thread::spawn(move || worker.run())), finished: false }
    }

    /// Name the stage the run is in; it is drawn after the diamond.
    pub fn stage(&self, text: &str) {
        text.clone_into(&mut self.shared.stage.lock().unwrap_or_else(PoisonError::into_inner));
    }

    /// Record how many files are done; the side thread reads these two numbers.
    pub fn update(&self, done: usize, total: usize) {
        self.shared.done.store(done, Ordering::SeqCst);
        self.shared.total.store(total, Ordering::SeqCst);
    }

    /// The run succeeded: flash the full diamond before it is erased.
    pub fn finish(mut self) {
        self.finished = true;
    }
}

impl Drop for Loader {
    /// End the side thread, flash the full diamond when the run finished, erase the line, and give the cursor back.
    fn drop(&mut self) {
        self.shared.stop();
        if let Some(thread) = self.thread.take() {
            drop(thread.join());
        }
        if self.shared.drawn.load(Ordering::SeqCst) && self.finished {
            for tint in [BRIGHT, LAVENDER, BRIGHT] {
                self.shared.draw(&format!("  {}", paint(MARK, tint, self.shared.color)));
                thread::sleep(FLASH);
            }
        }
        self.shared.erase();
        if self.shared.hidden.load(Ordering::SeqCst) {
            self.shared.write(SHOW_CURSOR);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A writer the test can read back.
    #[derive(Clone, Default)]
    struct Capture(Arc<Mutex<Vec<u8>>>);

    impl Write for Capture {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.0.lock().unwrap_or_else(PoisonError::into_inner).extend_from_slice(bytes);
            Ok(bytes.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    impl Capture {
        fn text(&self) -> String {
            String::from_utf8_lossy(&self.0.lock().unwrap()).into_owned()
        }
    }

    fn terminal() -> Environment {
        Environment { is_terminal: true, term: Some("xterm".to_string()), in_ci: false, opted_out: false }
    }

    // The diamond fills and empties, round and round, and is bright only when full.
    #[test]
    fn test_pulse() {
        let shapes: Vec<String> = (0..6).map(|tick| frame(tick, false)).collect();
        assert_eq!(shapes, ["  ◇", "  ◈", "  ◆", "  ◈", "  ◇", "  ◈"], "hollow, half, full, half, again");
        assert!(frame(0, true).contains("\x1b[38;2;199;181;236m") && frame(2, true).contains("\x1b[38;2;240;234;255m"), "lavender, bright when full");
    }

    // The stage follows the diamond on the same line, with the file count only while it is counting.
    #[test]
    fn test_stage_beside() {
        assert_eq!(with_stage(frame(1, false), "Building the trees", 73, 145, false), "  ◈  Building the trees  73 of 145 files", "stage and count");
        assert_eq!(with_stage(frame(2, false), "Scoring on the Krypton server", 145, 145, false), "  ◆  Scoring on the Krypton server", "no count when done");
        assert_eq!(with_stage(frame(0, false), "", 0, 1, false), "  ◇", "no stage, no text");
    }

    // Nothing is drawn where it would get in the way.
    #[test]
    fn test_wanted_only_on_a_terminal() {
        assert_eq!(wanted(&terminal(), false), !cfg!(windows), "a capable terminal gets it (not a Windows console)");
        assert!(!wanted(&Environment { is_terminal: false, ..terminal() }, false), "a pipe gets nothing");
        assert!(!wanted(&Environment { term: Some("dumb".to_string()), ..terminal() }, false), "a dumb terminal gets nothing");
        assert!(!wanted(&Environment { in_ci: true, ..terminal() }, false), "CI gets nothing");
        assert!(!wanted(&Environment { opted_out: true, ..terminal() }, false), "KRYPTON_NO_LOADER switches it off");
        assert!(!wanted(&terminal(), true), "--no-loader switches it off");
    }

    // A quick run draws nothing at all, not even a cursor change.
    #[test]
    fn test_quick_run_silent() {
        let capture = Capture::default();
        let loader = Loader::with_output(true, Box::new(capture.clone()));
        loader.update(1, 1);
        loader.finish();
        assert_eq!(capture.text(), "", "nothing was written");
    }

    // A slow run pulses on one line, then erases it and gives the cursor back.
    #[test]
    fn test_slow_run_cleans_up() {
        let capture = Capture::default();
        let loader = Loader::with_output(true, Box::new(capture.clone()));
        loader.update(6, 10);
        thread::sleep(Duration::from_millis(900));
        loader.finish();
        let text = capture.text();
        assert!(text.starts_with(HIDE_CURSOR), "the cursor is hidden first");
        assert!(text.contains('◇') && text.contains('◈') && text.contains("\x1b[38;2;199;181;236m"), "lavender pulse frames were drawn");
        assert!(!text.contains('\n'), "one line, redrawn in place");
        assert!(text.ends_with(&format!("\r{CLEAR_LINE}{SHOW_CURSOR}")), "erased, then the cursor given back");
    }

    // A failed run (the loader dropped without finish) still erases and restores the cursor, and skips the finishing flash.
    #[test]
    fn test_failed_run_cleans_up() {
        let capture = Capture::default();
        let loader = Loader::with_output(false, Box::new(capture.clone()));
        thread::sleep(Duration::from_millis(700));
        drop(loader);
        let text = capture.text();
        assert!(text.ends_with(SHOW_CURSOR), "the cursor is given back even on failure");
        assert!(!text.contains("\x1b[38;2"), "no colour when colour is off");
    }
}
