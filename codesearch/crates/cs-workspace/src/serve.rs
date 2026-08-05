//! A line protocol over stdin/stdout, so another program can use the graph.
//!
//! PaperKG's backend is Python. It needs the whole query surface, not the
//! human-readable CLI output, and it needs it often enough that a process per
//! question would be wasteful. So: one long-lived child per code project,
//! speaking newline-delimited JSON.
//!
//! Deliberately not HTTP. A port means picking one, guarding it, and explaining
//! to the user why a code viewer opened a socket; stdio means the child dies
//! with its parent and no one else can reach it. It also costs no dependency —
//! `serde_json` was already here.
//!
//! ```text
//! → {"id":1,"method":"search_symbols","params":{"query":"parse"}}
//! ← {"id":1,"ok":true,"result":[…]}
//! ← {"id":2,"event":"progress","data":{"phase":"parsing","done":128,"total":840}}
//! ← {"id":2,"ok":true,"result":{…IndexReport…}}
//! ```
//!
//! Every method is a thin projection of a `cs-graph` query, and — as in the
//! desktop shell — nothing here may drop a confidence level or an evidence
//! location on the way out. A caller that cannot render the badge next to a
//! relationship would be presenting a guess as a fact.

use anyhow::{anyhow, Context, Result};
use cs_core::{EdgeKind, FileId, NodeId, NodeKind, Span};
use cs_graph::{CycleLevel, Direction, HotspotThresholds, SpanRead};
use cs_llm::citation;
use cs_llm::tools;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, Write};
use std::path::Path;

use crate::{Progress, Workspace};

#[derive(Deserialize)]
struct Request {
    /// Echoed back on the response. Any JSON value; the caller decides.
    #[serde(default)]
    id: Value,
    method: String,
    #[serde(default)]
    params: Value,
}

/// Writes one JSON value as a line and flushes it.
///
/// Flushing per line is the entire point: progress that arrives after the run it
/// describes is not progress. The lock is taken per write because indexing
/// reports from rayon worker threads.
fn emit(value: &Value) {
    let stdout = std::io::stdout();
    let mut handle = stdout.lock();
    if serde_json::to_writer(&mut handle, value).is_ok() {
        let _ = handle.write_all(b"\n");
        let _ = handle.flush();
    }
}

fn ok(id: &Value, result: Value) {
    emit(&json!({ "id": id, "ok": true, "result": result }));
}

fn err(id: &Value, error: &str) {
    emit(&json!({ "id": id, "ok": false, "error": error }));
}

/// Per-conversation record of what the model was actually shown.
///
/// This is the license to quote: `citation::verify` will only accept a citation
/// into lines that some tool call in *this* session handed over. Keeping the
/// sessions here rather than in the caller is what stops the evidence invariant
/// from being something Python could forget to enforce.
type Sessions = HashMap<String, tools::Session>;

/// One open workspace plus the conversations running against it.
///
/// Split from the stdio loop so the dispatch table can be exercised directly —
/// a protocol that can only be tested by spawning a process and typing at it
/// tends not to be tested.
pub struct Server {
    workspace: Workspace,
    sessions: Sessions,
}

impl Server {
    pub fn open(root: &Path, db_path: Option<&Path>) -> Result<Self> {
        let workspace = match db_path {
            Some(path) => Workspace::open_with_db(root, path)?,
            None => Workspace::open(root)?,
        };
        Ok(Self { workspace, sessions: HashMap::new() })
    }

    pub fn root(&self) -> &Path {
        self.workspace.root()
    }

    /// Runs one method. `id` is only used to tag progress events.
    pub fn call(&mut self, method: &str, params: Value, id: &Value) -> Result<Value> {
        dispatch(&mut self.workspace, &mut self.sessions, method, &params, id)
    }

    /// Reads requests from stdin until the pipe closes.
    pub fn serve_stdio(mut self) -> Result<()> {
        // Announce readiness before reading, so the caller can wait for a line
        // instead of sleeping a guessed number of milliseconds.
        emit(&json!({
            "event": "ready",
            "root": self.workspace.root().display().to_string(),
        }));

        let stdin = std::io::stdin();
        for line in stdin.lock().lines() {
            let line = match line {
                Ok(line) => line,
                // A closed pipe is the normal way this ends.
                Err(_) => break,
            };
            if line.trim().is_empty() {
                continue;
            }

            let request: Request = match serde_json::from_str(&line) {
                Ok(request) => request,
                Err(error) => {
                    err(&Value::Null, &format!("Ungültige Anfrage: {error}"));
                    continue;
                }
            };

            if request.method == "shutdown" {
                ok(&request.id, json!({ "bye": true }));
                break;
            }

            let id = request.id.clone();
            match self.call(&request.method, request.params, &id) {
                Ok(result) => ok(&id, result),
                Err(error) => err(&id, &format!("{error:#}")),
            }
        }

        Ok(())
    }
}

pub fn run(root: &Path, db_path: Option<&Path>) -> Result<()> {
    Server::open(root, db_path)?.serve_stdio()
}

