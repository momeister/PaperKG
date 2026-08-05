//! Language packs: grammar, tag query and manifest, one per language.
//!
//! Packs are embedded at build time. Adding a language means dropping a
//! `tags.scm` and a `manifest.toml` under `languages/` and adding one line to
//! [`pack_source`] — no changes to the parser, the resolver, the ranking or the
//! UI, all of which are written against the capture names rather than against
//! any particular grammar.
//!
//! A tag query that names a node kind the grammar does not have fails to compile
//! *as a whole*, silently costing you every symbol in that language. That failure
//! mode is why [`tests::every_language_pack_compiles`] exists and why it asserts
//! on captures rather than only on `Query::new` succeeding.

use anyhow::{Context, Result};
use cs_core::{Language, SideEffect};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::OnceLock;
use tree_sitter::Query;

/// Static per-language configuration, read from `manifest.toml`.
#[derive(Debug, Clone, Deserialize)]
pub struct Manifest {
    pub display: String,
    pub extensions: Vec<String>,
    pub comment_prefixes: Vec<String>,
    pub doc_style: DocStyle,
    /// Node kinds that introduce a branch. Cyclomatic complexity counts these.
    pub decision_nodes: Vec<String>,
    /// Node kinds that open a scope, used to measure nesting depth.
    pub block_nodes: Vec<String>,
    /// Node kinds that contribute a name prefix without being symbols of their
    /// own — Rust's `impl` blocks, C++ `namespace`, Go's method receivers.
    ///
    /// Without these, every method in a Rust codebase is called `new`, `build`
    /// or `as_str`, which makes search useless and turns almost every call into
    /// an ambiguous guess.
    #[serde(default)]
    pub qualifier_nodes: Vec<String>,
    #[serde(default)]
    pub test_prefixes: Vec<String>,
    #[serde(default)]
    pub test_suffixes: Vec<String>,
    #[serde(default)]
    pub entry_names: Vec<String>,
    #[serde(default)]
    pub effects: EffectPatterns,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum DocStyle {
    /// Doc comment sits above the definition (C-family, Go, Rust).
    LeadingComment,
    /// Doc is the first string expression inside the body (Python).
    StringFirst,
}

/// Substrings matched against the text of a call site to infer side effects.
///
/// This is a heuristic and is labelled as such in the UI. It is checked against
/// the *call text*, so `db.execute(...)` matches `execute` regardless of what
/// `db` turns out to be — a deliberate trade: over-reporting "touches the
/// database" is a small annoyance, missing it while you debug a data bug is not.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct EffectPatterns {
    #[serde(default)]
    pub file_read: Vec<String>,
    #[serde(default)]
    pub file_write: Vec<String>,
    #[serde(default)]
    pub network: Vec<String>,
    #[serde(default)]
    pub database: Vec<String>,
    #[serde(default)]
    pub process_spawn: Vec<String>,
    #[serde(default)]
    pub console: Vec<String>,
    #[serde(default)]
    pub randomness: Vec<String>,
    #[serde(default)]
    pub time: Vec<String>,
    #[serde(default)]
    pub environment: Vec<String>,
}

impl EffectPatterns {
    /// Every effect whose patterns appear in `text`.
    pub fn detect(&self, text: &str) -> Vec<SideEffect> {
        let groups: [(&Vec<String>, SideEffect); 9] = [
            (&self.file_read, SideEffect::FileRead),
            (&self.file_write, SideEffect::FileWrite),
            (&self.network, SideEffect::Network),
            (&self.database, SideEffect::Database),
            (&self.process_spawn, SideEffect::ProcessSpawn),
            (&self.console, SideEffect::Console),
            (&self.randomness, SideEffect::Randomness),
            (&self.time, SideEffect::Time),
            (&self.environment, SideEffect::Environment),
        ];
        groups
            .iter()
            .filter(|(patterns, _)| patterns.iter().any(|p| text.contains(p.as_str())))
            .map(|(_, effect)| *effect)
            .collect()
    }
}

