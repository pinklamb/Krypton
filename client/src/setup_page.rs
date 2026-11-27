//! The settings page of `krypton setup --page`: a one-time page on this computer where a person clicks the settings instead of typing them.
//!
//! It is a tiny HTTP server built on the standard library. It listens on `127.0.0.1:7777` (or `KRYPTON_LISTEN`, which the Docker image sets
//! to `0.0.0.0:7777` so a port published as `-p 127.0.0.1:7777:7777` reaches it), serves one page at a secret link, and stops as soon as
//! the settings are saved.
//!
//! Any web page open in the browser can send requests to this computer, so the page guards itself:
//! - the link printed in the terminal carries a fresh 256-bit secret from the operating system's random source;
//! - the first visit swaps it for a cookie (`HttpOnly`, `SameSite=Strict`) and redirects to `/`, so the secret leaves the address bar, and
//!   the link is then spent: opening it again (from history, or a copied link) is refused;
//! - every later request must carry that cookie (compared in constant time), a `Host` of this computer (against DNS rebinding) and, for
//!   the Save, an `Origin` (or `Referer`) of this page;
//! - a refused request is answered and the page keeps serving, so a stray request cannot end the person's setup;
//! - the session ends with the Save, or after [`IDLE`] without a valid request.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::time::{Duration, Instant};

use crate::setup_command::{parse_runs, parse_target, Mode, Settings};
use crate::workflow::Gate;
use crate::Failure;

/// Where the page listens when `KRYPTON_LISTEN` is not set: this computer only.
pub const LISTEN: &str = "127.0.0.1:7777";
/// A settings form is a few dozen bytes; anything much larger is not ours.
const MOST_BODY: usize = 4096;
/// How long a connection may stay quiet before it is dropped. Browsers open spare connections ahead of time and often send nothing on
/// them; waiting on one without a limit would leave the real request (the Save) unanswered behind it.
const QUIET: Duration = Duration::from_secs(2);
/// How long the page waits without a valid request before the link expires.
pub const IDLE: Duration = Duration::from_mins(30);
/// The cookie the link's secret is swapped for. Browsers do not keep cookies apart by port, so the name is the page's own.
const COOKIE: &str = "krypton_setup";
/// Sent with every answer: never cached, never named in a `Referer`, never framed, and no scripts or outside resources at all.
const GUARD: &str = "Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nX-Frame-Options: DENY\r\nX-Content-Type-Options: nosniff\r\n\
Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'\r\n";

/// A secret for the link: 32 bytes from the operating system's secure random source, as 64 hex characters.
///
/// # Errors
/// When the operating system gives no randomness.
fn secret() -> Result<String, Failure> {
    let mut bytes = [0_u8; 32];
    getrandom::getrandom(&mut bytes).map_err(|error| Failure::Output(format!("no randomness for the settings link: {error}")))?;
    Ok(krypton_client::hex(&bytes))
}

/// Equal bytes, compared in constant time: every byte is looked at whatever the first difference. The early length check reveals only
/// the length, which is fixed and public here (a 64-character secret); do not reuse this for secrets whose length is itself private.
fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0_u8, |seen, (x, y)| seen | (x ^ y)) == 0
}

/// A `Host` (or the host part of an origin) that is this computer on the page's port.
fn local_host(host: &str, port: u16) -> bool {
    let host = host.to_ascii_lowercase();
    ["localhost", "127.0.0.1", "[::1]"].iter().any(|name| host == format!("{name}:{port}"))
}

/// The Save came from this page: its `Origin`, or the origin of its `Referer` when a browser sent no `Origin`. Neither is refused.
fn local_origin(origin: Option<&str>, referer: Option<&str>, port: u16) -> bool {
    let Some(source) = origin.or(referer) else { return false };
    source.strip_prefix("http://").and_then(|rest| rest.split('/').next()).is_some_and(|host| local_host(host, port))
}

