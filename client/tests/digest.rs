//! The digest is part of the wire contract: the client and the server each carry the function, so both are pinned to these known values
//! (computed from the definition: sha256 of the whitespace-collapsed text, first 16 hex characters). The server's own test holds the same table.

use krypton_client::{expression_digest, hex, hmac_sha256, NameKey, PUBLIC_WORDS};

const KNOWN: [(&str, &str); 7] = [
    ("foo", "2c26b46b68ffc68f"),
    ("  foo   bar \n baz ", "dbd318c1c462aee8"),
    ("self.items", "f2a283630adf0803"),
    ("a\u{00a0}b", "c8687a08aa5d6ed2"),
    ("tab\there", "4c4df4a5a8758766"),
    ("", "e3b0c44298fc1c14"),
    ("x\u{2003}y", "887fcea6a80333c6"),
];

// Digests match the contract.
#[test]
fn test_digest_contract() {
    for (text, expected) in KNOWN {
        assert_eq!(expression_digest(text), expected, "digest of {text:?}");
    }
}

// HMAC-SHA256 matches RFC 4231 test case 2 (a short key is padded with zeros, so "Jefe" and "Jefe" plus 28 zero bytes are the same key).
#[test]
fn test_hmac_vector() {
    let mut key = [0_u8; 32];
    key[..4].copy_from_slice(b"Jefe");
    assert_eq!(hex(&hmac_sha256(&key, b"what do ya want for nothing?")), "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843", "RFC 4231 case 2");
}

// A name's digest needs the key: it is not the plain hash, it differs between keys, and it is stable under one key.
#[test]
fn test_keyed_digest() {
    let (one, two) = (NameKey::new([1; 32]), NameKey::new([2; 32]));
    assert_ne!(one.digest("apply_discount"), expression_digest("apply_discount"), "not the guessable plain hash");
    assert_ne!(one.digest("apply_discount"), two.digest("apply_discount"), "another key, another digest");
    assert_eq!(one.digest("apply_discount"), one.digest("  apply_discount "), "equal text, equal digest");
    assert_eq!(one.digest("x").len(), 16, "16 hex characters");
}

// The framework words the server looks for keep the plain digest, so the server still recognises them.
#[test]
fn test_public_words() {
    let key = NameKey::new([1; 32]);
    for word in PUBLIC_WORDS.iter().chain(std::iter::once(&"onmessage")) {
        assert_eq!(key.digest(word), expression_digest(word), "{word} stays recognisable");
    }
}

// A key round-trips through hex, and anything but 64 hex characters is refused.
#[test]
fn test_key_hex() {
    let key = NameKey::new([171; 32]);
    assert_eq!(NameKey::from_hex(&key.to_hex()).map(|back| back.to_hex()), Ok(key.to_hex()), "round trip");
    assert!(NameKey::from_hex("abc").is_err() && NameKey::from_hex(&"g".repeat(64)).is_err(), "short or not hex is refused");
}

// Wiping a key zeroes it (it wipes itself the same way when dropped).
#[test]
fn test_key_wipe() {
    use zeroize::Zeroize;
    let mut key = NameKey::new([171; 32]);
    key.zeroize();
    assert_eq!(key.to_hex(), "0".repeat(64), "all zero after wiping");
}