fn dispatch(
    workspace: &mut Workspace,
    sessions: &mut Sessions,
    method: &str,
    params: &Value,
    request_id: &Value,
) -> Result<Value> {
    match method {
        "ping" => Ok(json!({ "pong": true })),

        "open" => {
            let stats = workspace.graph().stats()?;
            Ok(json!({
                "root": workspace.root().display().to_string(),
                "needs_index": stats.nodes == 0,
                "stats": stats,
            }))
        }

        "stats" => Ok(serde_json::to_value(workspace.graph().stats()?)?),

        "index" => {
            let id = request_id.clone();
            let report = workspace.index(&move |progress: Progress| {
                emit(&json!({ "id": id, "event": "progress", "data": progress }));
            })?;
            Ok(serde_json::to_value(report)?)
        }

        "search_symbols" => {
            let query = str_param(params, "query")?;
            let kinds = node_kinds(params, "kinds");
            let limit = usize_param(params, "limit", 40);
            Ok(serde_json::to_value(workspace.graph().search_symbols(
                &query, &kinds, limit,
            )?)?)
        }

        "search_text" => {
            let query = str_param(params, "query")?;
            let limit = usize_param(params, "limit", 20);
            Ok(serde_json::to_value(
                workspace.graph().search_text(&query, limit)?,
            )?)
        }

        "symbol_at" => {
            let path = str_param(params, "path")?;
            let line = u32_param(params, "line", 1);
            let node = workspace.graph().symbol_at(FileId::of_path(&path), line)?;
            Ok(json!({ "node_id": node }))
        }

        "node" => {
            let id = node_id(params, "id")?;
            Ok(serde_json::to_value(workspace.graph().node(id)?)?)
        }

        "neighbours" => {
            let id = node_id(params, "id")?;
            let direction = direction(params);
            let kinds = edge_kinds(params, "edge_kinds", &REFERENCE_EDGES);
            Ok(serde_json::to_value(
                workspace.graph().neighbours(id, direction, &kinds)?,
            )?)
        }

        "graph_slice" => {
            let id = node_id(params, "id")?;
            let depth = u32_param(params, "depth", 1);
            let budget = usize_param(params, "budget", 400);
            let kinds = edge_kinds(params, "edge_kinds", &[]);
            Ok(serde_json::to_value(workspace.graph().slice(
                id,
                depth,
                direction(params),
                &kinds,
                budget,
            )?)?)
        }

        "path_between" => {
            let from = node_id(params, "from")?;
            let to = node_id(params, "to")?;
            let max_depth = u32_param(params, "max_depth", 12);
            let Some(path) = workspace.graph().path_between(from, to, max_depth)? else {
                return Ok(json!({ "path": Value::Null }));
            };
            let mut steps = Vec::new();
            for step in path {
                if let Some(detail) = workspace.graph().node(step)? {
                    steps.push(json!({
                        "id": detail.id,
                        "name": detail.name,
                        "qualified": detail.qualified,
                        "path": detail.path,
                        "line": detail.span.start_line,
                    }));
                }
            }
            Ok(json!({ "path": steps }))
        }

        "impact" => {
            // „Was bricht, wenn ich das aendere?" — Rueckwaerts-Erreichbarkeit
            // plus die Tests, die die Stelle abdecken. Default calls/reads/writes
            // (REFERENCE_EDGES); ein leerer Client-Wunsch meint hier *nicht*
            // „alle Kanten", sondern faellt auf die Referenzkanten zurueck.
            let id = node_id(params, "id")?;
            let max_depth = u32_param(params, "max_depth", 3);
            let budget = usize_param(params, "budget", 400);
            let kinds = edge_kinds(params, "edge_kinds", &REFERENCE_EDGES);
            Ok(serde_json::to_value(
                workspace.graph().impact(id, max_depth, &kinds, budget)?,
            )?)
        }

        "top_symbols" => {
            let limit = usize_param(params, "limit", 30);
            let kinds = node_kinds(params, "kinds");
            let kinds = if kinds.is_empty() {
                vec![NodeKind::Function, NodeKind::Method, NodeKind::Class, NodeKind::Interface]
            } else {
                kinds
            };
            Ok(serde_json::to_value(
                workspace.graph().top_by_relevance(limit, &kinds)?,
            )?)
        }

        "overview" => overview(workspace),

        "hotspots" => {
            // Spaghetti-diagnose: jede Fundstelle trägt, *welche* Regel sie
            // gerissen hat und mit welchem Messwert — keine erfundene Gesamtnote.
            // Schwellen 0 bedeuten „Regel deaktiviert"; ``churn`` 0 fällt auf
            // das obere Dezil dieses Index (relativ, nicht konfiguriert).
            let t = HotspotThresholds {
                loc: u32_param(params, "loc", 200),
                complexity: u32_param(params, "complexity", 15),
                max_nesting: u32_param(params, "max_nesting", 5),
                fan_in: u32_param(params, "fan_in", 30),
                fan_out: u32_param(params, "fan_out", 25),
                churn: u32_param(params, "churn", 0),
            };
            let limit = usize_param(params, "limit", 50);
            Ok(serde_json::to_value(workspace.graph().hotspots(&t, limit)?)?)
        }

        "cycles" => {
            // Echte Zyklenerkennung (Tarjan-SCC) — zwei Ebenen: Dateien über
            // Imports, Symbole über Calls. Ein leerer ``edge_kinds``-Wunsch
            // meint hier *nicht* „alle Kanten", sondern fällt auf die
            // ebenentypische Menge zurück, damit die Frage „wo ist es
            // verknotet?" nicht von der Wahl der Kantenart abhängt.
            let level = match params.get("level").and_then(Value::as_str) {
                Some("symbol") => CycleLevel::Symbol,
                _ => CycleLevel::File,
            };
            let fallback: &[EdgeKind] = match level {
                CycleLevel::File => &[EdgeKind::Imports],
                CycleLevel::Symbol => &[EdgeKind::Calls],
            };
            let kinds = edge_kinds(params, "edge_kinds", fallback);
            Ok(serde_json::to_value(workspace.graph().cycles(level, &kinds)?)?)
        }

        "blueprint" => {
            let id = node_id(params, "id")?;
            let focus = workspace
                .graph()
                .node(id)?
                .ok_or_else(|| anyhow!("Knoten nicht gefunden"))?;
            Ok(json!({
                "focus": focus,
                "callers": workspace.graph().neighbours(id, Direction::In, &REFERENCE_EDGES)?,
                "callees": workspace.graph().neighbours(id, Direction::Out, &REFERENCE_EDGES)?,
                "children": children(workspace, id)?,
            }))
        }

        "class_diagram" => class_diagram(workspace, node_id(params, "id")?),

        "sequence_diagram" => {
            sequence_diagram(workspace, node_id(params, "id")?, u32_param(params, "depth", 2))
        }

        "source" => {
            let path = str_param(params, "path")?;
            let file = FileId::of_path(&path);
            // A span covering the file: `read_span` clamps the end to the real
            // length and checks the recorded hash on the way through.
            let whole = Span::new(0, u32::MAX, 1, u32::MAX);
            match workspace.graph().read_span(file, whole)? {
                SpanRead::Text { path, text, .. } => {
                    Ok(json!({ "path": path, "text": text, "stale": false }))
                }
                SpanRead::Stale { path } | SpanRead::OutOfRange { path } => {
                    // Still show it, but say plainly that the line numbers in the
                    // graph no longer line up with what is on disk.
                    let text = std::fs::read_to_string(workspace.root().join(&path))
                        .unwrap_or_default();
                    Ok(json!({ "path": path, "text": text, "stale": true }))
                }
            }
        }

        "resolve_positions" => resolve_positions(workspace, params),

        "tool_specs" => Ok(serde_json::to_value(tools::specs())?),

        "tool_call" => {
            let name = str_param(params, "name")?;
            let arguments = match params.get("arguments") {
                // Accept both the raw string providers emit and a real object.
                Some(Value::String(raw)) => raw.clone(),
                Some(value) => value.to_string(),
                None => "{}".to_string(),
            };
            let session = sessions.entry(session_key(params)).or_default();
            let raw = tools::dispatch(workspace.graph(), session, &name, &arguments);
            // Tools answer in JSON; hand the caller a value, not a string it
            // would have to parse a second time.
            Ok(serde_json::from_str(&raw).unwrap_or(Value::String(raw)))
        }

        "context_build" => {
            let question = str_param(params, "question")?;
            let broad = params.get("broad").and_then(Value::as_bool).unwrap_or(false);
            let max_symbols = usize_param(params, "max_symbols", 6).clamp(1, 40);

            // Extra search terms from the caller, plus the German→English code
            // vocabulary. Both are only *more* places to look; neither can make
            // retrieval claim something it did not find.
            let mut extra: Vec<String> = params
                .get("extra_terms")
                .and_then(Value::as_array)
                .map(|items| {
                    items.iter().filter_map(Value::as_str).map(str::to_string).collect()
                })
                .unwrap_or_default();
            for term in cs_llm::context::code_terms_for(&question) {
                if !extra.contains(&term) {
                    extra.push(term);
                }
            }

            let retrieval = cs_llm::context::build_with(
                workspace.graph(),
                &question,
                &extra,
                max_symbols,
                broad,
            );
            let result = json!({
                "context": retrieval.context,
                "matched_by_name": retrieval.matched_by_name,
                "symbols": retrieval.symbols,
                "symbol_ids": retrieval.symbol_ids,
                "extra_terms": extra,
            });

            // The retrieval *is* the permission to quote what it showed.
            //
            // A new **question** starts a new record — that is the default and
            // the reason the check exists. A follow-up inside the same
            // conversation must not, though: the model was shown those lines,
            // and flagging a quote of them as fabricated would be the check
            // lying. `extend` is that second case, and only that one.
            let key = session_key(params);
            if params.get("extend").and_then(Value::as_bool).unwrap_or(false) {
                let carried = retrieval.session.into_shown();
                let session = sessions.entry(key).or_default();
                for span in carried {
                    session.record(span);
                }
            } else {
                sessions.insert(key, retrieval.session);
            }
            Ok(result)
        }

        "verify_citations" => {
            let text = str_param(params, "text")?;
            let session = sessions.entry(session_key(params)).or_default();
            let verified = citation::verify(workspace.graph(), session, &text);
            let verdict = verified.verdict();
            Ok(json!({
                "text": verified.text,
                "citations": verified.citations,
                "quote_mismatches": verified.quote_mismatches,
                "uncited_sentences": verified.uncited_sentences,
                "is_clean": verified.is_clean(),
                "verdict": verdict,
                "verdict_label": verdict.label(),
            }))
        }

        "session_reset" => {
            sessions.remove(&session_key(params));
            Ok(json!({ "reset": true }))
        }

        // The project seen from far enough away to have a shape. `prefix` is ""
        // for the top level and descends one path segment at a time.
        "clusters" => {
            let prefix = params.get("prefix").and_then(Value::as_str).unwrap_or("");
            let kinds = edge_kinds(params, "edge_kinds", &CLUSTER_EDGES);
            Ok(serde_json::to_value(workspace.graph().clusters(prefix, &kinds)?)?)
        }

        // The real edges behind one aggregated arrow. Without this the rollup
        // would be a summary nobody could check.
        "cluster_edges" => {
            let from = str_param(params, "from")?;
            let to = str_param(params, "to")?;
            let kinds = edge_kinds(params, "edge_kinds", &CLUSTER_EDGES);
            let limit = usize_param(params, "limit", 60);
            Ok(serde_json::to_value(
                workspace.graph().cluster_edge_detail(&from, &to, &kinds, limit)?,
            )?)
        }

        "cluster_members" => {
            let prefix = params.get("prefix").and_then(Value::as_str).unwrap_or("");
            let limit = usize_param(params, "limit", 30);
            Ok(serde_json::to_value(workspace.graph().cluster_members(prefix, limit)?)?)
        }

        other => Err(anyhow!("unbekannte Methode: {other}")),
    }
}

