//! The indexing pipeline: scan, parse, resolve, rank.
//!
//! This is the only place that knows the order of operations, which keeps the
//! layers below it independently testable. The stages are:
//!
//! 1. **Scan** — walk the tree, classify every file, decide what to read.
//! 2. **Parse** — in parallel, only for files whose content hash changed.
//! 3. **Resolve** — globally, from stored references. One changed file does not
//!    mean reparsing the workspace, but it *does* mean re-resolving it: a new
//!    definition can change what a name in an untouched file refers to.
//! 4. **History and rank** — git signals, then PageRank and the relevance score.
//!
//! Progress is reported per stage so the UI can show what is happening instead of
//! an indeterminate spinner. Indexing a large repository takes long enough that
//! silence reads as a hang.

use anyhow::{Context, Result};
use cs_core::{ContentHash, Edge, EdgeKind, Evidence, FileId, Language, NodeId, NodeKind, Span, Symbol, Visibility};
use cs_graph::{FileRecord, Graph};
use cs_index::parse::{parse_file, ParsedFile};
use cs_index::registry::pack;
use cs_index::walk::{self, Candidate, Skip};
use rayon::prelude::*;
use serde::Serialize;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub mod serve;

/// Files parsed and written per transaction.
///
/// One transaction per file spends most of its time in fsync; one transaction
/// for everything holds the whole workspace in memory. A few hundred is the flat
/// part of that curve.
const CHUNK: usize = 256;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Scanning,
    Parsing,
    Resolving,
    History,
    Ranking,
    Done,
}