/// One cookie's value from a `Cookie` header.
fn cookie_value<'a>(header: Option<&'a str>, name: &str) -> Option<&'a str> {
    header?.split(';').filter_map(|pair| pair.trim().split_once('=')).find(|(key, _)| *key == name).map(|(_, value)| value)
}

/// One request as far as the page needs it: the method, the path, the headers it checks and the form body.
#[derive(Default)]
struct Request {
    method: String,
    path: String,
    host: Option<String>,
    origin: Option<String>,
    referer: Option<String>,
    cookie: Option<String>,
    body: String,
}

/// The page's session: the secret, whether its link was already used, the port browsers reach, and when it was last used.
pub struct Session {
    key: String,
    spent: bool,
    port: u16,
    last: Instant,
}

impl Session {
    /// A fresh session for a page reached on `port`.
    ///
    /// # Errors
    /// When the operating system gives no randomness.
    pub fn new(port: u16) -> Result<Self, Failure> {
        Ok(Self { key: secret()?, spent: false, port, last: Instant::now() })
    }

    /// The link to print: it works once.
    #[must_use]
    pub fn link(&self) -> String {
        format!("http://localhost:{}/{}", self.port, self.key)
    }

    /// The request carries this session's cookie.
    fn signed_in(&self, request: &Request) -> bool {
        cookie_value(request.cookie.as_deref(), COOKIE).is_some_and(|value| same(value.as_bytes(), self.key.as_bytes()))
    }

    /// The request is for the link itself (compared in constant time).
    fn is_link(&self, request: &Request) -> bool {
        request.path.strip_prefix('/').is_some_and(|rest| same(rest.as_bytes(), self.key.as_bytes()))
    }
}

/// A connection that went quiet: a browser's spare connection, or one it gave up on.
fn quiet(error: &std::io::Error) -> bool {
    matches!(error.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut)
}

/// The request on this connection, `None` when the browser sent nothing (a spare connection), or what is wrong with it.
fn read_request(stream: &TcpStream) -> Result<Option<Request>, String> {
    let mut reader = BufReader::new(stream);
    let mut first = String::new();
    match reader.read_line(&mut first) {
        Ok(0) => return Ok(None),
        Err(error) if quiet(&error) => return Ok(None),
        Err(error) => return Err(error.to_string()),
        Ok(_) => {}
    }
    let mut words = first.split_whitespace();
    let (method, path) = (words.next().unwrap_or_default().to_string(), words.next().unwrap_or_default().to_string());
    let mut request = Request { method, path, ..Request::default() };
    let length = read_headers(&mut reader, &mut request)?;
    if length > MOST_BODY {
        return Err("form too large".to_string());
    }
    let mut body = vec![0; length];
    reader.read_exact(&mut body).map_err(|error| error.to_string())?;
    request.body = String::from_utf8_lossy(&body).into_owned();
    Ok(Some(request))
}

/// Read the headers up to the blank line, keeping the ones the page checks; the body's length.
fn read_headers(reader: &mut impl BufRead, request: &mut Request) -> Result<usize, String> {
    let mut length = 0;
    loop {
        let mut line = String::new();
        reader.read_line(&mut line).map_err(|error| error.to_string())?;
        let Some((name, value)) = line.split_once(':') else { return Ok(length) };
        let value = value.trim().to_string();
        match name.trim().to_ascii_lowercase().as_str() {
            "content-length" => length = value.parse().map_err(|error| format!("bad {name}: {error}"))?,
            "host" => request.host = Some(value),
            "origin" => request.origin = Some(value),
            "referer" => request.referer = Some(value),
            "cookie" => request.cookie = Some(value),
            _ => {}
        }
    }
}