/// Calls, reads and writes — what "who uses this" means in practice.
const REFERENCE_EDGES: [EdgeKind; 3] = [EdgeKind::Calls, EdgeKind::Reads, EdgeKind::Writes];

/// What counts as one area depending on another.
///
/// Wider than [`REFERENCE_EDGES`] because at this zoom an import *is* a
/// dependency, and narrower than "everything" because `contains` holds between
/// a file and its own symbols — every cluster would depend on itself through it.
const CLUSTER_EDGES: [EdgeKind; 6] = [
    EdgeKind::Calls,
    EdgeKind::Reads,
    EdgeKind::Writes,
    EdgeKind::Imports,
    EdgeKind::Inherits,
    EdgeKind::Implements,
];

/// What this repository is, on one screen.
///
/// The `hot_files` query is the only hand-written SQL above the graph layer,
/// carried over unchanged from the desktop shell — a bad join here still returns
/// rows, just not the right ones.
fn overview(workspace: &Workspace) -> Result<Value> {
    let hot_files: Vec<Value> = {
        let conn = workspace.graph().connection();
        let mut stmt = conn.prepare(
            "SELECT f.path, max(m.churn), max(m.risk)
             FROM metrics m
             JOIN nodes n ON n.id = m.node_id
             JOIN files f ON f.id = n.file_id
             WHERE m.churn > 0
             GROUP BY f.path
             ORDER BY max(m.churn) DESC
             LIMIT 12",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok(json!({
                "path": row.get::<_, String>(0)?,
                "churn": row.get::<_, u32>(1)?,
                "risk": row.get::<_, u32>(2)?,
            }))
        })?;
        rows.collect::<Result<_, _>>()?
    };

    Ok(json!({
        "stats": workspace.graph().stats()?,
        "important": workspace.graph().top_by_relevance(
            14,
            &[NodeKind::Function, NodeKind::Method, NodeKind::Class],
        )?,
        "hot_files": hot_files,
        "dependencies": workspace.graph().top_by_relevance(24, &[NodeKind::ExternalPackage])?,
        "gaps": workspace.graph().top_by_relevance(12, &[NodeKind::DynamicGap])?,
    }))
}

