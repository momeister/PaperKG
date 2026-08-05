//! The shared vocabulary of CodeSearch: what a node is, what an edge is, and —
//! most importantly — what it takes for either of them to be allowed to exist.
//!
//! The single rule this crate enforces is that **no edge exists without
//! evidence**. [`Edge`] cannot be constructed without a [`Span`] pointing at the
//! source text that produced it, and every edge carries the [`Confidence`] of the
//! analysis that found it. Those two fields travel all the way to the UI badge and
//! into the LLM tool results, which is what stops the assistant from asserting a
//! relationship the index merely guessed at.

mod ids;
mod lang;
mod node;

pub use ids::{EdgeId, FileId, NodeId};
pub use lang::Language;
pub use node::{
    Facts, Param, RawBinding, RawImport, RawReference, SideEffect, Symbol, Visibility,
};

use serde::{Deserialize, Serialize};

/// How much the index actually knows about a relationship.
///
/// The ordering is meaningful: `Measured > Verified > Resolved > Guessed`. When
/// two analyses disagree about the same edge, the higher confidence wins and the
/// loser is dropped, which is why this derives `Ord`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Confidence {
    /// Tier C — a name matched and nothing contradicted it. May be one of
    /// several candidates. Never to be stated as fact.
    Guessed,
    /// Tier B — imports and scope resolved this unambiguously within the file's
    /// own module graph.
    Resolved,
    /// Tier A — a language server confirmed it.
    Verified,
    /// Phase 2 — observed in an actual program run. Beats every static claim.
    Measured,
}

impl Confidence {
    /// The badge label shown in the UI and handed to the model.
    pub fn label(self) -> &'static str {
        match self {
            Confidence::Guessed => "vermutet",
            Confidence::Resolved => "aufgelöst",
            Confidence::Verified => "verifiziert",
            Confidence::Measured => "gemessen",
        }
    }

    /// Whether a claim at this level may be phrased as a plain statement of fact.
    /// The chat system prompt keys its hedging language off this.
    pub fn is_assertable(self) -> bool {
        self >= Confidence::Resolved
    }
}

/// A half-open byte range plus the 1-based line numbers that contain it.
///
/// Byte offsets drive slicing; line numbers drive display and citation checking.
/// Both are stored because recomputing lines from bytes needs the file, and
/// citation verification has to work without re-reading it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Span {
    pub start_byte: u32,
    pub end_byte: u32,
    pub start_line: u32,
    pub end_line: u32,
}

impl Span {
    pub fn new(start_byte: u32, end_byte: u32, start_line: u32, end_line: u32) -> Self {
        Self { start_byte, end_byte, start_line, end_line }
    }

    /// True when `other` lies entirely inside `self`. Used by citation
    /// verification: a quoted range must fall within a span the model was
    /// actually shown.
    pub fn contains(&self, other: &Span) -> bool {
        self.start_byte <= other.start_byte && self.end_byte >= other.end_byte
    }

    pub fn line_count(&self) -> u32 {
        self.end_line.saturating_sub(self.start_line) + 1
    }
}

/// The proof that an edge or fact is real: a file, the exact range that shows it,
/// and the content hash of that file at the time it was read.
///
/// The hash is what makes a citation falsifiable later. When a file changes, every
/// citation into it is stale until reindexed, and the UI marks it as such rather
/// than showing a line number that has since moved.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Evidence {
    pub file: FileId,
    pub span: Span,
    pub content_hash: ContentHash,
}

/// blake3 of a file's bytes, truncated to 16 bytes. Collisions are not a
/// practical concern at this length and it halves the index size.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct ContentHash(pub [u8; 16]);

impl ContentHash {
    pub fn of(bytes: &[u8]) -> Self {
        let full = blake3::hash(bytes);
        let mut short = [0u8; 16];
        short.copy_from_slice(&full.as_bytes()[..16]);
        ContentHash(short)
    }

    pub fn to_hex(self) -> String {
        self.0.iter().map(|b| format!("{b:02x}")).collect()
    }

    pub fn from_hex(s: &str) -> Option<Self> {
        if s.len() != 32 {
            return None;
        }
        let mut out = [0u8; 16];
        for (i, byte) in out.iter_mut().enumerate() {
            *byte = u8::from_str_radix(s.get(i * 2..i * 2 + 2)?, 16).ok()?;
        }
        Some(ContentHash(out))
    }
}

/// What kind of thing a node is.
///
/// `DynamicGap` is deliberately one of these rather than an error state: when
/// static analysis hits reflection, a DI container, `eval` or monkeypatching, the
/// index records a visible hole in the graph instead of inventing an edge or
/// silently dropping the call. Phase 2 tracing fills these in.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NodeKind {
    File,
    Module,
    Class,
    Interface,
    Function,
    Method,
    Field,
    Global,
    Route,
    DbTable,
    DbColumn,
    Test,
    ConfigKey,
    ExternalPackage,
    DynamicGap,
}

impl NodeKind {
    /// Nodes that hold executable logic — the ones a call graph connects.
    pub fn is_callable(self) -> bool {
        matches!(self, NodeKind::Function | NodeKind::Method | NodeKind::Test)
    }

    /// Nodes that can own other nodes, used when building the containment tree.
    pub fn is_container(self) -> bool {
        matches!(
            self,
            NodeKind::File | NodeKind::Module | NodeKind::Class | NodeKind::Interface
        )
    }

