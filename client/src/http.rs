//! The client's calls to Krypton: POST a JSON body (gzipped) to the server and read its JSON answer. Scoring carries the Krypton token from
//! `krypton login` (see `login_command.rs`); the server also rate limits by address and stores nothing.
//!
//! A server that is busy (429, 503), restarting (502, 504) or out of reach is tried again, waiting as long as its `Retry-After` asks (else
//! a little longer each time), up to [`TRIES`] times in all. Only then does the command stop, with a failure that says which it was, so a
//! busy minute never fails a pull request.

use std::env;
use std::io::Write;
use std::thread;
use std::time::Duration;

use flate2::write::GzEncoder;
use flate2::Compression;
use serde_json::Value;

use crate::Failure;

/// The hosted Krypton server, used unless `--server` or `KRYPTON_SERVER` names another (a private deployment, a test server).
const DEFAULT_SERVER: &str = "https://krypton-prodserver-production.up.railway.app";
const TIMEOUT: Duration = Duration::from_secs(300);
/// A body bigger than this is preceded by a tiny request, because a server that refuses a request (rate limit) does so before it reads the body,
/// and the client would only see a broken connection.
const PREFLIGHT_OVER: usize = 64 * 1024;
/// How many times in all a busy or unreachable server is tried.
const TRIES: u32 = 4;
/// The longest single wait, in seconds, whatever `Retry-After` asks.
const LONGEST_WAIT: u64 = 60;

/// The wait before try `next` (2, 3, ...) when the server named none: a little longer each time.
const fn backoff(next: u32) -> u64 {
    match next {
        0..=2 => 2,
        3 => 5,
        _ => 15,
    }
}

/// The wait in seconds before trying again, or None when trying again cannot help (the server refused, or failed on this request).
fn retry_wait(error: &ureq::Error, next: u32) -> Option<u64> {
    match error {
        ureq::Error::Status(429 | 503, reply) => {
            let asked = reply.header("retry-after").and_then(|value| value.trim().parse::<u64>().ok());
            Some(asked.unwrap_or_else(|| backoff(next)).clamp(1, LONGEST_WAIT))
        }
        ureq::Error::Status(502 | 504, _) => Some(backoff(next)),
        ureq::Error::Transport(problem) => {
            matches!(problem.kind(), ureq::ErrorKind::Dns | ureq::ErrorKind::ConnectionFailed | ureq::ErrorKind::Io).then(|| backoff(next))
        }
        ureq::Error::Status(..) => None,
    }
}

/// The address to use: the flag, else a non-empty `KRYPTON_SERVER`, else the hosted server.
fn address(flag: Option<&str>, environment: Option<String>) -> String {
    let named = |text: &str| !text.trim().is_empty();
    flag.filter(|text| named(text)).map(ToString::to_string).or_else(|| environment.filter(|text| named(text))).unwrap_or_else(|| DEFAULT_SERVER.to_string())
}

/// Hosts plain `http://` may go to: this computer only. `host.docker.internal` is this computer as seen from inside the Docker container
/// the client runs in.
const LOCAL_HOSTS: [&str; 4] = ["localhost", "127.0.0.1", "[::1]", "host.docker.internal"];

/// The host of an address after its scheme: no user, no port, no path, lower case.
fn host_of(rest: &str) -> String {
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    let host_port = authority.rsplit('@').next().unwrap_or(authority);
    let host =
        if host_port.starts_with('[') { host_port.split_inclusive(']').next().unwrap_or(host_port) } else { host_port.split(':').next().unwrap_or(host_port) };
    host.to_ascii_lowercase()
}

/// Refuse an address that would send the Krypton token unencrypted over a network: only `https://`, or plain `http://` to this computer.
///
/// # Errors
///
/// [`Failure::Usage`] naming the address and what to use instead.
fn secure(url: &str) -> Result<(), Failure> {
    let lower = url.to_ascii_lowercase();
    if lower.starts_with("https://") {
        return Ok(());
    }
    if let Some(rest) = lower.strip_prefix("http://") {
        if LOCAL_HOSTS.contains(&host_of(rest).as_str()) {
            return Ok(());
        }
        return Err(Failure::Usage(format!(
            "refusing to send your Krypton token to {url} over plain http, unencrypted; use https:// (plain http is only for localhost and 127.0.0.1)"
        )));
    }
    Err(Failure::Usage(format!("the Krypton server address must start with https:// (got {url})")))
}

/// Where to send requests.
pub struct Server {
    url: String,
}

impl Server {
    /// The `--server` address, else a non-empty `KRYPTON_SERVER`, else the hosted server; refused unless it is https (or plain http to this
    /// computer), so a token never crosses a network unencrypted.
    ///
    /// # Errors
    ///
    /// [`Failure::Usage`] for an address that is not `https://`, or `http://` to a host other than this computer.
    pub fn from_flag(flag: Option<&str>) -> Result<Self, Failure> {
        let url = address(flag, env::var("KRYPTON_SERVER").ok()).trim().trim_end_matches('/').to_string();
        secure(&url)?;
        Ok(Self { url })
    }