/// The symbols whose `parent` is `id` — a class's methods, a module's functions.
///
/// Deliberately over `nodes.parent_id` rather than the `Contains` edge kind:
/// containment is recorded flat (a file contains its class *and* its methods),
/// so the edge says nothing about which method belongs to which class. The
/// `nodes_parent` index makes this cheap.
fn children_of(workspace: &Workspace, id: NodeId) -> Result<Vec<NodeId>> {
    let conn = workspace.graph().connection();
    let mut stmt = conn.prepare("SELECT id FROM nodes WHERE parent_id = ?1")?;
    let rows = stmt.query_map([id.to_sqlite()], |row| Ok(NodeId::from_sqlite(row.get(0)?)))?;
    Ok(rows.collect::<Result<_, _>>()?)
}

/// The children of a symbol, shaped like a neighbour list so the UI can reuse it.
///
/// Containment is structural, not inferred, so the confidence is `verified` by
/// construction and the evidence is the child's own definition site — which is
/// literally the place in the source where the containment is visible. The
/// badge is still emitted rather than omitted: every relationship this tool
/// shows carries one, and an exception would be the start of the erosion.
fn children(workspace: &Workspace, id: NodeId) -> Result<Vec<Value>> {
    let graph = workspace.graph();
    let mut out = Vec::new();
    for child in children_of(workspace, id)? {
        let Some(detail) = graph.node(child)? else { continue };
        out.push(json!({
            "node": {
                "id": detail.id,
                "name": detail.name,
                "qualified": detail.qualified,
                "kind": detail.kind.as_str(),
                "lang": detail.language.slug(),
                "path": detail.path,
                "line": detail.span.start_line,
                "relevance": detail.metrics.relevance,
            },
            "kind": EdgeKind::Contains.as_str(),
            "confidence": cs_core::Confidence::Verified,
            "evidence_path": detail.path,
            "evidence_line": detail.span.start_line,
            "candidates": 1,
            "occurrences": 1,
        }));
    }
    Ok(out)
}

/// Everything needed to draw a class diagram around one symbol.
///
/// Scoped to the focus and its direct relatives rather than the whole
/// repository: a class diagram of four hundred classes is a wall, not a diagram,
/// and the question people actually have is "what does *this* sit between".
fn class_diagram(workspace: &Workspace, id: NodeId) -> Result<Value> {
    const TYPE_EDGES: [EdgeKind; 2] = [EdgeKind::Inherits, EdgeKind::Implements];

    let graph = workspace.graph();
    let focus = graph.node(id)?.ok_or_else(|| anyhow!("Knoten nicht gefunden"))?;

    // Start from the type itself; if a method was focused, climb to its owner.
    let root = match focus.kind {
        NodeKind::Method | NodeKind::Function | NodeKind::Field => {
            focus.parent.and_then(|parent| graph.node(parent).ok().flatten()).unwrap_or(focus)
        }
        _ => focus,
    };

    let mut wanted = vec![root.id];
    wanted.extend(
        graph.neighbours(root.id, Direction::Out, &TYPE_EDGES)?.into_iter().map(|n| n.node.id),
    );
    wanted.extend(
        graph
            .neighbours(root.id, Direction::In, &TYPE_EDGES)?
            .into_iter()
            .take(8)
            .map(|n| n.node.id),
    );
    wanted.sort();
    wanted.dedup();

    let mut classes = Vec::new();
    let mut inherits = Vec::new();

    for node_id in &wanted {
        let Some(node) = graph.node(*node_id)? else { continue };

        let members: Vec<Value> = children_of(workspace, *node_id)?
            .into_iter()
            .take(30)
            .filter_map(|child| {
                let detail = graph.node(child).ok().flatten()?;
                Some(json!({
                    "name": detail.name,
                    "kind": detail.kind.as_str(),
                    "signature": detail.facts.map(|facts| facts.signature),
                }))
            })
            .collect();

        classes.push(json!({
            "id": node.id,
            "name": node.name,
            "path": node.path,
            "line": node.span.start_line,
            "kind": node.kind.as_str(),
            "members": members,
        }));

        // Each inheritance arrow keeps its confidence and its evidence location.
        // A diagram is the most authoritative-looking thing a tool can produce;
        // dropping the badge here would launder a name-match guess into a fact.
        for parent in graph.neighbours(*node_id, Direction::Out, &TYPE_EDGES)? {
            inherits.push(json!({
                "from": node.name,
                "to": parent.node.name,
                "kind": parent.kind.as_str(),
                "confidence": parent.confidence,
                "evidence_path": parent.evidence_path,
                "evidence_line": parent.evidence_line,
            }));
        }
    }

    Ok(json!({ "classes": classes, "inherits": inherits, "sequence": [] }))
}