/// Capture indices resolved once, so the hot parse loop compares integers rather
/// than strings.
#[derive(Debug, Clone, Default)]
pub struct Captures {
    pub name: Option<u32>,
    pub receiver: Option<u32>,
    pub def_function: Option<u32>,
    pub def_class: Option<u32>,
    pub def_interface: Option<u32>,
    pub def_field: Option<u32>,
    pub def_module: Option<u32>,
    pub ref_call: Option<u32>,
    pub ref_type: Option<u32>,
    pub import: Option<u32>,
    pub import_module: Option<u32>,
    pub import_symbol: Option<u32>,
    pub import_alias: Option<u32>,
}

impl Captures {
    fn resolve(query: &Query) -> Self {
        let index = |name: &str| query.capture_index_for_name(name);
        Self {
            name: index("name"),
            receiver: index("recv"),
            def_function: index("def.function"),
            def_class: index("def.class"),
            def_interface: index("def.interface"),
            def_field: index("def.field"),
            def_module: index("def.module"),
            ref_call: index("ref.call"),
            ref_type: index("ref.type"),
            import: index("import"),
            import_module: index("import.module"),
            import_symbol: index("import.symbol"),
            import_alias: index("import.alias"),
        }
    }
}

pub struct LanguagePack {
    pub language: Language,
    pub ts_language: tree_sitter::Language,
    pub query: Query,
    pub manifest: Manifest,
    pub captures: Captures,
}

impl LanguagePack {
    /// True when a symbol name looks like a test, by the manifest's convention.
    pub fn looks_like_test(&self, name: &str, path: &str) -> bool {
        self.manifest.test_prefixes.iter().any(|p| name.starts_with(p.as_str()))
            || self.manifest.test_suffixes.iter().any(|s| name.ends_with(s.as_str()))
            || self.manifest.test_suffixes.iter().any(|s| path.contains(s.as_str()))
            || path.contains("/test/")
            || path.contains("/tests/")
            || path.contains("__tests__")
    }

    pub fn is_entry_point(&self, name: &str) -> bool {
        self.manifest.entry_names.iter().any(|e| e == name)
    }
}

/// The grammar and pack files for one language.
///
/// TSX shares TypeScript's tag query: the grammars differ only in JSX, and JSX
/// elements are not symbols.
fn pack_source(language: Language) -> (tree_sitter::Language, &'static str, &'static str) {
    use Language::*;
    match language {
        Python => (
            tree_sitter_python::LANGUAGE.into(),
            include_str!("../../../languages/python/tags.scm"),
            include_str!("../../../languages/python/manifest.toml"),
        ),
        JavaScript => (
            tree_sitter_javascript::LANGUAGE.into(),
            include_str!("../../../languages/javascript/tags.scm"),
            include_str!("../../../languages/javascript/manifest.toml"),
        ),
        TypeScript => (
            tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
            include_str!("../../../languages/typescript/tags.scm"),
            include_str!("../../../languages/typescript/manifest.toml"),
        ),
        Tsx => (
            tree_sitter_typescript::LANGUAGE_TSX.into(),
            include_str!("../../../languages/typescript/tags.scm"),
            include_str!("../../../languages/tsx/manifest.toml"),
        ),
        Go => (
            tree_sitter_go::LANGUAGE.into(),
            include_str!("../../../languages/go/tags.scm"),
            include_str!("../../../languages/go/manifest.toml"),
        ),
        Rust => (
            tree_sitter_rust::LANGUAGE.into(),
            include_str!("../../../languages/rust/tags.scm"),
            include_str!("../../../languages/rust/manifest.toml"),
        ),
        Java => (
            tree_sitter_java::LANGUAGE.into(),
            include_str!("../../../languages/java/tags.scm"),
            include_str!("../../../languages/java/manifest.toml"),
        ),
        C => (
            tree_sitter_c::LANGUAGE.into(),
            include_str!("../../../languages/c/tags.scm"),
            include_str!("../../../languages/c/manifest.toml"),
        ),
        Cpp => (
            tree_sitter_cpp::LANGUAGE.into(),
            include_str!("../../../languages/cpp/tags.scm"),
            include_str!("../../../languages/cpp/manifest.toml"),
        ),
        CSharp => (
            tree_sitter_c_sharp::LANGUAGE.into(),
            include_str!("../../../languages/csharp/tags.scm"),
            include_str!("../../../languages/csharp/manifest.toml"),
        ),
        Ruby => (
            tree_sitter_ruby::LANGUAGE.into(),
            include_str!("../../../languages/ruby/tags.scm"),
            include_str!("../../../languages/ruby/manifest.toml"),
        ),
        Php => (
            tree_sitter_php::LANGUAGE_PHP.into(),
            include_str!("../../../languages/php/tags.scm"),
            include_str!("../../../languages/php/manifest.toml"),
        ),
        Bash => (
            tree_sitter_bash::LANGUAGE.into(),
            include_str!("../../../languages/bash/tags.scm"),
            include_str!("../../../languages/bash/manifest.toml"),
        ),
    }
}