    /// POST `body` (compressed here) to `path` and return the server's JSON answer.
    ///
    /// # Errors
    ///
    /// [`Failure::Unreachable`] or [`Failure::Busy`] when the server still cannot be reached or is still busy after [`TRIES`] tries,
    /// [`Failure::Refused`] when it refuses the request (its message is shown), [`Failure::Server`] when it fails or answers with something
    /// that is not JSON.
    pub fn post(&self, path: &str, body: &[u8]) -> Result<Value, Failure> {
        if body.len() > PREFLIGHT_OVER {
            self.preflight()?;
        }
        self.send(path, body, crate::login_command::token_for(&self.url)?.as_deref())
    }

    /// POST a small JSON value without a Krypton token: the login exchanges, which are how a token is earned.
    ///
    /// # Errors
    ///
    /// As [`Server::post`].
    pub fn post_anonymous(&self, path: &str, value: &Value) -> Result<Value, Failure> {
        self.send(path, &serde_json::to_vec(value).map_err(|error| Failure::Output(error.to_string()))?, None)
    }

    /// GET a JSON answer (the server's capabilities).
    ///
    /// # Errors
    ///
    /// As [`Server::post`].
    pub fn get(&self, path: &str) -> Result<Value, Failure> {
        let reply = self.with_retries(|| ureq::get(&format!("{}{path}", self.url)).timeout(Duration::from_secs(30)).call().map_err(Box::new))?;
        json_of(reply, "the server")
    }

    /// The server's address, as tokens are saved under it.
    #[must_use]
    pub fn url(&self) -> &str {
        &self.url
    }

    fn send(&self, path: &str, body: &[u8], token: Option<&str>) -> Result<Value, Failure> {
        let mut encoder = GzEncoder::new(Vec::new(), Compression::fast());
        encoder.write_all(body).map_err(|error| Failure::Output(format!("cannot compress the request: {error}")))?;
        let packed = encoder.finish().map_err(|error| Failure::Output(format!("cannot compress the request: {error}")))?;
        let reply = self.with_retries(|| {
            let mut request =
                ureq::post(&format!("{}{path}", self.url)).timeout(TIMEOUT).set("Content-Type", "application/json").set("Content-Encoding", "gzip");
            if let Some(token) = token {
                request = request.set("Authorization", &format!("Bearer {token}"));
            }
            request.send_bytes(&packed).map_err(Box::new)
        })?;
        json_of(reply, "the Krypton server")
    }

    /// Ask for the server's capabilities: it answers at once, and says clearly when this login (or address) is rate limited. The login is
    /// sent when there is one, so the server counts it against the login, like the scoring that follows, not against a shared address.
    fn preflight(&self) -> Result<(), Failure> {
        let token = crate::login_command::token_for(&self.url)?;
        self.with_retries(|| {
            let mut request = ureq::get(&format!("{}/v1/capabilities", self.url)).timeout(Duration::from_secs(30));
            if let Some(token) = &token {
                request = request.set("Authorization", &format!("Bearer {token}"));
            }
            request.call().map_err(Box::new)
        })
        .map(drop)
    }

    /// Make the call, and while the server is busy or out of reach, wait as it asks and try again, up to [`TRIES`] times in all.
    fn with_retries(&self, mut call: impl FnMut() -> Result<ureq::Response, Box<ureq::Error>>) -> Result<ureq::Response, Failure> {
        let mut tried: u32 = 1;
        loop {
            let error = match call() {
                Ok(reply) => return Ok(reply),
                Err(error) => error,
            };
            let next = tried.saturating_add(1);
            let wait = retry_wait(&error, next).filter(|_| tried < TRIES);
            let failure = self.failure(*error);
            let Some(seconds) = wait else { return Err(failure) };
            eprintln!("{}; trying again in {seconds} s (try {next} of {TRIES})", first_line(&failure));
            thread::sleep(Duration::from_secs(seconds));
            tried = next;
        }
    }

    /// What a failed call means to the person running the command.
    fn failure(&self, error: ureq::Error) -> Failure {
        match error {
            ureq::Error::Status(401, reply) if reply.header("content-type").is_some_and(|kind| kind.contains("json")) => {
                let text = reply.into_string().unwrap_or_default();
                if text.contains("login_required") {
                    return Failure::Refused(format!("{} needs you to log in: run `krypton login`", self.url));
                }
                Failure::Refused(format!("the Krypton server refused the login (401): {}", text.chars().take(600).collect::<String>()))
            }
            ureq::Error::Status(code, reply) => {
                let text = reply.into_string().unwrap_or_default().chars().take(600).collect::<String>();
                match code {
                    429 | 503 => Failure::Busy(format!("the Krypton server is busy ({code}): {text}")),
                    500..=599 => Failure::Server(format!("the Krypton server failed ({code}): {text}")),
                    _ => Failure::Refused(format!("the Krypton server refused the request ({code}): {text}")),
                }
            }
            ureq::Error::Transport(problem) => Failure::Unreachable(format!("could not reach {}: {problem}", self.url)),
        }
    }
}