/// A sequence diagram along the outgoing calls of a symbol.
///
/// Static, so it shows what *can* happen rather than what did. Each step keeps
/// its confidence for the same reason the arrows above do.
fn sequence_diagram(workspace: &Workspace, id: NodeId, depth: u32) -> Result<Value> {
    const MAX_STEPS: usize = 40;

    let graph = workspace.graph();
    let focus = graph.node(id)?.ok_or_else(|| anyhow!("Knoten nicht gefunden"))?;

    // A type makes no calls — its methods do. Starting at the class itself
    // returned an empty diagram for every class, which reads as "broken" rather
    // than as "wrong question".
    //
    // Descending happens over `nodes.parent_id`, *not* over `Contains` edges:
    // containment is recorded flat (the file contains both the class and its
    // methods), so `neighbours(.., Contains)` on a class yields its file and
    // nothing else.
    let mut frontier = match focus.kind {
        NodeKind::Class | NodeKind::Interface | NodeKind::Module | NodeKind::File => {
            children_of(workspace, id)?
        }
        _ => Vec::new(),
    };
    if frontier.is_empty() {
        frontier = vec![id];
    }
    let mut seen = frontier.clone();
    let mut sequence = Vec::new();

    for _ in 0..depth.clamp(1, 4) {
        let mut next = Vec::new();
        for current in frontier.drain(..) {
            let Some(from) = graph.node(current)? else { continue };
            for call in graph.neighbours(current, Direction::Out, &[EdgeKind::Calls])? {
                sequence.push(json!({
                    "from": from.name,
                    "to": call.node.name,
                    "to_id": call.node.id,
                    "confidence": call.confidence,
                    "candidates": call.candidates,
                    "evidence_path": call.evidence_path,
                    "evidence_line": call.evidence_line,
                }));
                if !seen.contains(&call.node.id) && sequence.len() < MAX_STEPS {
                    seen.push(call.node.id);
                    next.push(call.node.id);
                }
            }
        }
        if next.is_empty() {
            break;
        }
        frontier = next;
    }

    Ok(json!({ "classes": [], "inherits": [], "sequence": sequence }))
}

/// Turns `datei:zeile` pairs found in terminal output into graph positions.
///
/// The scanning itself happens in the caller (PaperKG owns the terminal); what
/// only the graph can do is say which symbol a line falls inside, which is what
/// turns a stack trace into something you can click.
fn resolve_positions(workspace: &Workspace, params: &Value) -> Result<Value> {
    #[derive(Deserialize)]
    struct Position {
        path: String,
        line: u32,
    }

    #[derive(Serialize)]
    struct Resolved {
        path: String,
        line: u32,
        node_id: Option<NodeId>,
        qualified: Option<String>,
    }

    let positions: Vec<Position> = match params.get("positions") {
        Some(value) => serde_json::from_value(value.clone())
            .context("positions muss eine Liste aus {path, line} sein")?,
        None => Vec::new(),
    };

    let mut resolved = Vec::new();
    for position in positions {
        let file = FileId::of_path(&position.path);
        let node = workspace.graph().symbol_at(file, position.line).ok().flatten();
        let qualified = match node {
            Some(id) => workspace.graph().node(id).ok().flatten().map(|d| d.qualified),
            None => None,
        };
        resolved.push(Resolved {
            path: position.path,
            line: position.line,
            node_id: node,
            qualified,
        });
    }

    Ok(serde_json::to_value(resolved)?)
}

// --- parameter helpers -------------------------------------------------------

/// Conversations are named by the caller; an unnamed one still gets a session so
/// that a single question works without any bookkeeping.
fn session_key(params: &Value) -> String {
    params
        .get("session")
        .and_then(Value::as_str)
        .unwrap_or("default")
        .to_string()
}

fn str_param(params: &Value, name: &str) -> Result<String> {
    params
        .get(name)
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(|| anyhow!("Parameter fehlt oder ist kein Text: {name}"))
}

fn usize_param(params: &Value, name: &str, fallback: usize) -> usize {
    params.get(name).and_then(Value::as_u64).map(|v| v as usize).unwrap_or(fallback)
}

fn u32_param(params: &Value, name: &str, fallback: u32) -> u32 {
    params.get(name).and_then(Value::as_u64).map(|v| v as u32).unwrap_or(fallback)
}

/// Ids arrive as hex strings; an integer is tolerated for hand-written calls.
fn node_id(params: &Value, name: &str) -> Result<NodeId> {
    let raw = params
        .get(name)
        .ok_or_else(|| anyhow!("Parameter fehlt: {name}"))?;
    serde_json::from_value(raw.clone()).map_err(|_| anyhow!("Ungültige Knoten-ID: {raw}"))
}

fn direction(params: &Value) -> Direction {
    match params.get("direction").and_then(Value::as_str) {
        Some("in") => Direction::In,
        Some("out") => Direction::Out,
        _ => Direction::Both,
    }
}

fn node_kinds(params: &Value, name: &str) -> Vec<NodeKind> {
    params
        .get(name)
        .and_then(Value::as_array)
        .map(|items| {
            items.iter().filter_map(Value::as_str).filter_map(NodeKind::from_str).collect()
        })
        .unwrap_or_default()
}