impl Phase {
    /// Shown verbatim in the progress bar.
    pub fn label(self) -> &'static str {
        match self {
            Phase::Scanning => "Dateien durchsuchen",
            Phase::Parsing => "Code lesen",
            Phase::Resolving => "Verbindungen auflösen",
            Phase::History => "Historie auswerten",
            Phase::Ranking => "Relevanz berechnen",
            Phase::Done => "fertig",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct Progress {
    pub phase: Phase,
    pub done: usize,
    pub total: usize,
    /// The file currently being worked on, when there is one.
    pub detail: String,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct IndexReport {
    pub files_seen: usize,
    /// Files actually reparsed. On a warm index after one edit this is 1.
    pub files_parsed: usize,
    /// Files whose content hash was unchanged, so their parse was reused.
    pub files_reused: usize,
    pub files_removed: usize,
    /// Skip counts by reason, so the UI can show honest coverage.
    pub skipped: HashMap<String, usize>,
    pub duration_ms: u128,
    pub commits_walked: usize,
    pub history_truncated: bool,
}

pub struct Workspace {
    root: PathBuf,
    graph: Graph,
}

impl Workspace {
    /// Opens a workspace, putting its index under `.codesearch/index.csdb`.
    ///
    /// The index lives inside the workspace so that deleting the project takes
    /// its index with it, and so several checkouts of the same repository do not
    /// fight over one database.
    pub fn open(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref().canonicalize().with_context(|| {
            format!("resolving workspace root {}", root.as_ref().display())
        })?;
        let db_path = root.join(".codesearch").join("index.csdb");
        let graph = Graph::open(&root, &db_path)?;
        Ok(Self { root, graph })
    }

    /// Opens a workspace whose index lives somewhere else entirely.
    ///
    /// An embedder that indexes repositories it does not own has no business
    /// writing a `.codesearch/` directory into them — the index is its cache, not
    /// the repository's data. `Graph::open` always accepted an arbitrary path;
    /// only [`Workspace::open`] hardcoded the location.
    pub fn open_with_db(root: impl AsRef<Path>, db_path: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref().canonicalize().with_context(|| {
            format!("resolving workspace root {}", root.as_ref().display())
        })?;
        let db_path = db_path.as_ref();
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("creating index directory {}", parent.display()))?;
        }
        let graph = Graph::open(&root, db_path)?;
        Ok(Self { root, graph })
    }

    pub fn graph(&self) -> &Graph {
        &self.graph
    }

    pub fn graph_mut(&mut self) -> &mut Graph {
        &mut self.graph
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Runs the whole pipeline. Safe to call repeatedly; only what changed is
    /// reparsed.
    pub fn index(&mut self, on_progress: &(dyn Fn(Progress) + Sync)) -> Result<IndexReport> {
        let started = std::time::Instant::now();
        let mut report = IndexReport::default();

        on_progress(Progress {
            phase: Phase::Scanning,
            done: 0,
            total: 0,
            detail: String::new(),
        });

        let candidates = walk::scan(&self.root);
        report.files_seen = candidates.len();

        let known: HashMap<String, ContentHash> = self
            .graph
            .known_files()?
            .into_iter()
            .map(|(path, hash, _mtime)| (path, hash))
            .collect();

        let total = candidates.len();
        let mut processed = 0usize;

        for chunk in candidates.chunks(CHUNK) {
            let outcomes: Vec<Outcome> = chunk
                .par_iter()
                .map(|candidate| process_one(candidate, &known))
                .collect();

            let batch = self.graph.write_batch()?;
            for outcome in outcomes {
                match outcome {
                    Outcome::Unchanged => report.files_reused += 1,
                    Outcome::Unreadable => {}
                    Outcome::Skipped(record) => {
                        *report.skipped.entry(record.skip_label.clone()).or_default() += 1;
                        batch.put_file(&record.file)?;
                        if let Some(text) = &record.searchable_text {
                            batch.index_text(record.file.id, text)?;
                        }
                    }
                    Outcome::Parsed(parsed) => {
                        report.files_parsed += 1;
                        batch.put_file(&parsed.file)?;
                        batch.index_text(parsed.file.id, &parsed.text)?;

                        for symbol in &parsed.symbols {
                            batch.put_symbol(parsed.file.id, &parsed.file.path, symbol)?;
                        }
                        for reference in &parsed.parsed.references {
                            batch.put_reference(parsed.file.id, reference)?;
                        }
                        for import in &parsed.parsed.imports {
                            batch.put_import(parsed.file.id, import)?;
                        }
                        for binding in &parsed.parsed.bindings {
                            batch.put_binding(parsed.file.id, binding)?;
                        }
                        for (node, facts) in &parsed.parsed.facts {
                            batch.put_facts(*node, facts)?;
                        }
                        // Containment: the file owns its top-level symbols.
                        for symbol in &parsed.symbols {
                            if symbol.parent.is_none() && symbol.kind != NodeKind::File {
                                batch.put_edge(&Edge::new(
                                    parsed.file_node,
                                    symbol.id,
                                    EdgeKind::Contains,
                                    cs_core::Confidence::Resolved,
                                    cs_core::Source::Syntax,
                                    Evidence {
                                        file: parsed.file.id,
                                        span: symbol.signature_span,
                                        content_hash: parsed.file.hash,
                                    },
                                ))?;
                            }
                        }
                    }
                }
            }
            batch.commit()?;

            processed += chunk.len();
            on_progress(Progress {
                phase: Phase::Parsing,
                done: processed,
                total,
                detail: chunk.last().map(|c| c.rel_path.clone()).unwrap_or_default(),
            });
        }

        let existing: Vec<String> = candidates.iter().map(|c| c.rel_path.clone()).collect();
        report.files_removed = self.graph.prune_missing(&existing)?;

        on_progress(Progress {
            phase: Phase::Resolving,
            done: 0,
            total: 0,
            detail: String::new(),
        });
        let entry_points = self.resolve_all()?;

        on_progress(Progress { phase: Phase::History, done: 0, total: 0, detail: String::new() });
        let history = cs_git::collect(&self.root).unwrap_or_default();
        report.commits_walked = history.commits_walked;
        report.history_truncated = history.truncated;

        on_progress(Progress { phase: Phase::Ranking, done: 0, total: 0, detail: String::new() });
        cs_rank::apply(&mut self.graph, &history, &entry_points)?;

        report.duration_ms = started.elapsed().as_millis();
        on_progress(Progress {
            phase: Phase::Done,
            done: total,
            total,
            detail: String::new(),
        });
        Ok(report)
    }

    /// Re-resolves the whole workspace from stored references and returns the
    /// entry points it found along the way.
    fn resolve_all(&mut self) -> Result<Vec<NodeId>> {
        let stored = self.graph.stored_facts()?;

        let mut entry_points = Vec::new();
        for file in &stored {
            let Some(pack) = pack(file.language) else { continue };
            for symbol in &file.symbols {
                if symbol.kind.is_callable() && pack.is_entry_point(&symbol.name) {
                    entry_points.push(symbol.id);
                }
                // Tests are entry points too: they are how most code is actually
                // reached, and treating them as unreachable would mark half a
                // well-tested codebase as dead.
                if symbol.kind == NodeKind::Test {
                    entry_points.push(symbol.id);
                }
            }
        }

        let facts: Vec<cs_resolve::FileFacts> = stored
            .into_iter()
            .map(|f| cs_resolve::FileFacts {
                file: f.file,
                path: f.path,
                language: f.language,
                hash: f.hash,
                symbols: f.symbols,
                references: f.references,
                imports: f.imports,
                bindings: f.bindings,
            })
            .collect();

        let resolved = cs_resolve::resolve(&facts);
        let by_id: HashMap<FileId, (&str, Language)> =
            facts.iter().map(|f| (f.file, (f.path.as_str(), f.language))).collect();

        let batch = self.graph.write_batch()?;
        batch.clear_resolved_edges()?;

        // External packages and dynamic gaps need node rows before any edge can
        // reference them.
        for (id, name, language) in &resolved.externals {
            batch.put_symbol(
                // Externals belong to no file; they are hung off the first file
                // that imports them so the foreign key holds.
                external_host(&facts),
                "",
                &Symbol {
                    id: *id,
                    kind: NodeKind::ExternalPackage,
                    language: *language,
                    name: name.clone(),
                    qualified: name.clone(),
                    span: Span::new(0, 0, 1, 1),
                    signature_span: Span::new(0, 0, 1, 1),
                    parent: None,
                    visibility: Visibility::Public,
                    doc: None,
                },
            )?;
        }

        for (id, gap) in &resolved.gaps {
            let (_, language) = by_id.get(&gap.file).copied().unwrap_or(("", Language::Python));
            batch.put_symbol(
                gap.file,
                &gap.path,
                &Symbol {
                    id: *id,
                    kind: NodeKind::DynamicGap,
                    language,
                    name: gap.construct.clone(),
                    qualified: format!("{}:{} {}", gap.path, gap.line, gap.construct),
                    span: Span::new(0, 0, gap.line, gap.line),
                    signature_span: Span::new(0, 0, gap.line, gap.line),
                    parent: None,
                    visibility: Visibility::Public,
                    doc: Some(
                        "Statische Analyse kann hier nicht weiter — das Ziel steht erst zur \
                         Laufzeit fest."
                            .to_string(),
                    ),
                },
            )?;
        }

        for edge in &resolved.edges {
            batch.put_edge(edge)?;
        }
        batch.commit()?;

        entry_points.sort();
        entry_points.dedup();
        Ok(entry_points)
    }
}

/// External package nodes have no file of their own but the schema requires one.
/// Any indexed file works as a host; the first keeps it deterministic.
fn external_host(facts: &[cs_resolve::FileFacts]) -> FileId {
    facts.first().map(|f| f.file).unwrap_or(FileId::of_path(""))
}

enum Outcome {
    Unchanged,
    Unreadable,
    Skipped(SkippedFile),
    Parsed(Box<ParsedOutcome>),
}

struct SkippedFile {
    file: FileRecord,
    skip_label: String,
    /// Set for readable text we cannot parse — a README is not navigable but is
    /// very much searchable, and often holds the answer.
    searchable_text: Option<String>,
}

struct ParsedOutcome {
    file: FileRecord,
    file_node: NodeId,
    /// The file's own node plus every symbol in it.
    symbols: Vec<Symbol>,
    parsed: ParsedFile,
    text: String,
}

fn process_one(candidate: &Candidate, known: &HashMap<String, ContentHash>) -> Outcome {
    let id = FileId::of_path(&candidate.rel_path);

    // Files we already decided not to parse still need a row, but re-reading
    // them on every run would be pointless work.
    if let Some(skip) = candidate.skip {
        if skip == Skip::TooLarge || skip == Skip::Vendored {
            return Outcome::Skipped(SkippedFile {
                file: FileRecord {
                    id,
                    path: candidate.rel_path.clone(),
                    lang: candidate.language,
                    hash: ContentHash::of(candidate.rel_path.as_bytes()),
                    size: candidate.size,
                    lines: 0,
                    mtime: candidate.mtime,
                    parsed: false,
                    skip_reason: Some(skip.as_str().to_string()),
                },
                skip_label: skip.label().to_string(),
                searchable_text: None,
            });
        }
    }

    let Ok(bytes) = std::fs::read(&candidate.abs_path) else {
        return Outcome::Unreadable;
    };
    let hash = ContentHash::of(&bytes);

    // The hash check, not the mtime, is what keeps `git checkout` from
    // triggering a full reparse of files whose contents never changed.
    if known.get(&candidate.rel_path) == Some(&hash) {
        return Outcome::Unchanged;
    }

    let text = String::from_utf8_lossy(&bytes).into_owned();
    let lines = text.lines().count() as u32;

    let record = |parsed: bool, skip_reason: Option<String>| FileRecord {
        id,
        path: candidate.rel_path.clone(),
        lang: candidate.language,
        hash,
        size: candidate.size,
        lines,
        mtime: candidate.mtime,
        parsed,
        skip_reason,
    };

    let content_skip = walk::inspect_contents(&text);
    let skip = candidate.skip.or(content_skip);

    if let Some(skip) = skip {
        return Outcome::Skipped(SkippedFile {
            file: record(false, Some(skip.as_str().to_string())),
            skip_label: skip.label().to_string(),
            searchable_text: (skip != Skip::Binary).then(|| text.clone()),
        });
    }

    let Some(language) = candidate.language else {
        return Outcome::Skipped(SkippedFile {
            file: record(false, Some(Skip::Unsupported.as_str().to_string())),
            skip_label: Skip::Unsupported.label().to_string(),
            searchable_text: Some(text),
        });
    };

    let Some(language_pack) = pack(language) else {
        return Outcome::Skipped(SkippedFile {
            file: record(false, Some(Skip::Unsupported.as_str().to_string())),
            skip_label: Skip::Unsupported.label().to_string(),
            searchable_text: Some(text),
        });
    };

    match parse_file(language_pack, id, &candidate.rel_path, &text) {
        Ok(parsed) => {
            let file_symbol =
                cs_resolve::file_symbol(id, &candidate.rel_path, language, lines);
            let file_node = file_symbol.id;
            let mut symbols = Vec::with_capacity(parsed.symbols.len() + 1);
            symbols.push(file_symbol);
            symbols.extend(parsed.symbols.iter().cloned());

            Outcome::Parsed(Box::new(ParsedOutcome {
                file: record(true, None),
                file_node,
                symbols,
                parsed,
                text,
            }))
        }
        Err(err) => {
            tracing::warn!(path = %candidate.rel_path, error = %err, "parse failed");
            Outcome::Skipped(SkippedFile {
                file: record(false, Some("parse_error".to_string())),
                skip_label: "Parserfehler".to_string(),
                searchable_text: Some(text),
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use cs_graph::Direction;

    fn fixture() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::create_dir_all(root.join("src")).unwrap();

        std::fs::write(
            root.join("src/pricing.py"),
            "\"\"\"Preisberechnung.\"\"\"\n\
             \n\
             TAX = 0.19\n\
             \n\
             def apply_discount(amount, pct):\n\
             \x20   \"\"\"Zieht einen Rabatt ab.\"\"\"\n\
             \x20   return amount * (1 - pct)\n\
             \n\
             def total(amount, pct):\n\
             \x20   net = apply_discount(amount, pct)\n\
             \x20   return net * (1 + TAX)\n",
        )
        .unwrap();

        std::fs::write(
            root.join("src/main.py"),
            "from pricing import total\n\
             \n\
             def main():\n\
             \x20   print(total(100, 0.1))\n",
        )
        .unwrap();

        std::fs::write(root.join("README.md"), "# Demo\nEin Beispielprojekt.\n").unwrap();
        dir
    }

    fn index(dir: &tempfile::TempDir) -> (Workspace, IndexReport) {
        let mut workspace = Workspace::open(dir.path()).unwrap();
        let report = workspace.index(&|_| {}).unwrap();
        (workspace, report)
    }

    #[test]
    fn indexing_finds_symbols_and_connects_them() {
        let dir = fixture();
        let (workspace, report) = index(&dir);

        assert_eq!(report.files_parsed, 2, "two python files, the README is not parsed");

        let hits = workspace.graph().search_symbols("apply_discount", &[], 10).unwrap();
        assert_eq!(hits.len(), 1);

        let callers = workspace
            .graph()
            .neighbours(hits[0].id, Direction::In, &[EdgeKind::Calls])
            .unwrap();
        assert!(
            callers.iter().any(|c| c.node.name == "total"),
            "total() calls apply_discount() and the graph should say so"
        );
    }

    #[test]
    fn containment_survives_re_resolution() {
        // Containment comes from the parse, not from resolution. Clearing it
        // along with resolved edges emptied the Blueprint's "enthält" list on
        // every index run — silently, because nothing errors when a list is
        // empty.
        let dir = fixture();
        let (mut workspace, _) = index(&dir);
        workspace.index(&|_| {}).unwrap();

        let file = workspace
            .graph()
            .search_symbols("pricing.py", &[cs_core::NodeKind::File], 1)
            .unwrap();
        assert_eq!(file.len(), 1);

        let children = workspace
            .graph()
            .neighbours(file[0].id, Direction::Out, &[EdgeKind::Contains])
            .unwrap();
        assert!(
            children.iter().any(|c| c.node.name == "apply_discount"),
            "the file must still own its functions after a re-resolve"
        );
    }

    #[test]
    fn a_global_is_connected_to_the_code_that_reads_it() {
        let dir = fixture();
        let (workspace, _) = index(&dir);

        let tax = workspace.graph().search_symbols("TAX", &[], 1).unwrap();
        assert_eq!(tax.len(), 1);

        let readers = workspace
            .graph()
            .neighbours(tax[0].id, Direction::In, &[EdgeKind::Reads])
            .unwrap();
        assert!(
            readers.iter().any(|r| r.node.name == "total"),
            "total() uses TAX and the graph should say so"
        );
    }

    #[test]
    fn a_second_run_reuses_every_parse() {
        let dir = fixture();
        let (mut workspace, _) = index(&dir);

        let again = workspace.index(&|_| {}).unwrap();
        assert_eq!(again.files_parsed, 0, "nothing changed, nothing should be reparsed");
        assert!(again.files_reused >= 2);
    }

    #[test]
    fn editing_one_file_reparses_only_that_file() {
        let dir = fixture();
        let (mut workspace, _) = index(&dir);

        std::fs::write(
            dir.path().join("src/main.py"),
            "from pricing import total\n\ndef main():\n    print(total(200, 0.2))\n",
        )
        .unwrap();

        let again = workspace.index(&|_| {}).unwrap();
        assert_eq!(again.files_parsed, 1);
    }

    #[test]
    fn deleting_a_file_removes_its_symbols() {
        let dir = fixture();
        let (mut workspace, _) = index(&dir);

        std::fs::remove_file(dir.path().join("src/main.py")).unwrap();
        let again = workspace.index(&|_| {}).unwrap();

        assert_eq!(again.files_removed, 1);
        assert!(workspace.graph().search_symbols("main", &[], 10).unwrap().is_empty());
    }

    #[test]
    fn unparsable_files_stay_searchable() {
        let dir = fixture();
        let (workspace, _) = index(&dir);

        let hits = workspace.graph().search_text("Beispielprojekt", 10).unwrap();
        assert_eq!(hits.len(), 1, "the README has no grammar but must still be findable");
        assert_eq!(hits[0].path, "README.md");
    }

    #[test]
    fn ranking_puts_the_shared_helper_above_the_leaf() {
        let dir = fixture();
        let (workspace, _) = index(&dir);

        let helper = workspace.graph().search_symbols("apply_discount", &[], 1).unwrap();
        let main = workspace.graph().search_symbols("main", &[], 1).unwrap();

        let helper_score = workspace.graph().node(helper[0].id).unwrap().unwrap().metrics.relevance;
        let main_score = workspace.graph().node(main[0].id).unwrap().unwrap().metrics.relevance;
        assert!(
            helper_score > main_score,
            "apply_discount is used by the rest of the code; main() is a leaf ({helper_score} vs {main_score})"
        );
    }

    #[test]
    fn computed_facts_are_stored_alongside_symbols() {
        let dir = fixture();
        let (workspace, _) = index(&dir);

        let hit = workspace.graph().search_symbols("apply_discount", &[], 1).unwrap();
        let detail = workspace.graph().node(hit[0].id).unwrap().unwrap();
        let facts = detail.facts.expect("facts computed for a callable");

        assert_eq!(facts.params.len(), 2);
        assert_eq!(detail.doc.as_deref(), Some("Zieht einen Rabatt ab."));
    }
}
