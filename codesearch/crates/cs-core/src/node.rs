//! What the index stores about a single symbol.
//!
//! [`Facts`] is the deliberately un-generated half of an explanation. Everything
//! in it is derived from the syntax tree, so it is either correct or absent — it
//! is never plausible-but-wrong. The UI renders it above the model's prose and
//! visually separates the two, so you always know which half you can trust
//! without checking.

use crate::{Confidence, EdgeKind, Language, NodeId, NodeKind, Span};
use serde::{Deserialize, Serialize};

/// A name written down somewhere, not yet connected to anything.
///
/// Lives in `cs-core` rather than in the parser because the index stores these
/// verbatim: resolution is global, so reindexing one file has to be able to
/// re-resolve against every other file's references without reparsing them.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RawReference {
    pub name: String,
    /// The `db` in `db.execute(...)`. Narrows candidates during resolution and
    /// is often the difference between a usable guess and a coin flip.
    pub receiver: Option<String>,
    pub span: Span,
    /// The symbol the reference sits inside, or the file node for top-level code.
    pub from: NodeId,
    pub kind: EdgeKind,
}

/// A local variable bound directly to a constructor call: `app = Flask(...)`.
///
/// This is the cheapest useful piece of type information there is, and it
/// unlocks a large share of real call graphs. In a typical web application the
/// majority of calls are `app.something()` or `client.something()`, where the
/// receiver is a variable, not a class. Without knowing what `app` is, every one
/// of those falls back to a global name guess.
///
/// Only *direct* constructor assignments count. `x = make_thing()` is not a
/// binding, because knowing the return type would need real inference — and a
/// wrong binding would confidently mis-route hundreds of edges, which is worse
/// than an honest guess.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RawBinding {
    pub variable: String,
    /// The name being called: `Flask` in `app = Flask(__name__)`.
    pub constructor: String,
    pub span: Span,
}

/// An import as written, before it is matched to a file.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RawImport {
    /// As written: `os.path`, `./utils`, `github.com/x/y`.
    pub module: String,
    /// The specific name imported, for `from x import y` shapes.
    pub symbol: Option<String>,
    pub alias: Option<String>,
    pub span: Span,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Visibility {
    Public,
    Protected,
    Private,
    /// Python's leading underscore, Go's lowercase initial — a convention rather
    /// than an enforced rule, but worth surfacing.
    Internal,
}

/// An externally observable thing a function does beyond returning a value.
///
/// This is the answer to "is it safe to call this to find out what it does?",
/// which is the question you actually have when exploring unfamiliar code.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SideEffect {
    FileRead,
    FileWrite,
    Network,
    Database,
    ProcessSpawn,
    GlobalWrite,
    /// Printing, logging — harmless, but explains a lot of output.
    Console,
    Randomness,
    /// Reads the clock; a common reason a function is not reproducible.
    Time,
    Environment,
}

impl SideEffect {
    pub fn label(self) -> &'static str {
        match self {
            SideEffect::FileRead => "liest Dateien",
            SideEffect::FileWrite => "schreibt Dateien",
            SideEffect::Network => "Netzwerkzugriff",
            SideEffect::Database => "Datenbankzugriff",
            SideEffect::ProcessSpawn => "startet Prozesse",
            SideEffect::GlobalWrite => "schreibt globalen Zustand",
            SideEffect::Console => "gibt etwas aus",
            SideEffect::Randomness => "nutzt Zufall",
            SideEffect::Time => "liest die Uhr",
            SideEffect::Environment => "liest Umgebungsvariablen",
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            SideEffect::FileRead => "file_read",
            SideEffect::FileWrite => "file_write",
            SideEffect::Network => "network",
            SideEffect::Database => "database",
            SideEffect::ProcessSpawn => "process_spawn",
            SideEffect::GlobalWrite => "global_write",
            SideEffect::Console => "console",
            SideEffect::Randomness => "randomness",
            SideEffect::Time => "time",
            SideEffect::Environment => "environment",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        Some(match s {
            "file_read" => SideEffect::FileRead,
            "file_write" => SideEffect::FileWrite,
            "network" => SideEffect::Network,
            "database" => SideEffect::Database,
            "process_spawn" => SideEffect::ProcessSpawn,
            "global_write" => SideEffect::GlobalWrite,
            "console" => SideEffect::Console,
            "randomness" => SideEffect::Randomness,
            "time" => SideEffect::Time,
            "environment" => SideEffect::Environment,
            _ => return None,
        })
    }
}