fn edge_kinds(params: &Value, name: &str, fallback: &[EdgeKind]) -> Vec<EdgeKind> {
    let kinds: Vec<EdgeKind> = params
        .get(name)
        .and_then(Value::as_array)
        .map(|items| {
            items.iter().filter_map(Value::as_str).filter_map(EdgeKind::from_str).collect()
        })
        .unwrap_or_default();
    if kinds.is_empty() {
        fallback.to_vec()
    } else {
        kinds
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// A workspace with one file whose contents the tests know by heart.
    fn fixture() -> (tempfile::TempDir, tempfile::TempDir, Server) {
        let root = tempfile::tempdir().expect("temp workspace");
        let index = tempfile::tempdir().expect("temp index dir");
        fs::create_dir_all(root.path().join("src")).expect("src");
        fs::write(
            root.path().join("src/pricing.py"),
            "def apply_discount(total, pct):\n    return total * (1 - pct)\n\n\ndef checkout(cart):\n    return apply_discount(cart.total, 0.1)\n",
        )
        .expect("write fixture");

        let mut server =
            Server::open(root.path(), Some(&index.path().join("index.csdb"))).expect("open");
        server.call("index", json!({}), &Value::Null).expect("index");
        (root, index, server)
    }

    /// Two areas that depend on each other, so a rollup has something to roll up.
    fn two_area_fixture() -> (tempfile::TempDir, tempfile::TempDir, Server) {
        let root = tempfile::tempdir().expect("temp workspace");
        let index = tempfile::tempdir().expect("temp index dir");
        fs::create_dir_all(root.path().join("storage/backends")).expect("storage");
        fs::create_dir_all(root.path().join("api")).expect("api");

        fs::write(
            root.path().join("storage/__init__.py"),
            "from storage.backends.disk import save_row\n\n\ndef store(row):\n    return save_row(row)\n",
        )
        .expect("storage init");
        fs::write(
            root.path().join("storage/backends/disk.py"),
            "def save_row(row):\n    return len(row)\n",
        )
        .expect("disk");
        fs::write(
            root.path().join("api/routes.py"),
            "from storage import store\n\n\ndef post_row(row):\n    return store(row)\n\n\ndef get_row(key):\n    return store(key)\n",
        )
        .expect("routes");

        let mut server =
            Server::open(root.path(), Some(&index.path().join("index.csdb"))).expect("open");
        server.call("index", json!({}), &Value::Null).expect("index");
        (root, index, server)
    }

    /// Both licence tests need two questions that retrieve *disjoint* code.
    ///
    /// `tools/lonely.py` has no callers and no callees, so a question naming
    /// `post_row` cannot reach it — not through dependencies and not through the
    /// top-by-relevance fallback, which only runs when nothing matched by name.
    /// Without that separation the fixture re-licenses the same file for every
    /// question and neither test would prove anything.
    fn disjoint_fixture() -> (tempfile::TempDir, tempfile::TempDir, Server) {
        let (root, index, mut server) = two_area_fixture();
        fs::create_dir_all(root.path().join("tools")).expect("tools");
        fs::write(
            root.path().join("tools/lonely.py"),
            "def lonely_helper(value):\n    return value + 1\n",
        )
        .expect("lonely");
        server.call("index", json!({}), &Value::Null).expect("reindex");
        (root, index, server)
    }

    #[test]
    fn a_new_question_still_starts_with_an_empty_licence_to_quote() {
        let (_root, _index, mut server) = disjoint_fixture();
        let claim = "Der Helfer steht in tools/lonely.py:1-2.";

        server
            .call("context_build", json!({ "session": "s", "question": "lonely_helper" }), &Value::Null)
            .expect("build");
        let first = server
            .call("verify_citations", json!({ "session": "s", "text": claim }), &Value::Null)
            .expect("verify");
        assert_eq!(first["citations"][0]["status"], "verified");

        // Default behaviour is unchanged: a new question replaces the record.
        server
            .call("context_build", json!({ "session": "s", "question": "post_row" }), &Value::Null)
            .expect("rebuild");
        let second = server
            .call("verify_citations", json!({ "session": "s", "text": claim }), &Value::Null)
            .expect("verify");
        assert_eq!(
            second["citations"][0]["status"], "not_retrieved",
            "a new question must not inherit the last one's permission to quote"
        );
    }

    #[test]
    fn a_follow_up_keeps_what_the_first_turn_was_shown() {
        let (_root, _index, mut server) = disjoint_fixture();
        let claim = "Der Helfer steht in tools/lonely.py:1-2.";

        server
            .call("context_build", json!({ "session": "c", "question": "lonely_helper" }), &Value::Null)
            .expect("turn one");
        server
            .call(
                "context_build",
                json!({ "session": "c", "question": "post_row", "extend": true }),
                &Value::Null,
            )
            .expect("turn two");

        let verified = server
            .call("verify_citations", json!({ "session": "c", "text": claim }), &Value::Null)
            .expect("verify");
        assert_eq!(
            verified["citations"][0]["status"], "verified",
            "inside one conversation the model *was* shown these lines"
        );
    }

    #[test]
    fn a_german_question_reaches_english_identifiers() {
        let terms = cs_llm::context::code_terms_for("Wo werden die Passwörter verschlüsselt?");
        assert!(terms.contains(&"password".to_string()), "got {terms:?}");
        assert!(terms.contains(&"encrypt".to_string()), "got {terms:?}");
        // Kein Treffer heisst keine Begriffe — nicht geraten.
        assert!(cs_llm::context::code_terms_for("wie schnell ist das").is_empty());
    }

    #[test]
    fn broad_retrieval_reports_the_symbols_it_pulled() {
        let (_root, _index, mut server) = fixture();
        let built = server
            .call(
                "context_build",
                json!({ "session": "b", "question": "Rabatt", "broad": true, "max_symbols": 12 }),
                &Value::Null,
            )
            .expect("build");
        let ids = built["symbol_ids"].as_array().expect("symbol_ids");
        assert!(!ids.is_empty(), "a feature question needs something to point at");
        for id in ids {
            assert!(id.is_string(), "ids stay hex strings across the wire: {id}");
        }
        assert_eq!(ids.len() as u64, built["symbols"].as_u64().unwrap());
    }

    #[test]
    fn the_top_level_of_the_rollup_is_the_directory_tree() {
        let (_root, _index, mut server) = two_area_fixture();
        let level = server.call("clusters", json!({ "prefix": "" }), &Value::Null).expect("top");

        assert_eq!(level["prefix"], "");
        assert!(level["parent"].is_null(), "the top level has nowhere to go up to");
        let names: Vec<&str> =
            level["nodes"].as_array().expect("nodes").iter().map(|n| n["path"].as_str().unwrap()).collect();
        assert!(names.contains(&"storage"), "got {names:?}");
        assert!(names.contains(&"api"), "got {names:?}");

        // Descending must agree with what the level claimed about itself.
        let storage = level["nodes"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["path"] == "storage")
            .expect("storage cluster");
        assert!(storage["symbols"].as_u64().unwrap() > 0);
        assert_eq!(storage["has_children"], true, "storage/backends/ is below it");

        let deeper =
            server.call("clusters", json!({ "prefix": "storage" }), &Value::Null).expect("deeper");
        assert_eq!(deeper["parent"], "");
        let deeper_names: Vec<&str> =
            deeper["nodes"].as_array().unwrap().iter().map(|n| n["path"].as_str().unwrap()).collect();
        assert!(
            deeper_names.contains(&"storage/backends"),
            "one level down shows the subfolder: {deeper_names:?}"
        );
        // A file sitting directly in the prefix is its own leaf, not a silent
        // remainder — otherwise storage/__init__.py would have no home.
        assert!(
            deeper_names.iter().any(|name| name.ends_with(".py")),
            "files directly in the prefix must still appear: {deeper_names:?}"
        );
    }

    #[test]
    fn an_aggregated_edge_carries_the_weakest_confidence_it_summarises() {
        let (_root, _index, mut server) = two_area_fixture();
        let level = server.call("clusters", json!({ "prefix": "" }), &Value::Null).expect("top");
        let edges = level["edges"].as_array().expect("edges");
        assert!(!edges.is_empty(), "api depends on storage");

        let rank = |value: &str| match value {
            "guessed" => 0,
            "resolved" => 1,
            "verified" => 2,
            _ => 3,
        };

        for edge in edges {
            assert_ne!(edge["from"], edge["to"], "a cluster is not its own neighbour");
            let claimed = edge["weakest"].as_str().expect("weakest");

            // Open the aggregate and check the claim against the real edges.
            let detail = server
                .call(
                    "cluster_edges",
                    json!({ "from": edge["from"], "to": edge["to"] }),
                    &Value::Null,
                )
                .expect("detail");
            let real = detail.as_array().expect("array");
            assert!(!real.is_empty(), "an aggregated edge must open into real ones");

            let lowest = real
                .iter()
                .map(|e| rank(e["confidence"].as_str().expect("confidence")))
                .min()
                .expect("at least one");
            assert_eq!(
                rank(claimed),
                lowest,
                "the rollup must report the weakest grade it contains, not the commonest"
            );

            for one in real {
                assert!(one["evidence_path"].is_string(), "no edge without its evidence");
                assert!(
                    one["evidence_line"].as_u64().unwrap_or(0) >= 1,
                    "evidence needs a line to jump to"
                );
                assert!(one["node"]["id"].is_string(), "ids stay hex strings");
            }
        }
    }

    #[test]
    fn cluster_members_are_symbols_not_files() {
        let (_root, _index, mut server) = two_area_fixture();
        let members = server
            .call("cluster_members", json!({ "prefix": "api", "limit": 10 }), &Value::Null)
            .expect("members");
        let names: Vec<&str> =
            members.as_array().expect("array").iter().map(|m| m["name"].as_str().unwrap()).collect();
        assert!(names.contains(&"post_row"), "got {names:?}");
        for member in members.as_array().unwrap() {
            assert_ne!(member["kind"], "file", "a file is the container, not the answer");
            assert_ne!(member["kind"], "module");
        }
    }

    #[test]
    fn the_index_lands_outside_the_workspace() {
        let (root, index, _server) = fixture();
        assert!(
            index.path().join("index.csdb").exists(),
            "the index belongs where the caller asked for it"
        );
        assert!(
            !root.path().join(".codesearch").exists(),
            "indexing a repository must not leave anything behind in it"
        );
    }

    #[test]
    fn node_ids_cross_the_protocol_as_strings() {
        let (_root, _index, mut server) = fixture();
        let hits = server
            .call("search_symbols", json!({ "query": "apply_discount" }), &Value::Null)
            .expect("search");
        let id = &hits.as_array().expect("array")[0]["id"];
        assert!(id.is_string(), "a 64-bit id as a JSON number loses its low bits: {id}");

        // And the string is accepted straight back as a parameter.
        let detail = server.call("node", json!({ "id": id }), &Value::Null).expect("node");
        assert_eq!(detail["name"], "apply_discount");
    }

    #[test]
    fn a_relationship_never_arrives_without_its_confidence_and_evidence() {
        let (_root, _index, mut server) = fixture();
        let hits = server
            .call("search_symbols", json!({ "query": "apply_discount" }), &Value::Null)
            .expect("search");
        let id = hits.as_array().expect("array")[0]["id"].clone();

        let blueprint = server.call("blueprint", json!({ "id": id }), &Value::Null).expect("bp");
        let callers = blueprint["callers"].as_array().expect("callers");
        assert!(!callers.is_empty(), "checkout calls apply_discount");
        for caller in callers {
            assert!(caller["confidence"].is_string(), "confidence must survive the wire");
            assert!(caller["evidence_path"].is_string(), "so must the evidence location");
        }
    }

    #[test]
    fn quoting_is_licensed_by_retrieval_within_the_same_session() {
        let (_root, _index, mut server) = fixture();
        let claim = "Der Rabatt wird in src/pricing.py:1-2 gerechnet.";

        // Never looked up: a real file at a real line is still not evidence.
        let cold = server
            .call("verify_citations", json!({ "session": "cold", "text": claim }), &Value::Null)
            .expect("verify");
        assert_eq!(cold["citations"][0]["status"], "not_retrieved");

        // Same lines, after actually reading them.
        server
            .call(
                "tool_call",
                json!({
                    "session": "warm",
                    "name": "read_lines",
                    "arguments": { "path": "src/pricing.py", "from_line": 1, "to_line": 2 },
                }),
                &Value::Null,
            )
            .expect("read_lines");
        let warm = server
            .call("verify_citations", json!({ "session": "warm", "text": claim }), &Value::Null)
            .expect("verify");
        assert_eq!(warm["citations"][0]["status"], "verified");
        assert_eq!(warm["verdict"], "sound");
    }

    /// A workspace with a small class hierarchy, for the diagram methods.
    fn hierarchy() -> (tempfile::TempDir, tempfile::TempDir, Server) {
        let root = tempfile::tempdir().expect("temp workspace");
        let index = tempfile::tempdir().expect("temp index dir");
        fs::create_dir_all(root.path().join("src")).expect("src");
        fs::write(
            root.path().join("src/shapes.py"),
            "class Shape:\n    def area(self):\n        return 0\n\n\nclass Circle(Shape):\n    def area(self):\n        return 3\n",
        )
        .expect("write fixture");

        let mut server =
            Server::open(root.path(), Some(&index.path().join("index.csdb"))).expect("open");
        server.call("index", json!({}), &Value::Null).expect("index");
        (root, index, server)
    }

    fn find(server: &mut Server, query: &str) -> Value {
        let hits = server
            .call("search_symbols", json!({ "query": query }), &Value::Null)
            .expect("search");
        hits.as_array().expect("array")[0]["id"].clone()
    }

    #[test]
    fn a_class_diagram_keeps_the_confidence_on_every_arrow() {
        let (_root, _index, mut server) = hierarchy();
        let id = find(&mut server, "Circle");

        let diagram = server.call("class_diagram", json!({ "id": id }), &Value::Null).expect("cd");
        let names: Vec<&str> = diagram["classes"]
            .as_array()
            .expect("classes")
            .iter()
            .map(|class| class["name"].as_str().unwrap_or_default())
            .collect();
        assert!(names.contains(&"Circle"), "das Fokus-Symbol gehört ins Diagramm: {names:?}");

        let arrows = diagram["inherits"].as_array().expect("inherits");
        assert!(!arrows.is_empty(), "Circle erbt von Shape");
        for arrow in arrows {
            // Ohne die Sicherheitsstufe wäre ein geratener Pfeil von einem
            // belegten nicht zu unterscheiden — und ein Diagramm sieht
            // autoritativer aus als jede Textzeile.
            assert!(arrow["confidence"].is_string(), "arrow without confidence: {arrow}");
            assert!(arrow["evidence_path"].is_string(), "arrow without evidence: {arrow}");
        }
    }

    /// A class whose method calls a free function, so descending is observable.
    fn class_with_a_calling_method() -> (tempfile::TempDir, tempfile::TempDir, Server) {
        let root = tempfile::tempdir().expect("temp workspace");
        let index = tempfile::tempdir().expect("temp index dir");
        fs::create_dir_all(root.path().join("src")).expect("src");
        fs::write(
            root.path().join("src/cart.py"),
            "def apply_discount(total, pct):\n    return total * (1 - pct)\n\n\nclass Cart:\n    def checkout(self):\n        return apply_discount(10, 0.1)\n",
        )
        .expect("write fixture");

        let mut server =
            Server::open(root.path(), Some(&index.path().join("index.csdb"))).expect("open");
        server.call("index", json!({}), &Value::Null).expect("index");
        (root, index, server)
    }

    #[test]
    fn the_children_of_a_class_are_its_methods_not_its_file() {
        // Containment ist flach gespeichert: die *Datei* enthält Klasse und
        // Methoden gleichermassen. Über die Contains-Kante zu gehen, lieferte
        // unter „Enthält" die Datei — also nichts, was man wissen wollte.
        let (_root, _index, mut server) = class_with_a_calling_method();
        let id = find(&mut server, "Cart");

        let blueprint = server.call("blueprint", json!({ "id": id }), &Value::Null).expect("bp");
        let names: Vec<&str> = blueprint["children"]
            .as_array()
            .expect("children")
            .iter()
            .map(|child| child["node"]["name"].as_str().unwrap_or_default())
            .collect();
        assert!(names.contains(&"checkout"), "erwartet die Methoden, bekam {names:?}");

        for child in blueprint["children"].as_array().expect("children") {
            // Auch eine strukturelle Beziehung trägt ihre Stufe — eine Ausnahme
            // hier wäre der Anfang der Aufweichung.
            assert_eq!(child["confidence"], "verified");
            assert!(child["evidence_path"].is_string());
        }
    }

    #[test]
    fn a_sequence_diagram_of_a_class_descends_into_its_methods() {
        // Eine Klasse ruft selbst nichts auf. Von ihr aus zu starten ergab für
        // *jede* Klasse ein leeres Bild — das liest sich wie ein Defekt, nicht
        // wie eine falsch gestellte Frage.
        let (_root, _index, mut server) = class_with_a_calling_method();
        let id = find(&mut server, "Cart");

        let diagram = server
            .call("sequence_diagram", json!({ "id": id, "depth": 2 }), &Value::Null)
            .expect("sd");
        let steps = diagram["sequence"].as_array().expect("sequence");
        assert!(!steps.is_empty(), "Cart.checkout ruft apply_discount auf");
        assert_eq!(steps[0]["from"], "checkout");
        assert_eq!(steps[0]["to"], "apply_discount");
    }

    #[test]
    fn a_class_diagram_lists_the_members_of_a_class() {
        let (_root, _index, mut server) = class_with_a_calling_method();
        let id = find(&mut server, "Cart");

        let diagram = server.call("class_diagram", json!({ "id": id }), &Value::Null).expect("cd");
        let cart = diagram["classes"]
            .as_array()
            .expect("classes")
            .iter()
            .find(|class| class["name"] == "Cart")
            .expect("Cart im Diagramm");
        let members: Vec<&str> = cart["members"]
            .as_array()
            .expect("members")
            .iter()
            .map(|member| member["name"].as_str().unwrap_or_default())
            .collect();
        assert!(members.contains(&"checkout"), "erwartet die Methoden, bekam {members:?}");
    }

    #[test]
    fn a_sequence_diagram_keeps_the_confidence_on_every_step() {
        let (_root, _index, mut server) = fixture();
        let id = find(&mut server, "checkout");

        let diagram = server
            .call("sequence_diagram", json!({ "id": id, "depth": 2 }), &Value::Null)
            .expect("sd");
        let steps = diagram["sequence"].as_array().expect("sequence");
        assert!(!steps.is_empty(), "checkout ruft apply_discount auf");
        for step in steps {
            assert!(step["confidence"].is_string(), "step without confidence: {step}");
            assert!(step["evidence_path"].is_string(), "step without evidence: {step}");
            assert!(step["to_id"].is_string(), "eine 64-Bit-ID darf keine JSON-Zahl werden");
        }
    }

    #[test]
    fn a_diagram_for_an_unknown_node_is_an_error_not_an_empty_picture() {
        let (_root, _index, mut server) = fixture();
        let error = server
            .call("class_diagram", json!({ "id": "0000000000000000" }), &Value::Null)
            .unwrap_err();
        assert!(error.to_string().contains("nicht gefunden"));
    }

    #[test]
    fn an_unknown_method_is_an_error_rather_than_a_panic() {
        let (_root, _index, mut server) = fixture();
        let error = server.call("erfundene_methode", json!({}), &Value::Null).unwrap_err();
        assert!(error.to_string().contains("unbekannte Methode"));
    }
}