/// The four settings from a submitted form (`target=70&runs=3&mode=manual&gate=worse`), or what is wrong with it.
pub fn parse_form(body: &str, baseline: Option<i64>) -> Result<Settings, String> {
    let field = |key: &str| body.split('&').filter_map(|pair| pair.split_once('=')).find(|(name, _)| *name == key).map(|(_, value)| value.trim().to_string());
    let target = field("target").as_deref().and_then(parse_target).ok_or("The target is a whole number from 0 to 90 (no code scores above 90).")?;
    let max_runs = field("runs").as_deref().and_then(parse_runs).ok_or("Max tries is a whole number from 1 to 10.")?;
    let mode = field("mode").as_deref().and_then(Mode::parse).ok_or("Choose Ask me or Auto.")?;
    let gate = field("gate").as_deref().and_then(Gate::parse).ok_or("Choose whether to set up the pull request gate.")?;
    let hook = field("hook").as_deref().and_then(crate::setup_command::parse_yes_no).ok_or("Choose whether to check each push on this computer.")?;
    Ok(Settings { mode, target, max_runs, baseline, gate, hook })
}

fn respond(stream: &TcpStream, status: &str, html: &str) {
    respond_with(stream, status, "", html);
}

/// Answer with `extra` headers (each ending in CRLF) besides the guarding ones.
fn respond_with(mut stream: &TcpStream, status: &str, extra: &str, html: &str) {
    let head =
        format!("HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\n{GUARD}{extra}Connection: close\r\n\r\n", html.len());
    // The browser may already have gone; the page's own state does not depend on it reading the answer.
    let sent = stream.write_all(head.as_bytes()).and_then(|()| stream.write_all(html.as_bytes()));
    if let Err(error) = sent {
        eprintln!("the settings page could not answer the browser: {error}");
    }
}

