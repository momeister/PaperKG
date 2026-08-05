//! Identity that survives reindexing.
//!
//! Node ids are derived from *what a symbol is* — its file, kind and qualified
//! name — never from a row number or a byte offset. Adding a blank line at the
//! top of a file must not invalidate the note you attached to a function, nor
//! break a saved trail. Everything that points at code from outside the index
//! (bookmarks, trails, explanation cache, LLM citations) points at these.

use serde::de::{Error as DeError, Unexpected, Visitor};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::fmt;

macro_rules! hash_id {
    ($name:ident, $doc:literal) => {
        #[doc = $doc]
        #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
        pub struct $name(pub u64);

        /// Ids cross JSON as the 16-character hex string, never as a number.
        ///
        /// These are 64-bit hashes, and a JavaScript number silently drops the
        /// low bits of anything above 2^53 — which for a hash-derived id means
        /// pointing at a different symbol, with no error anywhere. The hex form
        /// is what `Display` has always produced and what every consumer already
        /// expects.
        impl Serialize for $name {
            fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
                serializer.serialize_str(&format!("{:016x}", self.0))
            }
        }

        /// Accepts the hex string *and* a bare integer.
        ///
        /// The integer arm keeps older payloads and hand-written test fixtures
        /// working; nothing produces them any more.
        impl<'de> Deserialize<'de> for $name {
            fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
                struct IdVisitor;

                impl<'v> Visitor<'v> for IdVisitor {
                    type Value = u64;

                    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                        f.write_str("a 16-character hex id or an unsigned integer")
                    }

                    fn visit_str<E: DeError>(self, value: &str) -> Result<u64, E> {
                        u64::from_str_radix(value.trim_start_matches("0x"), 16)
                            .map_err(|_| E::invalid_value(Unexpected::Str(value), &self))
                    }

                    fn visit_u64<E: DeError>(self, value: u64) -> Result<u64, E> {
                        Ok(value)
                    }

                    fn visit_i64<E: DeError>(self, value: i64) -> Result<u64, E> {
                        Ok(value as u64)
                    }
                }

                deserializer.deserialize_any(IdVisitor).map($name)
            }
        }

        impl $name {
            pub fn raw(self) -> u64 {
                self.0
            }

            /// SQLite stores integers as signed 64-bit, so ids round-trip through
            /// a bitwise reinterpretation rather than a lossy cast.
            pub fn to_sqlite(self) -> i64 {
                self.0 as i64
            }

            pub fn from_sqlite(v: i64) -> Self {
                Self(v as u64)
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, "{:016x}", self.0)
            }
        }
    };
}

hash_id!(NodeId, "Stable identity of a symbol, derived from its qualified name.");
hash_id!(FileId, "Stable identity of a file, derived from its workspace-relative path.");
hash_id!(EdgeId, "Stable identity of an edge, derived from its endpoints and kind.");

/// Hashes a list of parts with a domain tag, so a file and a node that happen to
/// share a string can never collide.
fn derive(domain: &str, parts: &[&str]) -> u64 {
    let mut hasher = blake3::Hasher::new();
    hasher.update(domain.as_bytes());
    for part in parts {
        // Length-prefixing keeps ("ab", "c") distinct from ("a", "bc").
        hasher.update(&(part.len() as u32).to_le_bytes());
        hasher.update(part.as_bytes());
    }
    let bytes = hasher.finalize();
    u64::from_le_bytes(bytes.as_bytes()[..8].try_into().expect("blake3 yields 32 bytes"))
}

impl FileId {
    /// `path` must be workspace-relative with forward slashes. Callers normalise
    /// before getting here; see `cs_index::paths::normalise`.
    pub fn of_path(path: &str) -> Self {
        FileId(derive("file", &[path]))
    }
}

impl NodeId {
    /// `qualified` is the language-specific dotted path within the file, e.g.
    /// `Invoice.apply_discount`. Together with the file it is unique; the same
    /// name in another file is a different node, which is exactly what we want.
    pub fn of_symbol(file: FileId, kind: &str, qualified: &str) -> Self {
        NodeId(derive("node", &[&file.to_string(), kind, qualified]))
    }

    /// Nodes that exist outside the workspace — an imported third-party package.
    /// These have no file of their own, so the ecosystem namespaces them.
    pub fn of_external(ecosystem: &str, name: &str) -> Self {
        NodeId(derive("external", &[ecosystem, name]))
    }

    /// A hole in the graph where static analysis cannot see the target. Keyed by
    /// the call site so that repeated indexing yields the same gap node.
    pub fn of_gap(file: FileId, line: u32, expression: &str) -> Self {
        NodeId(derive("gap", &[&file.to_string(), &line.to_string(), expression]))
    }
}

impl EdgeId {
    pub fn of(from: NodeId, to: NodeId, kind: &str) -> Self {
        EdgeId(derive("edge", &[&from.to_string(), &to.to_string(), kind]))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_stable_across_runs() {
        let a = FileId::of_path("src/pricing.py");
        let b = FileId::of_path("src/pricing.py");
        assert_eq!(a, b, "the same path must always produce the same id");
    }

    #[test]
    fn length_prefixing_prevents_concatenation_collisions() {
        let file = FileId::of_path("a.py");
        let joined = NodeId::of_symbol(file, "function", "ab");
        let split = NodeId::of_symbol(file, "functiona", "b");
        assert_ne!(joined, split);
    }

    #[test]
    fn same_name_in_different_files_is_a_different_node() {
        let one = NodeId::of_symbol(FileId::of_path("a.py"), "function", "process");
        let two = NodeId::of_symbol(FileId::of_path("b.py"), "function", "process");
        assert_ne!(one, two);
    }

    #[test]
    fn ids_survive_the_sqlite_round_trip() {
        // Ids near u64::MAX become negative i64 values; the reinterpretation has
        // to be lossless or half the index would corrupt on reload.
        let id = NodeId(u64::MAX - 3);
        assert_eq!(NodeId::from_sqlite(id.to_sqlite()), id);
    }

    #[test]
    fn ids_cross_json_as_hex_strings() {
        // The whole point: an id above 2^53 must not reach a JSON consumer as a
        // number, because JavaScript would round it into a different symbol.
        let id = NodeId(u64::MAX - 3);
        let json = serde_json::to_string(&id).expect("serialisable");
        assert_eq!(json, "\"fffffffffffffffc\"");
        assert_eq!(serde_json::from_str::<NodeId>(&json).expect("round trip"), id);
    }

    #[test]
    fn a_bare_integer_is_still_accepted() {
        assert_eq!(serde_json::from_str::<NodeId>("42").expect("legacy form"), NodeId(42));
    }
}
