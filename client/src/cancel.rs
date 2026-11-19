//! Cancelling a wait with Ctrl+C or Esc: the GitHub login wait and the settings page.
//!
//! Normally Ctrl+C asks the operating system to stop a program. Inside Docker, Krypton is the container's first process, and the system
//! never stops a first process that way, so a wait could not be cancelled. Instead of asking the system, Krypton reads the keys itself:
//! while it waits, it puts the terminal in raw mode (each key arrives as it is pressed, Ctrl+C included, instead of being turned into a
//! signal) and watches for Ctrl+C or Esc in one simple loop.
//!
//! Raw mode must always be switched off again, or the terminal is left unusable. [`RawMode`] makes that automatic: it is a guard. Turning
//! raw mode on gives back a `RawMode` value, and when that value goes away, Rust runs its `Drop`, which turns raw mode off. A value goes
//! away when its owner's scope ends, however it ends: the wait finishing, a `?` returning an error early, or a cancel. So no path can
//! forget to clean up.
//!
//! When nobody can press a key (CI, a pipe, `docker run` without `-it`), nothing changes: the wait is a plain sleep.

use std::io::IsTerminal;
use std::thread;
use std::time::{Duration, Instant};

use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use crossterm::terminal;

use crate::Failure;

/// The exit code for a command cancelled with Ctrl+C or Esc, as shells report a Ctrl+C.
pub const CANCELLED: u8 = 130;

/// The guard: while a `RawMode` value exists, the terminal is in raw mode; dropping it switches raw mode off.
struct RawMode;

impl RawMode {
    /// Switch raw mode on, and return the guard that will switch it off. `None` when there is no terminal to read keys from.
    fn enable() -> Option<Self> {
        if !std::io::stdin().is_terminal() {
            return None;
        }
        terminal::enable_raw_mode().ok().map(|()| Self)
    }
}

impl Drop for RawMode {
    /// Runs by itself when the guard goes away. A failure here cannot be reported anywhere useful, so it is dropped on purpose.
    fn drop(&mut self) {
        drop(terminal::disable_raw_mode());
    }
}

/// Whether this key cancels: Ctrl+C or Esc, when it is pressed (not when it is released).
fn is_cancel(key: &KeyEvent) -> bool {
    let ctrl_c = key.code == KeyCode::Char('c') && key.modifiers.contains(KeyModifiers::CONTROL);
    key.kind == KeyEventKind::Press && (ctrl_c || key.code == KeyCode::Esc)
}

/// Watches the keyboard during a wait. Make one before a waiting loop and keep it until the loop ends; its raw mode lasts exactly
/// that long, because the `RawMode` guard inside goes away with it.
pub struct Watch {
    raw: Option<RawMode>,
}

impl Watch {
    /// Start watching (raw mode on, if there is a terminal).
    #[must_use]
    pub fn start() -> Self {
        Self { raw: RawMode::enable() }
    }

    /// Wait up to `span`, or stop early when Ctrl+C or Esc is pressed.
    ///
    /// # Errors
    ///
    /// [`Failure::Cancelled`] on Ctrl+C or Esc; [`Failure::Output`] when the keyboard cannot be read.
    pub fn wait(&self, span: Duration) -> Result<(), Failure> {
        if self.raw.is_none() {
            thread::sleep(span);
            return Ok(());
        }
        let started = Instant::now();
        loop {
            let left = span.saturating_sub(started.elapsed());
            if left.is_zero() {
                return Ok(());
            }
            if !event::poll(left).map_err(|error| unreadable(&error))? {
                continue;
            }
            let pressed = event::read().map_err(|error| unreadable(&error))?;
            if matches!(pressed, Event::Key(key) if is_cancel(&key)) {
                return Err(Failure::Cancelled);
            }
        }
    }
}

fn unreadable(error: &std::io::Error) -> Failure {
    Failure::Output(format!("cannot read the keyboard: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(code: KeyCode, modifiers: KeyModifiers, kind: KeyEventKind) -> KeyEvent {
        let mut key = KeyEvent::new(code, modifiers);
        key.kind = kind;
        key
    }

    // Ctrl+C and Esc cancel when pressed; a plain c, Ctrl+D or a released key does not.
    #[test]
    fn test_is_cancel() {
        let (press, release) = (KeyEventKind::Press, KeyEventKind::Release);
        let cancels = [
            is_cancel(&key(KeyCode::Char('c'), KeyModifiers::CONTROL, press)),
            is_cancel(&key(KeyCode::Esc, KeyModifiers::NONE, press)),
            is_cancel(&key(KeyCode::Char('c'), KeyModifiers::NONE, press)),
            is_cancel(&key(KeyCode::Char('d'), KeyModifiers::CONTROL, press)),
            is_cancel(&key(KeyCode::Esc, KeyModifiers::NONE, release)),
        ];
        assert_eq!(cancels, [true, true, false, false, false], "only Ctrl+C and Esc, on press");
    }

    // Without a terminal (as in these tests) a wait is a plain sleep that finishes, and raw mode is never touched.
    #[test]
    fn test_wait_without_terminal() {
        let watch = Watch { raw: None };
        let started = Instant::now();
        assert!(watch.wait(Duration::from_millis(30)).is_ok(), "the wait finishes");
        assert!(started.elapsed() >= Duration::from_millis(30), "and lasts its span");
    }
}