/// Serve the page until the settings are submitted, and return them.
///
/// # Errors
/// When the address cannot be listened on (for example the port is taken), there is no randomness for the link, or the link expires.
pub fn choose(listen: &str, current: &Settings, theme: Theme) -> Result<Settings, Failure> {
    let listener = TcpListener::bind(listen).map_err(|error| Failure::Setup(format!("cannot open the settings page on {listen}: {error}")))?;
    let address = listener.local_addr().map_err(|error| Failure::Setup(format!("cannot open the settings page on {listen}: {error}")))?;
    warn_if_open(address);
    let mut session = Session::new(address.port())?;
    println!("\nOpen this link to choose your settings (it works once, on this computer):\n\n  {}\n\nPress Ctrl+C or Esc to cancel.\n", session.link());
    // Not blocking: the loop takes turns between the browser (a request waiting?) and the keyboard (Ctrl+C or Esc in the next 100 ms?),
    // so neither one freezes the other. The keyboard is watched (raw mode) until this function returns; nothing is printed meanwhile.
    listener.set_nonblocking(true).map_err(|error| Failure::Setup(format!("cannot open the settings page on {listen}: {error}")))?;
    let watch = crate::cancel::Watch::start();
    loop {
        if session.last.elapsed() > IDLE {
            return Err(Failure::Setup("the settings link expired after 30 minutes without a visit; run krypton setup --page again".to_string()));
        }
        match listener.accept() {
            Ok((stream, _from)) => {
                if let Some(settings) = answer(&stream, &mut session, current, theme) {
                    return Ok(settings);
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => watch.wait(Duration::from_millis(100))?,
            Err(error) => return Err(Failure::Setup(format!("the settings page on {listen} stopped before anything was saved: {error}"))),
        }
    }
}

/// Say so when the page listens where other computers could reach it. Inside the Docker image it must listen on every interface (a
/// container's own loopback cannot be published); there `-p 127.0.0.1:7777:7777` keeps it on this computer, so nothing is said.
fn warn_if_open(address: std::net::SocketAddr) {
    if !address.ip().is_loopback() && !std::path::Path::new("/.dockerenv").exists() {
        eprintln!("note: the settings page listens on {address}, which other computers on your network can reach; its link's secret still guards it");
    }
}

/// Answer one browser request: the page, a refusal, or (when the form was sent and is valid) the saved settings. A refused request is
/// answered and the page keeps serving.
fn answer(stream: &TcpStream, session: &mut Session, current: &Settings, theme: Theme) -> Option<Settings> {
    // the listener does not block, but each request is read and answered in one go, giving up on a connection that stays quiet
    if stream.set_nonblocking(false).and_then(|()| stream.set_read_timeout(Some(QUIET))).is_err() {
        return None;
    }
    let request = match read_request(stream) {
        Ok(Some(request)) => request,
        Ok(None) => return None,
        Err(error) => {
            respond(stream, "400 Bad Request", &page(current, Some(&error), theme));
            return None;
        }
    };
    if !request.host.as_deref().is_some_and(|host| local_host(host, session.port)) {
        respond(stream, "421 Misdirected Request", "<p>This page answers only on this computer.</p>");
        return None;
    }
    if session.is_link(&request) {
        swap(stream, session, &request);
        return None;
    }
    if !session.signed_in(&request) {
        respond(stream, "403 Forbidden", "<p>Not here. Use the link krypton setup printed in your terminal.</p>");
        return None;
    }
    session.last = Instant::now();
    if request.path != "/" {
        respond(stream, "404 Not Found", "<p>Not here.</p>");
        return None;
    }
    if request.method != "POST" {
        respond(stream, "200 OK", &page(current, None, theme));
        return None;
    }
    if !local_origin(request.origin.as_deref(), request.referer.as_deref(), session.port) {
        respond(stream, "403 Forbidden", "<p>The form was not sent from this page.</p>");
        return None;
    }
    save(stream, &request, current, theme)
}

/// The link's first visit: spend it, hand over the cookie and send the browser to `/`. Later visits are refused.
fn swap(stream: &TcpStream, session: &mut Session, request: &Request) {
    if session.spent || request.method != "GET" {
        respond(stream, "403 Forbidden", "<p>This link was already used. Go back to the tab it opened, or start again in the terminal.</p>");
        return;
    }
    session.spent = true;
    session.last = Instant::now();
    let cookie = format!("Set-Cookie: {COOKIE}={}; HttpOnly; SameSite=Strict; Path=/\r\nLocation: /\r\n", session.key);
    respond_with(stream, "302 Found", &cookie, "<p>Opening the settings ...</p>");
}

/// A Save from this page: the settings when the form is valid, or the page again with what is wrong.
fn save(stream: &TcpStream, request: &Request, current: &Settings, theme: Theme) -> Option<Settings> {
    match parse_form(&request.body, current.baseline) {
        Ok(settings) => {
            respond(stream, "200 OK", &saved(theme));
            Some(settings)
        }
        Err(problem) => {
            respond(stream, "400 Bad Request", &page(current, Some(&problem), theme));
            None
        }
    }
}

/// The page's look. Cocoa is Krypton's own (dark, lavender accent); blossom is cherry blossom (soft pink, deep rose accent), picked with
/// `krypton setup --page --theme blossom`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Theme {
    #[default]
    Cocoa,
    Blossom,
}

impl Theme {
    /// `cocoa`, or `blossom` (also `cherry-blossom`).
    #[must_use]
    pub fn parse(text: &str) -> Option<Self> {
        match text {
            "cocoa" => Some(Self::Cocoa),
            "blossom" | "cherry-blossom" => Some(Self::Blossom),
            _ => None,
        }
    }

    /// The colours the page's style reads.
    const fn colors(self) -> &'static str {
        match self {
            Self::Cocoa => COCOA,
            Self::Blossom => BLOSSOM,
        }
    }

    /// A mark before the heading: a five-petal blossom on the blossom page, nothing on cocoa.
    const fn mark(self) -> &'static str {
        match self {
            Self::Cocoa => "",
            Self::Blossom => FLOWER,
        }
    }
}