fn build(language: Language) -> Result<LanguagePack> {
    let (ts_language, tags, manifest_src) = pack_source(language);

    let query = Query::new(&ts_language, tags).with_context(|| {
        format!(
            "compiling the tag query for {}. A node kind in tags.scm does not exist in this \
             grammar version — the error offset points at the offending pattern.",
            language.display_name()
        )
    })?;

    let manifest: Manifest = toml::from_str(manifest_src)
        .with_context(|| format!("parsing manifest.toml for {}", language.display_name()))?;

    let captures = Captures::resolve(&query);
    Ok(LanguagePack { language, ts_language, query, manifest, captures })
}

/// All packs that compiled, built once on first use.
///
/// A language whose pack fails to build is logged and left out rather than
/// crashing the app: losing Ruby support is bad, refusing to open the workspace
/// at all is worse.
pub fn registry() -> &'static HashMap<Language, LanguagePack> {
    static REGISTRY: OnceLock<HashMap<Language, LanguagePack>> = OnceLock::new();
    REGISTRY.get_or_init(|| {
        let mut map = HashMap::new();
        for &language in Language::ALL {
            match build(language) {
                Ok(pack) => {
                    map.insert(language, pack);
                }
                Err(err) => {
                    tracing::error!(
                        language = language.display_name(),
                        error = format!("{err:#}"),
                        "language pack unavailable, files of this language will not be parsed"
                    );
                }
            }
        }
        map
    })
}

pub fn pack(language: Language) -> Option<&'static LanguagePack> {
    registry().get(&language)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The guard rail for every language pack.
    ///
    /// A grammar bump that renames a node makes the query silently useless in
    /// production; here it fails the build instead. Asserting that the essential
    /// captures resolved catches the subtler version, where the query compiles
    /// but the capture names were misspelled and nothing is ever extracted.
    #[test]
    fn every_language_pack_compiles() {
        let mut failures = Vec::new();

        for &language in Language::ALL {
            match build(language) {
                Err(err) => failures.push(format!("{}: {err:#}", language.display_name())),
                Ok(pack) => {
                    if pack.captures.name.is_none() {
                        failures.push(format!("{}: no @name capture", language.display_name()));
                    }
                    if pack.captures.def_function.is_none() {
                        failures
                            .push(format!("{}: no @def.function capture", language.display_name()));
                    }
                    if pack.captures.ref_call.is_none() {
                        failures.push(format!("{}: no @ref.call capture", language.display_name()));
                    }
                    if pack.manifest.decision_nodes.is_empty() {
                        failures.push(format!(
                            "{}: manifest lists no decision nodes, complexity would always be 1",
                            language.display_name()
                        ));
                    }
                }
            }
        }

        assert!(failures.is_empty(), "language packs broken:\n  {}", failures.join("\n  "));
    }

    #[test]
    fn extensions_map_back_to_the_language_that_claims_them() {
        for &language in Language::ALL {
            let Ok(pack) = build(language) else { continue };
            for ext in &pack.manifest.extensions {
                assert_eq!(
                    Language::from_extension(ext),
                    Some(language),
                    "{} claims .{ext} in its manifest but from_extension disagrees",
                    language.display_name()
                );
            }
        }
    }

    #[test]
    fn effect_detection_reads_the_call_text() {
        let pack = build(Language::Python).unwrap();
        let effects = pack.manifest.effects.detect("requests.get(url)");
        assert!(effects.contains(&SideEffect::Network));
        assert!(pack.manifest.effects.detect("x + 1").is_empty());
    }
}
