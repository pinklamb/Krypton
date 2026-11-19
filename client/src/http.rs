//! The client's calls to Krypton: POST a JSON body (gzipped) to the server and read its JSON answer. Scoring carries the Krypton token from
//! `krypton login` (see `login_command.rs`); the server also rate limits by address and stores nothing.

use std::env;
use std::io::Write;
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

/// The address to use: the flag, else a non-empty `KRYPTON_SERVER`, else the hosted server.
fn address(flag: Option<&str>, environment: Option<String>) -> String {
    let named = |text: &str| !text.trim().is_empty();
    flag.filter(|text| named(text)).map(ToString::to_string).or_else(|| environment.filter(|text| named(text))).unwrap_or_else(|| DEFAULT_SERVER.to_string())
}

/// Where to send requests.
pub struct Server {
    url: String,
}

impl Server {
    /// The `--server` address, else a non-empty `KRYPTON_SERVER`, else the hosted server.
    #[must_use]
    pub fn from_flag(flag: Option<&str>) -> Self {
        Self { url: address(flag, env::var("KRYPTON_SERVER").ok()).trim_end_matches('/').to_string() }
    }

    /// POST `body` (compressed here) to `path` and return the server's JSON answer.
    ///
    /// # Errors
    ///
    /// [`Failure::Server`] when the server cannot be reached, refuses the request (its message is shown) or answers with something that is not JSON.
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
    /// [`Failure::Server`] when the server cannot be reached or answers with something that is not JSON.
    pub fn get(&self, path: &str) -> Result<Value, Failure> {
        let reply = ureq::get(&format!("{}{path}", self.url)).timeout(Duration::from_secs(30)).call().map_err(|error| self.failure(error))?;
        json_of(reply, "the server")
    }

    /// The server's address, as tokens are saved under it.
    #[must_use]
    pub fn url(&self) -> &str {
        &self.url
    }

    fn send(&self, path: &str, body: &[u8], token: Option<&str>) -> Result<Value, Failure> {
        let mut encoder = GzEncoder::new(Vec::new(), Compression::fast());
        encoder.write_all(body).map_err(|error| Failure::Server(format!("cannot compress the request: {error}")))?;
        let packed = encoder.finish().map_err(|error| Failure::Server(format!("cannot compress the request: {error}")))?;
        let mut request = ureq::post(&format!("{}{path}", self.url)).timeout(TIMEOUT).set("Content-Type", "application/json").set("Content-Encoding", "gzip");
        if let Some(token) = token {
            request = request.set("Authorization", &format!("Bearer {token}"));
        }
        let reply = request.send_bytes(&packed).map_err(|error| self.failure(error))?;
        let text = reply.into_string().map_err(|error| Failure::Server(format!("cannot read the answer: {error}")))?;
        serde_json::from_str(&text).map_err(|error| Failure::Server(format!("the answer was not JSON: {error}")))
    }

    /// Ask for the server's capabilities: it answers at once, and says clearly when this login (or address) is rate limited. The login is
    /// sent when there is one, so the server counts it against the login, like the scoring that follows, not against a shared address.
    fn preflight(&self) -> Result<(), Failure> {
        let mut request = ureq::get(&format!("{}/v1/capabilities", self.url)).timeout(Duration::from_secs(30));
        if let Some(token) = crate::login_command::token_for(&self.url)? {
            request = request.set("Authorization", &format!("Bearer {token}"));
        }
        request.call().map(drop).map_err(|error| self.failure(error))
    }

    /// What a failed call means to the person running the command.
    fn failure(&self, error: ureq::Error) -> Failure {
        match error {
            ureq::Error::Status(401, reply) if reply.header("content-type").is_some_and(|kind| kind.contains("json")) => {
                let text = reply.into_string().unwrap_or_default();
                if text.contains("login_required") {
                    return Failure::Server(format!("{} needs you to log in: run `krypton login`", self.url));
                }
                Failure::Server(format!("the server refused the login (401): {}", text.chars().take(600).collect::<String>()))
            }
            ureq::Error::Status(code, reply) => {
                let text = reply.into_string().unwrap_or_default();
                Failure::Server(format!("the server refused the request ({code}): {}", text.chars().take(600).collect::<String>()))
            }
            ureq::Error::Transport(problem) => Failure::Server(format!("could not reach {}: {problem}. Is the server running?", self.url)),
        }
    }
}

/// A reply's body as JSON; `who` names the sender in the error.
///
/// # Errors
///
/// [`Failure::Server`] when the body cannot be read or is not JSON.
pub fn json_of(reply: ureq::Response, who: &str) -> Result<Value, Failure> {
    let text = reply.into_string().map_err(|error| Failure::Server(format!("cannot read {who}'s answer: {error}")))?;
    serde_json::from_str(&text).map_err(|error| Failure::Server(format!("{who}'s answer was not JSON: {error}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    // The flag wins, then the environment, and an empty setting counts as none.
    #[test]
    fn test_address_order() {
        assert_eq!(address(Some("http://a"), Some("http://b".to_string())), "http://a", "the flag wins");
        assert_eq!(address(None, Some("http://b".to_string())), "http://b", "then the environment");
        assert_eq!(address(None, Some("  ".to_string())), DEFAULT_SERVER, "a blank environment variable is not an address");
        assert_eq!(address(None, None), DEFAULT_SERVER, "nothing set means the hosted server");
    }
}