const COCOA: &str = ":root{color-scheme:dark;--bg:#1b1613;--wash:none;--card:#251e1a;--field:#1b1613;--ink:#ede4da;--ink2:#cfc4b8;--mute:#a89c90;\
--line:#4a3f37;--acc:#c7b5ec;--on-acc:#1b1613;--err:#e59a9a;--shadow:rgba(0,0,0,.3)}";
const BLOSSOM: &str = ":root{color-scheme:light;--bg:#fcf0f3;--wash:radial-gradient(circle at 8% 4%,#f8d3de 0,transparent 32%),\
radial-gradient(circle at 96% 92%,#f9dbe4 0,transparent 38%);--card:#fffafb;--field:#fff4f7;--ink:#3b2129;--ink2:#5e3a45;--mute:#94707a;\
--line:#efc9d5;--acc:#c13f63;--on-acc:#fff;--err:#b3261e;--shadow:rgba(193,63,99,.12)}";
const FLOWER: &str = "<svg class=mark viewBox='-12 -12 24 24' aria-hidden=true><g fill='#f4a7bb'><ellipse cx=0 cy=-6 rx=4 ry=6/>\
<ellipse cx=0 cy=-6 rx=4 ry=6 transform='rotate(72)'/><ellipse cx=0 cy=-6 rx=4 ry=6 transform='rotate(144)'/>\
<ellipse cx=0 cy=-6 rx=4 ry=6 transform='rotate(216)'/><ellipse cx=0 cy=-6 rx=4 ry=6 transform='rotate(288)'/></g>\
<circle r=2.4 fill='#c13f63'/></svg>";

const STYLE: &str = "body{margin:0;background:var(--bg);background-image:var(--wash);color:var(--ink);font:300 16px/1.6 system-ui,-apple-system,'Segoe UI',sans-serif;min-height:100vh}
main{max-width:560px;margin:0 auto;padding:56px 16px}
h1{font-weight:300;font-size:40px;margin:0 0 4px;display:flex;align-items:center;gap:12px}h1 small{font-size:16px;color:var(--mute)}.mark{width:34px;height:34px}
p{color:var(--ink2)}b{color:var(--acc);font-weight:500}.card{background:var(--card);border-radius:18px;padding:28px;margin-top:28px;box-shadow:0 6px 24px var(--shadow)}
fieldset{border:0;margin:0 0 22px;padding:0}legend{font-style:italic;color:var(--ink2);margin-bottom:8px}
label{display:inline-block;margin:0 6px 6px 0}input[type=radio]{position:absolute;opacity:0}
label span{display:inline-block;padding:6px 16px;border-radius:999px;border:1px solid var(--line);background:var(--field);color:var(--ink);cursor:pointer}
input:checked+span{background:var(--acc);border-color:var(--acc);color:var(--on-acc)}input:focus-visible+span{outline:2px solid var(--acc);outline-offset:2px}
input[type=number]{font:inherit;width:90px;padding:6px 12px;border-radius:999px;border:1px solid var(--line);background:var(--field);color:var(--ink)}
button{font:inherit;background:var(--acc);color:var(--on-acc);border:0;border-radius:999px;padding:10px 26px;cursor:pointer}
.err{color:var(--err)}.hint{font-size:14px;color:var(--mute)}.done{text-align:center;font-size:18px}";

/// The page's head: the theme's colours and the shared style.
fn head(theme: Theme) -> String {
    format!(
        "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Krypton setup</title><style>{}{STYLE}</style></head>",
        theme.colors()
    )
}

fn choice(name: &str, value: &str, text: &str, checked: bool) -> String {
    let on = if checked { " checked" } else { "" };
    format!("<label><input type=radio name={name} value={value}{on}><span>{text}</span></label>")
}

