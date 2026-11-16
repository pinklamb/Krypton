//! The one network call the client makes to Krypton: POST a JSON body (gzipped) to the server and read its JSON answer. The server has no
//! accounts or keys; it rate limits by address and stores nothing.

use std::env;
use std::io::Write;
use std::time::Duration;

use flate2::write::GzEncoder;
use flate2::Compression;
use serde_json::Value;

use crate::Failure;

const DEFAULT_SERVER: &str = "http://localhost:8080";
const TIMEOUT: Duration = Duration::from_secs(300);
/// A body bigger than this is preceded by a tiny request, because a server that refuses a request (rate limit) does so before it reads the body,
/// and the client would only see a broken connection.
const PREFLIGHT_OVER: usize = 64 * 1024;

/// Where to send requests.
pub struct Server {
    url: String,
}

impl Server {
    /// The `--server` address, else `KRYPTON_SERVER`, else the local default.
    #[must_use]
    pub fn from_flag(flag: Option<&str>) -> Self {
        let url = flag.map(ToString::to_string).or_else(|| env::var("KRYPTON_SERVER").ok()).unwrap_or_else(|| DEFAULT_SERVER.to_string());
        Self { url: url.trim_end_matches('/').to_string() }
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
        let mut encoder = GzEncoder::new(Vec::new(), Compression::fast());
        encoder.write_all(body).map_err(|error| Failure::Server(format!("cannot compress the request: {error}")))?;
        let packed = encoder.finish().map_err(|error| Failure::Server(format!("cannot compress the request: {error}")))?;
        let request = ureq::post(&format!("{}{path}", self.url)).timeout(TIMEOUT).set("Content-Type", "application/json").set("Content-Encoding", "gzip");
        let reply = request.send_bytes(&packed).map_err(|error| self.failure(error))?;
        let text = reply.into_string().map_err(|error| Failure::Server(format!("cannot read the answer: {error}")))?;
        serde_json::from_str(&text).map_err(|error| Failure::Server(format!("the answer was not JSON: {error}")))
    }

    /// Ask for the server's capabilities: it answers at once, and says clearly when this address is rate limited.
    fn preflight(&self) -> Result<(), Failure> {
        ureq::get(&format!("{}/v1/capabilities", self.url)).timeout(Duration::from_secs(30)).call().map(drop).map_err(|error| self.failure(error))
    }

    /// What a failed call means to the person running the command.
    fn failure(&self, error: ureq::Error) -> Failure {
        match error {
            ureq::Error::Status(code, reply) => {
                let text = reply.into_string().unwrap_or_default();
                Failure::Server(format!("the server refused the request ({code}): {}", text.chars().take(600).collect::<String>()))
            }
            ureq::Error::Transport(problem) => Failure::Server(format!("could not reach {}: {problem}. Is the server running?", self.url)),
        }
    }
}