/// The failure's message, for the line that says the server is being tried again.
fn first_line(failure: &Failure) -> String {
    let message = match failure {
        Failure::Busy(message) | Failure::Unreachable(message) | Failure::Server(message) | Failure::Refused(message) => message.as_str(),
        _ => "the Krypton server did not answer",
    };
    message.lines().next().unwrap_or_default().chars().take(160).collect()
}

/// A reply's body as JSON; `who` names the sender in the error.
///
/// # Errors
///
/// [`Failure::Unreachable`] when the connection broke while the body was read, [`Failure::Server`] when it is not JSON.
pub fn json_of(reply: ureq::Response, who: &str) -> Result<Value, Failure> {
    let text = reply.into_string().map_err(|error| Failure::Unreachable(format!("the connection to {who} broke while its answer was read: {error}")))?;
    serde_json::from_str(&text).map_err(|error| Failure::Server(format!("{who}'s answer was not JSON: {error}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn status(code: u16, retry_after: Option<&str>) -> ureq::Error {
        let header = retry_after.map_or_else(String::new, |seconds| format!("Retry-After: {seconds}\r\n"));
        let raw = format!("HTTP/1.1 {code} X\r\n{header}Content-Length: 0\r\n\r\n");
        ureq::Error::Status(code, raw.parse::<ureq::Response>().unwrap())
    }

    // A busy server is waited for as long as it asks (within reason), a restarting one a little longer each time, and a refusal or a
    // failure on this request is never tried again.
    #[test]
    fn test_retry_wait() {
        assert_eq!(retry_wait(&status(429, Some("12")), 2), Some(12), "as long as Retry-After asks");
        assert_eq!(retry_wait(&status(429, Some("900")), 2), Some(LONGEST_WAIT), "never longer than a minute at once");
        assert_eq!(retry_wait(&status(503, None), 3), Some(5), "busy without Retry-After: backing off");
        assert_eq!(retry_wait(&status(502, None), 4), Some(15), "a restarting server too");
        assert_eq!([retry_wait(&status(401, None), 2), retry_wait(&status(422, None), 2), retry_wait(&status(500, None), 2)], [None, None, None], "no retry");
    }

    // Each answer is the failure it means: busy, failed, refused or a login to do, each with its own exit code.
    #[test]
    fn test_failure_kinds() {
        let server = Server { url: "https://k.example".to_string() };
        let kind = |code: u16| match server.failure(status(code, None)) {
            Failure::Busy(_) => "busy",
            Failure::Server(_) => "failed",
            Failure::Refused(_) => "refused",
            _ => "other",
        };
        let kinds: Vec<&str> = [429, 503, 500, 413, 426].into_iter().map(kind).collect();
        assert_eq!(kinds, ["busy", "busy", "failed", "refused", "refused"], "rate limited and overloaded are busy; too large and old are refused");
    }

    // The flag wins, then the environment, and an empty setting counts as none.
    #[test]
    fn test_address_order() {
        assert_eq!(address(Some("http://a"), Some("http://b".to_string())), "http://a", "the flag wins");
        assert_eq!(address(None, Some("http://b".to_string())), "http://b", "then the environment");
        assert_eq!(address(None, Some("  ".to_string())), DEFAULT_SERVER, "a blank environment variable is not an address");
        assert_eq!(address(None, None), DEFAULT_SERVER, "nothing set means the hosted server");
    }

    // Tokens go only over https, or plain http to this computer; anything else is refused before a request is made.
    #[test]
    fn test_secure() {
        let fine = [
            "https://krypton.example",
            "HTTPS://k.example/x",
            "http://localhost:8080",
            "http://127.0.0.1",
            "http://[::1]:9/v1",
            "http://host.docker.internal:8080",
        ];
        assert!(fine.iter().all(|url| secure(url).is_ok()), "https and this computer");
        let refused =
            ["http://krypton.example", "http://localhost.evil.com", "http://127.0.0.1.evil.com", "http://user@evil.com", "ftp://x", "krypton.example"];
        assert!(refused.iter().all(|url| secure(url).is_err()), "plain http elsewhere, other schemes, no scheme");
        assert!(matches!(secure("http://evil.com"), Err(Failure::Usage(text)) if text.contains("unencrypted")), "says why");
    }
}