fn page(current: &Settings, problem: Option<&str>, theme: Theme) -> String {
    let today = current.baseline.map_or_else(String::new, |score| {
        format!(
            "<p>Your codebase scores <b>{score}%</b> today. Krypton suggests <b>{}%</b>, a step above: only new tickets are held to it, so \
             new work slowly cleans up the old and old code is never blocked.</p>",
            current.target
        )
    });
    let error = problem.map_or_else(String::new, |text| format!("<p class=err>{text}</p>"));
    let runs: String = [1, 2, 3, 5, 10].iter().map(|runs| choice("runs", &runs.to_string(), &runs.to_string(), *runs == current.max_runs)).collect();
    let modes = choice("mode", "manual", "Ask me", current.mode == Mode::Manual) + &choice("mode", "auto", "Auto", current.mode == Mode::Auto);
    let gates: String = [(Gate::Off, "Not now"), (Gate::Worse, "Never worse"), (Gate::Target, "Target")]
        .iter()
        .map(|(gate, text)| choice("gate", gate.name(), text, *gate == current.gate))
        .collect();
    let hooks = choice("hook", "on", "Check it", current.hook) + &choice("hook", "off", "Don't", !current.hook);
    format!(
        "{head}
<body><main><h1>{mark}<span>Kr<small>ypton</small> setup</span></h1>{today}{error}<form method=post class=card>
<fieldset><legend>Target score for each ticket</legend><input type=number name=target min=0 max=90 value={target} required></fieldset>
<fieldset><legend>Max tries on a function before it counts as stuck</legend>{runs}</fieldset>
<fieldset><legend>When the agent is still stuck</legend>{modes}<p class=hint>Ask me: your agent builds a review page for the current ticket, styled by themes.md, and you decide before anything goes to GitHub. Auto: Krypton keeps the best run and the agent carries on.</p></fieldset>
<fieldset><legend>Pull request gate on GitHub</legend>{gates}<p class=hint>Writes .github/workflows/krypton.yml; commit it to turn the gate on. Never worse: a pull request fails only if it lowers the purity score. Target: the files a pull request changes must reach your target; old code is never judged. An existing workflow is never overwritten.</p><p class=hint>So the comment on each pull request comes from Krypton rather than github-actions, <a href={app} target=_blank rel=noopener>install the Krypton app</a> on this repository once.</p></fieldset>
<fieldset><legend>Before each git push on this computer</legend>{hooks}<p class=hint>Installs a git pre-push hook: every push is scored first with the same rule, and a push that breaks it is stopped, so pushes without a pull request are checked too. git push --no-verify skips it once. A pre-push hook of your own is never overwritten.</p></fieldset>
<button type=submit>Save settings</button></form></main></body></html>",
        target = current.target,
        app = crate::workflow::APP_INSTALL,
        head = head(theme),
        mark = theme.mark()
    )
}