/// A parsed symbol as it comes out of the indexer.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Symbol {
    pub id: NodeId,
    pub kind: NodeKind,
    pub language: Language,
    /// The bare name, as written: `apply_discount`.
    pub name: String,
    /// Path within the file: `Invoice.apply_discount`. Used for ids and display.
    pub qualified: String,
    /// The whole definition, body included — this is what gets shown in a code
    /// card and what a citation may point into.
    pub span: Span,
    /// Just the signature line(s), for compact display without loading the body.
    pub signature_span: Span,
    /// Enclosing class or module node, when there is one.
    pub parent: Option<NodeId>,
    pub visibility: Visibility,
    /// Docstring / doc comment, already stripped of comment markers.
    pub doc: Option<String>,
}

/// Everything the index can state about a symbol without asking a model.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Facts {
    pub signature: String,
    pub params: Vec<Param>,
    pub returns: Option<String>,
    pub throws: Vec<String>,
    pub side_effects: Vec<SideEffect>,
    /// Cyclomatic complexity: decision points + 1.
    pub complexity: u32,
    /// Lines of the definition, comments and blanks included.
    pub loc: u32,
    /// Maximum nesting depth of blocks. A better readability signal than length.
    pub max_nesting: u32,
    pub callers: u32,
    pub callees: u32,
    /// Set when the symbol has no side effects and no calls to impure symbols.
    /// Absent rather than `false` when the analysis could not decide.
    pub pure: Option<bool>,
    /// Lowest confidence among the edges these counts are based on, so the UI can
    /// warn that "17 callers" is really "17 guesses".
    pub weakest_edge: Option<Confidence>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Param {
    pub name: String,
    pub type_hint: Option<String>,
    pub default: Option<String>,
    /// `*args` / `**kwargs` / rest parameters.
    pub variadic: bool,
}

impl Facts {
    /// A one-line summary built entirely from computed fields — the fallback
    /// shown before (or instead of) any model output.
    pub fn headline(&self) -> String {
        let mut parts = Vec::new();
        parts.push(match self.params.len() {
            0 => "ohne Parameter".to_string(),
            1 => "1 Parameter".to_string(),
            n => format!("{n} Parameter"),
        });
        if let Some(ret) = &self.returns {
            parts.push(format!("gibt {ret} zurück"));
        }
        if self.side_effects.is_empty() {
            if self.pure == Some(true) {
                parts.push("ohne Seiteneffekte".to_string());
            }
        } else {
            let effects: Vec<_> = self.side_effects.iter().map(|e| e.label()).collect();
            parts.push(effects.join(", "));
        }
        parts.join(" · ")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn headline_reports_purity_only_when_it_was_actually_decided() {
        let undecided = Facts { pure: None, ..Default::default() };
        assert!(!undecided.headline().contains("Seiteneffekte"));

        let decided = Facts { pure: Some(true), ..Default::default() };
        assert!(decided.headline().contains("ohne Seiteneffekte"));
    }

    #[test]
    fn headline_lists_effects_when_present() {
        let facts = Facts {
            side_effects: vec![SideEffect::Database, SideEffect::Network],
            ..Default::default()
        };
        let line = facts.headline();
        assert!(line.contains("Datenbankzugriff"));
        assert!(line.contains("Netzwerkzugriff"));
    }
}