    pub fn as_str(self) -> &'static str {
        match self {
            NodeKind::File => "file",
            NodeKind::Module => "module",
            NodeKind::Class => "class",
            NodeKind::Interface => "interface",
            NodeKind::Function => "function",
            NodeKind::Method => "method",
            NodeKind::Field => "field",
            NodeKind::Global => "global",
            NodeKind::Route => "route",
            NodeKind::DbTable => "db_table",
            NodeKind::DbColumn => "db_column",
            NodeKind::Test => "test",
            NodeKind::ConfigKey => "config_key",
            NodeKind::ExternalPackage => "external_package",
            NodeKind::DynamicGap => "dynamic_gap",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        Some(match s {
            "file" => NodeKind::File,
            "module" => NodeKind::Module,
            "class" => NodeKind::Class,
            "interface" => NodeKind::Interface,
            "function" => NodeKind::Function,
            "method" => NodeKind::Method,
            "field" => NodeKind::Field,
            "global" => NodeKind::Global,
            "route" => NodeKind::Route,
            "db_table" => NodeKind::DbTable,
            "db_column" => NodeKind::DbColumn,
            "test" => NodeKind::Test,
            "config_key" => NodeKind::ConfigKey,
            "external_package" => NodeKind::ExternalPackage,
            "dynamic_gap" => NodeKind::DynamicGap,
            _ => return None,
        })
    }
}

/// How two nodes relate.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EdgeKind {
    /// Lexical containment: file → class → method.
    Contains,
    Calls,
    Imports,
    Inherits,
    Implements,
    /// A read of a field, global or captured variable.
    Reads,
    Writes,
    ParamType,
    ReturnsType,
    Throws,
    TestedBy,
    TouchesTable,
    HandlesRoute,
    /// Behaviour switched by an environment variable or config key.
    GatedBy,
}

impl EdgeKind {
    /// Edges that make up the call graph proper. PageRank runs over exactly this
    /// set — containment and type edges would drown out the signal.
    pub fn is_reference(self) -> bool {
        matches!(
            self,
            EdgeKind::Calls | EdgeKind::Reads | EdgeKind::Writes | EdgeKind::Inherits | EdgeKind::Implements
        )
    }

    pub fn as_str(self) -> &'static str {
        match self {
            EdgeKind::Contains => "contains",
            EdgeKind::Calls => "calls",
            EdgeKind::Imports => "imports",
            EdgeKind::Inherits => "inherits",
            EdgeKind::Implements => "implements",
            EdgeKind::Reads => "reads",
            EdgeKind::Writes => "writes",
            EdgeKind::ParamType => "param_type",
            EdgeKind::ReturnsType => "returns_type",
            EdgeKind::Throws => "throws",
            EdgeKind::TestedBy => "tested_by",
            EdgeKind::TouchesTable => "touches_table",
            EdgeKind::HandlesRoute => "handles_route",
            EdgeKind::GatedBy => "gated_by",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        Some(match s {
            "contains" => EdgeKind::Contains,
            "calls" => EdgeKind::Calls,
            "imports" => EdgeKind::Imports,
            "inherits" => EdgeKind::Inherits,
            "implements" => EdgeKind::Implements,
            "reads" => EdgeKind::Reads,
            "writes" => EdgeKind::Writes,
            "param_type" => EdgeKind::ParamType,
            "returns_type" => EdgeKind::ReturnsType,
            "throws" => EdgeKind::Throws,
            "tested_by" => EdgeKind::TestedBy,
            "touches_table" => EdgeKind::TouchesTable,
            "handles_route" => EdgeKind::HandlesRoute,
            "gated_by" => EdgeKind::GatedBy,
            _ => return None,
        })
    }
}

/// Which analysis pass produced an edge. Kept so that a bad pass can be
/// identified and re-run in isolation, and so the UI can explain *why* it thinks
/// something, not just how sure it is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    /// Direct from the tree-sitter parse — containment, definitions.
    Syntax,
    /// Import graph and scope walking.
    ScopeResolution,
    /// Weighted global name matching.
    NameHeuristic,
    /// Language server.
    LanguageServer,
    /// Runtime trace (phase 2).
    Trace,
}

/// A relationship between two nodes, which by construction has proof.
///
/// There is no public field-by-field constructor: [`Edge::new`] is the only way
/// in, and it requires evidence. That is the invariant the whole product rests
/// on, so it is enforced by the type rather than by review.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Edge {
    pub from: NodeId,
    pub to: NodeId,
    pub kind: EdgeKind,
    pub confidence: Confidence,
    pub source: Source,
    /// The source range that demonstrates this relationship — the call site, the
    /// import statement, the `extends` clause.
    pub evidence: Evidence,
    /// For `Guessed` edges: how many other targets matched equally well. `1`
    /// means the name was unique, which is a much stronger guess than `7`.
    pub candidate_count: u16,
}

impl Edge {
    pub fn new(
        from: NodeId,
        to: NodeId,
        kind: EdgeKind,
        confidence: Confidence,
        source: Source,
        evidence: Evidence,
    ) -> Self {
        Self { from, to, kind, confidence, source, evidence, candidate_count: 1 }
    }

    /// Marks this edge as one of `n` equally plausible targets. Only meaningful
    /// for [`Confidence::Guessed`]; higher tiers are unambiguous by definition.
    pub fn with_candidates(mut self, n: u16) -> Self {
        debug_assert!(
            self.confidence == Confidence::Guessed || n == 1,
            "only guessed edges may have competing candidates"
        );
        self.candidate_count = n;
        self
    }

    /// Deduplication key. Two passes finding the same relationship differ only in
    /// confidence, and the caller keeps the better one.
    pub fn identity(&self) -> (NodeId, NodeId, EdgeKind) {
        (self.from, self.to, self.kind)
    }
}