/// What the browser shows once the settings are saved.
fn saved(theme: Theme) -> String {
    format!("{}<body><main><p class=done>Saved. You can close this tab; the rest is in your terminal.</p></main></body></html>", head(theme))
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};

    use super::{answer, cookie_value, local_host, local_origin, page, parse_form, same, secret, Session, Theme};
    use crate::setup_command::{Mode, Settings};
    use crate::workflow::Gate;

    // A submitted form becomes the settings, and a wrong one says what is wrong.
    #[test]
    fn test_parse_form() {
        let settings = parse_form("target=72&runs=4&mode=auto&gate=target&hook=on", Some(28)).unwrap();
        assert_eq!(settings, Settings { mode: Mode::Auto, target: 72, max_runs: 4, baseline: Some(28), gate: Gate::Target, hook: true }, "all five read");
        assert!(parse_form("target=72&runs=4&mode=auto&gate=target", None).is_err(), "a missing push check answer is refused");
        assert!(parse_form("target=150&runs=4&mode=auto", None).is_err(), "a target over 100 is refused");
        assert!(parse_form("target=70&runs=3&gate=worse", None).is_err(), "a missing mode is refused");
        assert!(parse_form("target=70&runs=3&mode=auto", None).is_err(), "a missing gate is refused");
    }

    // The page starts from the current settings and shows the baseline.
    #[test]
    fn test_page() {
        let html = page(&Settings { mode: Mode::Auto, target: 72, max_runs: 3, baseline: Some(28), gate: Gate::Worse, hook: true }, None, Theme::Cocoa);
        assert!(html.contains("value=72 required"), "the target is filled in");
        assert!(html.contains("value=auto checked"), "the mode is chosen");
        assert!(html.contains("<b>28%</b>"), "the baseline is shown");
        assert!(html.contains("value=worse checked"), "the gate is chosen");
        assert!(html.contains("name=hook value=on checked"), "the push check is on");
    }

    // The blossom theme swaps the colours and adds the blossom; cocoa stays Krypton's own.
    #[test]
    fn test_page_theme() {
        let settings = Settings { mode: Mode::Manual, target: 70, max_runs: 3, baseline: None, gate: Gate::Worse, hook: false };
        let blossom = page(&settings, None, Theme::Blossom);
        assert!(blossom.contains("--bg:#fcf0f3") && blossom.contains("<svg class=mark"), "pink, with the blossom");
        let cocoa = page(&settings, None, Theme::Cocoa);
        assert!(cocoa.contains("--bg:#1b1613") && !cocoa.contains("<svg class=mark"), "cocoa, no blossom");
        assert_eq!(
            [Theme::parse("blossom"), Theme::parse("cherry-blossom"), Theme::parse("pink")],
            [Some(Theme::Blossom), Some(Theme::Blossom), None],
            "names"
        );
    }

    // A spare connection that sends nothing is dropped after a short wait, so the request behind it is still answered.
    #[test]
    fn test_quiet_connection() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let _spare = std::net::TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (stream, _) = listener.accept().unwrap();
        let settings = Settings { mode: Mode::Manual, target: 70, max_runs: 3, baseline: None, gate: Gate::Worse, hook: false };
        let started = std::time::Instant::now();
        assert_eq!(answer(&stream, &mut Session::new(7777).unwrap(), &settings, Theme::Cocoa), None, "nothing saved");
        assert!(started.elapsed() < std::time::Duration::from_secs(5), "given up on after the quiet limit, not waited on forever");
    }

    // Two links are never the same, and each is 256 bits.
    #[test]
    fn test_secret() {
        let (one, two) = (secret().unwrap(), secret().unwrap());
        assert_ne!(one, two, "each link is new");
        assert!(one.len() == 64 && one.bytes().all(|byte| byte.is_ascii_hexdigit()), "256 bits in hex");
    }

    // Equal only when every byte is.
    #[test]
    fn test_same() {
        assert!(same(b"abc", b"abc") && !same(b"abc", b"abd") && !same(b"abc", b"ab") && !same(b"", b"a"));
    }

    // Only this computer, on the page's port.
    #[test]
    fn test_host() {
        assert!(local_host("localhost:7777", 7777) && local_host("127.0.0.1:7777", 7777) && local_host("[::1]:7777", 7777));
        assert!(local_host("LOCALHOST:7777", 7777), "host names are not case sensitive");
        assert!(!local_host("evil.com:7777", 7777) && !local_host("localhost:7778", 7777) && !local_host("localhost", 7777));
    }

    // The Save must name this page as its origin, or its Referer must; neither is refused.
    #[test]
    fn test_origin() {
        assert!(local_origin(Some("http://localhost:7777"), None, 7777), "Origin");
        assert!(local_origin(None, Some("http://127.0.0.1:7777/"), 7777), "Referer when there is no Origin");
        assert!(!local_origin(None, None, 7777), "neither");
        assert!(!local_origin(Some("https://evil.com"), Some("http://localhost:7777/"), 7777), "a foreign Origin wins over the Referer");
        assert!(!local_origin(Some("null"), None, 7777) && !local_origin(Some("https://localhost:7777"), None, 7777));
        assert_eq!(cookie_value(Some("a=1; krypton_setup=xyz"), "krypton_setup"), Some("xyz"));
    }

    /// Send one raw request to `answer` and return the response text and any saved settings.
    fn ask(session: &mut Session, raw: &str) -> (String, Option<Settings>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let mut client = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        client.write_all(raw.as_bytes()).unwrap();
        let (server, _) = listener.accept().unwrap();
        let settings = Settings { mode: Mode::Manual, target: 70, max_runs: 3, baseline: None, gate: Gate::Worse, hook: false };
        let saved = answer(&server, session, &settings, Theme::Cocoa);
        drop(server);
        let mut text = String::new();
        client.read_to_string(&mut text).unwrap();
        (text, saved)
    }

    fn status(text: &str) -> &str {
        text.split_whitespace().nth(1).unwrap()
    }

    /// A session whose link was opened: it and its `Cookie:` header line.
    fn signed_in() -> (Session, String) {
        let mut session = Session::new(7777).unwrap();
        let key = session.link().rsplit('/').next().unwrap().to_string();
        let (first, _) = ask(&mut session, &format!("GET /{key} HTTP/1.1\r\nHost: localhost:7777\r\n\r\n"));
        assert!(status(&first) == "302" && first.contains(&format!("Set-Cookie: krypton_setup={key}; HttpOnly; SameSite=Strict; Path=/")));
        (session, format!("Cookie: krypton_setup={key}\r\n"))
    }

    // The link is swapped for a cookie; no cookie, a wrong one or a foreign Host is refused.
    #[test]
    fn test_swap() {
        let (mut session, cookie) = signed_in();
        assert_eq!(status(&ask(&mut session, "GET / HTTP/1.1\r\nHost: localhost:7777\r\n\r\n").0), "403", "no cookie");
        assert_eq!(status(&ask(&mut session, "GET / HTTP/1.1\r\nHost: localhost:7777\r\nCookie: krypton_setup=wrong\r\n\r\n").0), "403", "wrong cookie");
        assert_eq!(status(&ask(&mut session, &format!("GET / HTTP/1.1\r\nHost: evil.com:7777\r\n{cookie}\r\n")).0), "421", "rebinding");
        assert_eq!(status(&ask(&mut session, &format!("GET / HTTP/1.1\r\nHost: localhost:7777\r\n{cookie}\r\n")).0), "200", "the page");
    }

    // A Save needs this page's Origin (or Referer); refusals leave the page serving, and then a real Save works.
    #[test]
    fn test_save_origin() {
        let (mut session, cookie) = signed_in();
        let form = "target=72&runs=4&mode=auto&gate=target&hook=on";
        let post = |origin: &str| format!("POST / HTTP/1.1\r\nHost: localhost:7777\r\n{cookie}{origin}Content-Length: {}\r\n\r\n{form}", form.len());
        for (origin, why) in [("Origin: https://evil.com\r\n", "foreign origin"), ("", "no origin or referer")] {
            let (text, saved) = ask(&mut session, &post(origin));
            assert!(status(&text) == "403" && saved.is_none(), "{why}");
        }
        assert_eq!(status(&ask(&mut session, &format!("GET / HTTP/1.1\r\nHost: localhost:7777\r\n{cookie}\r\n")).0), "200", "still serving");
        assert_eq!(ask(&mut session, &post("Origin: http://localhost:7777\r\n")).1.map(|saved| saved.target), Some(72), "saved");
    }

    // The link works once: opening it again is refused, while the cookie still works.
    #[test]
    fn test_link_once() {
        let mut session = Session::new(7777).unwrap();
        let key = session.link().rsplit('/').next().unwrap().to_string();
        let link = format!("GET /{key} HTTP/1.1\r\nHost: 127.0.0.1:7777\r\n\r\n");
        assert_eq!(status(&ask(&mut session, &link).0), "302");
        let again = ask(&mut session, &link).0;
        assert!(status(&again) == "403" && again.contains("already used"), "spent");
        assert_eq!(status(&ask(&mut session, &format!("GET / HTTP/1.1\r\nHost: 127.0.0.1:7777\r\nCookie: krypton_setup={key}\r\n\r\n")).0), "200");
    }
}
