//! The digest is part of the wire contract: the client and the server each carry the function, so both are pinned to these known values
//! (computed from the definition: sha256 of the whitespace-collapsed text, first 16 hex characters). The server's own test holds the same table.

use krypton_client::expression_digest;

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
