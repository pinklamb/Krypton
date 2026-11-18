//! The key that digests names for one run.
//!
//! Every `krypton scan` and `krypton wire` makes a fresh 256-bit key from the operating system's random source, keeps it in memory, and drops
//! it when the run ends. Nothing on the server or in the logs needs a digest to match one from another run: summaries, the run log and pull
//! request comments all use the real names, which this machine puts back. So the strongest choice costs nothing: two requests cannot even be
//! linked to each other. `KRYPTON_KEY` (64 hex characters) fixes the key instead, for byte-identical `krypton wire` output while debugging.

use std::env;

use krypton_client::NameKey;

use crate::Failure;

/// The key for this run: `KRYPTON_KEY` when it is set, otherwise a new random one.
///
/// # Errors
///
/// [`Failure::Usage`] when `KRYPTON_KEY` is set but is not 64 hex characters; [`Failure::Output`] when the operating system gives no randomness.
pub fn for_this_run() -> Result<NameKey, Failure> {
    if let Some(text) = env::var_os("KRYPTON_KEY") {
        let text = text.to_string_lossy();
        return NameKey::from_hex(&text).map_err(|error| Failure::Usage(format!("KRYPTON_KEY: {error}")));
    }
    let mut bytes = [0_u8; 32];
    getrandom::getrandom(&mut bytes).map_err(|error| Failure::Output(format!("no randomness for the name key: {error}")))?;
    Ok(NameKey::new(bytes))
}
